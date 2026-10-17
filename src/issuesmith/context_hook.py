"""
context_hook.py — ghdag context_hook for issuesmith

Invoked through the context_hook feature of ghdag WorkflowDispatcher.
Builds host-companion context variables from a GitHub Issue body and prints them as JSON on stdout.

Invocation:
    python -m issuesmith.context_hook <issue_number>

Output (JSON):
    {
        "pipeline_id": "issue-42-a1b2c3d4",
        "base_branch": "main",
        "allow_paths": "- src/**\n- tests/**",
        "branch": "feat/issue-42-a1b2c3d4",
        "worktree_path": "/path/to/nexus/.claude/worktrees/issue-42-a1b2c3d4",
        ...
    }
"""

from __future__ import annotations

import functools
import json
import logging
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

import yaml

from issuesmith.branch_reuse import find_reusable_branch
from issuesmith.branch_reuse import is_base_recorded as _is_base_recorded
from issuesmith.branch_reuse import previous_commits as _prev_commits
from issuesmith.config import get_config
from issuesmith.targets import companion_allow_paths_raw, targets_from_issue

_PIPELINE_BRANCH_RE = re.compile(
    r"<!--\s*pipeline-branch:\s*feat/issue-(\d+)-([a-f0-9]+)\s*-->",
    re.IGNORECASE,
)

_BASE_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_ALLOW_PATH_ELEMENT_RE = re.compile(r"^[A-Za-z0-9_./*\[\]!\-]+$")
_SOURCE_RE = re.compile(r"^[A-Za-z0-9._/-]+$")

_LANG_EN = Path(__file__).resolve().parent / "lang" / "en.yaml"


class ContextHookError(ValueError):
    """Issue YAML metadata failed security validation before template expansion."""


@functools.lru_cache
def _context_hook_messages() -> dict[str, str]:
    try:
        raw = yaml.safe_load(_LANG_EN.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ContextHookError(f"cannot read language pack {_LANG_EN}: {exc}") from exc
    messages = raw.get("messages", raw)
    if not isinstance(messages, dict):
        raise ContextHookError(f"invalid language pack {_LANG_EN}: messages must be a mapping")
    return {str(k): str(v) for k, v in messages.items()}


def _hook_message(key: str, /, **kwargs: object) -> str:
    template = _context_hook_messages()[key]
    return template.format(**kwargs)

_cfg = get_config()
SUPPORTED_REPOS: set[str] = set(_cfg.supported_repos)
_REPO_ROOT = str(_cfg.root)
try:
    _WORKTREES_REL = str(_cfg.paths.worktrees_dir.relative_to(_cfg.root))
except ValueError:
    _WORKTREES_REL = str(_cfg.paths.worktrees_dir)
try:
    _EXTERNAL_REL = str(_cfg.paths.external_dir.relative_to(_cfg.root))
except ValueError:
    _EXTERNAL_REL = str(_cfg.paths.external_dir)


# Phrases in the out-of-scope section that mean host-side change (matched lowercased).
_HOST_AVOIDANCE_PATTERNS: tuple[str, ...] = (
    "host-side",
    "host side",
    "update host",
    "host changes",
)


def _companion_worktree_path(pipeline_id: str) -> str:
    """Absolute path for the host companion worktree."""
    return str(Path(_REPO_ROOT) / _WORKTREES_REL / f"{pipeline_id}-host")


@dataclass
class MetadataViolation:
    field: str
    code: str
    message: str


def _validate_base_branch(value: object) -> MetadataViolation | None:
    text = str(value) if value is not None else "main"
    if ".." in text:
        reason = "must not contain '..'"
    elif not _BASE_BRANCH_RE.fullmatch(text):
        reason = (
            "must start with a letter or digit, be at most 200 characters,"
            " and contain only letters, digits, and . _ / -"
        )
    else:
        return None
    return MetadataViolation(
        field="base_branch",
        code="invalid_base_branch",
        message=_hook_message(
            "context_hook.invalid_base_branch",
            value=text,
            reason=reason,
        ),
    )


def _validate_source(value: object) -> MetadataViolation | None:
    if value is None or value == "":
        return None
    text = str(value)
    if _SOURCE_RE.fullmatch(text):
        return None
    return MetadataViolation(
        field="source",
        code="invalid_source",
        message=_hook_message("context_hook.invalid_source", value=text),
    )


def _validate_allow_path_element(index: int, path: object) -> MetadataViolation | None:
    text = str(path)
    if "(" in text and ")" in text:
        return MetadataViolation(
            field=f"allow_paths[{index}]",
            code="annotation_in_path",
            message=f"allow_paths[{index}] contains a parenthesized annotation: {text!r}",
        )
    if text.startswith("/var/tmp/"):
        return MetadataViolation(
            field=f"allow_paths[{index}]",
            code="invalid_path_format",
            message=(
                f"allow_paths[{index}] starts with /var/tmp/"
                " (install directories are not allowed)"
            ),
        )
    if not _ALLOW_PATH_ELEMENT_RE.fullmatch(text):
        return MetadataViolation(
            field=f"allow_paths[{index}]",
            code="invalid_allow_path",
            message=_hook_message(
                "context_hook.invalid_allow_path",
                index=index,
                value=text,
                reason=(
                    "must contain only letters, digits, and _ . / * [ ] ! -"
                    " (no spaces, quotes, or shell metacharacters)"
                ),
            ),
        )
    return None


def _allow_paths_list(metadata: dict) -> list:
    allow_paths_raw = metadata.get("allow_paths", [])
    if isinstance(allow_paths_raw, str):
        return [allow_paths_raw]
    if isinstance(allow_paths_raw, list):
        return allow_paths_raw
    return []


def validate_context_hook_security(
    metadata: dict,
    *,
    enforce_empty_allow_paths: bool = True,
) -> list[MetadataViolation]:
    """Regex / injection checks for fields expanded into shell templates (#5369)."""
    violations: list[MetadataViolation] = []

    bb_v = _validate_base_branch(metadata.get("base_branch", "main"))
    if bb_v:
        violations.append(bb_v)

    src_v = _validate_source(metadata.get("source"))
    if src_v:
        violations.append(src_v)

    allow_paths_raw = _allow_paths_list(metadata)
    target_repo = metadata.get("target_repo")
    from ghdag.github_client import DEFAULT_REPO

    issue_repo = str(metadata.get("issue_repo", DEFAULT_REPO))
    cross_repo_target = bool(
        target_repo
        and target_repo in SUPPORTED_REPOS
        and target_repo != issue_repo
    )
    if (
        enforce_empty_allow_paths
        and cross_repo_target
        and not allow_paths_raw
        and "scope_gate" not in metadata
    ):
        violations.append(MetadataViolation(
            field="allow_paths",
            code="empty_allow_paths",
            message=_hook_message("context_hook.empty_allow_paths"),
        ))

    for i, path in enumerate(allow_paths_raw):
        el_v = _validate_allow_path_element(i, path)
        if el_v:
            violations.append(el_v)

    return violations


def validate_issue_metadata(
    metadata: dict,
    *,
    enforce_empty_allow_paths: bool = True,
) -> list[MetadataViolation]:
    """Semantic validation of already-parsed YAML metadata.

    Kept separate from parse_issue_metadata() and called after the syntax parse,
    so the existing ValueError catch path of build_context() stays intact.
    """
    violations = validate_context_hook_security(
        metadata,
        enforce_empty_allow_paths=enforce_empty_allow_paths,
    )

    target_repo = metadata.get("target_repo", None)
    if not target_repo:
        violations.append(MetadataViolation(
            field="target_repo",
            code="missing_required",
            message="target_repo is required for every issue (single-repo issues included)",
        ))
    elif target_repo not in SUPPORTED_REPOS:
        violations.append(MetadataViolation(
            field="target_repo",
            code="unsupported_repo",
            message=(
                f"target_repo '{target_repo}' is not supported."
                f" Supported repositories: {sorted(SUPPORTED_REPOS)}"
            ),
        ))

    return violations


def raise_metadata_violations(
    violations: list[MetadataViolation],
    *,
    error_type: type[Exception] = ContextHookError,
) -> None:
    if violations:
        raise error_type(violations[0].message)


def parse_issue_metadata(body: str) -> dict:
    """Extract and return the YAML metadata from the leading ```yaml ... ``` block of an Issue body.

    "Leading" means the first code fence (```xxx ... ```) in the Issue body. To avoid
    picking up a yaml block in the middle of the text (e.g. a `schedule.yaml example`),
    the metadata is read only when the first code block starts with ```yaml.

    Raises:
        ValueError: no code block / first block is not yaml / YAML is empty /
            the parse result is not a dict (list / scalar etc.)
    """
    m = re.search(r"^```(\w*)\n(.*?)\n```", body, re.DOTALL | re.MULTILINE)
    if not m:
        raise ValueError("Issue body has no code block")
    lang, raw = m.group(1), m.group(2)
    if lang != "yaml":
        raise ValueError(
            f"leading code block of the Issue body is not yaml (lang={lang!r})"
        )
    result = yaml.safe_load(raw)
    if result is None:
        raise ValueError("YAML metadata block of the Issue body is empty")
    if not isinstance(result, dict):
        raise ValueError(
            f"YAML metadata of the Issue body must be a dict (got {type(result).__name__})"
        )
    return result


def parse_issue_metadata_blocks(body: str) -> list[dict]:
    """Parse every ```yaml ... ``` block in the Issue body, in order, and return them as a list of dicts.

    A milestone parent that spans repositories carries one metadata block per repository (nexus #4076).
    Blocks that fail to parse, are empty, or are not a dict are skipped. No exception is raised.
    The existing `parse_issue_metadata` (first block only) is unchanged.
    """
    blocks: list[dict] = []
    for m in re.finditer(r"^```yaml\n(.*?)\n```", body or "", re.DOTALL | re.MULTILINE):
        try:
            result = yaml.safe_load(m.group(1))
        except yaml.YAMLError:
            continue
        if isinstance(result, dict):
            blocks.append(result)
    return blocks


# When the ghdag dispatcher starts this as a subprocess, the parent process
# (ghdag_runner etc.) has not sourced .env, so GITHUB_TOKEN etc. are not visible.
# Load it ourselves. find_dotenv walks up from __file__, so the parent nexus repo's
# .env is found even when started from a worktree.
# (override=False is the default, so explicitly exported values are not overwritten)
try:
    from dotenv import find_dotenv, load_dotenv
    _env_path = find_dotenv(usecwd=False)
    if _env_path:
        load_dotenv(_env_path)
except ImportError:
    pass


def _fetch_issue_body_from_api(issue_number: int) -> str | None:
    """Fetch the latest Issue body via the GitHub API. Return None on failure."""
    try:
        from ghdag.forge import get_forge

        data = get_forge().issue_get(issue_number, fields=["body"])
        body = data.get("body")
        return body.strip() if isinstance(body, str) and body.strip() else None
    except Exception:
        return None


# Compatibility name patched by tests/tools/issuesmith/conftest.py
_fetch_issue_body_from_gh = _fetch_issue_body_from_api


def _comment_author_login(comment: dict) -> str:
    author = comment.get("author") if isinstance(comment, dict) else None
    if isinstance(author, str):
        # ghdag GitHubClient.get_issue_comments() flattens author to a login string
        return author
    if isinstance(author, dict):
        login = author.get("login")
        return str(login) if login else ""
    user = comment.get("user") if isinstance(comment, dict) else None
    if isinstance(user, dict):
        login = user.get("login")
        return str(login) if login else ""
    return ""


def _fetch_issue_author_login(issue_number: int, issue_repo: str) -> str | None:
    try:
        from ghdag.forge import get_forge

        data = get_forge(repo=issue_repo).issue_get(issue_number, fields=["user"])
        user = data.get("user")
        if isinstance(user, dict):
            login = user.get("login")
            return str(login) if login else None
    except Exception:
        return None
    return None


def _github_token_login() -> str | None:
    try:
        from ghdag.forge import get_forge

        from issuesmith.forge_api import api_request

        data = api_request(get_forge(), "/user")
        if isinstance(data, dict):
            login = data.get("login")
            return str(login) if login else None
    except Exception:
        return None
    return None


def _pipeline_id_from_comments(
    issue_number: int,
    comments: list[dict],
    *,
    issue_author_login: str | None,
    token_login: str | None,
    enforce_author: bool,
) -> str | None:
    """Restore pipeline_id from pipeline-branch comments. The last match wins."""
    trusted: set[str] = set()
    if issue_author_login:
        trusted.add(issue_author_login)
    if token_login:
        trusted.add(token_login)

    found: str | None = None
    for comment in comments:
        if enforce_author and trusted:
            login = _comment_author_login(comment)
            if login not in trusted:
                continue
        body = comment.get("body", "") if isinstance(comment, dict) else ""
        for match in _PIPELINE_BRANCH_RE.finditer(body):
            num = int(match.group(1))
            shortid = match.group(2)
            if num == issue_number:
                found = f"issue-{num}-{shortid}"
    return found


def _fetch_issue_comments_from_api(issue_number: int, issue_repo: str) -> list[dict]:
    """Fetch every Issue comment via the GitHub API. Empty list on failure."""
    try:
        from ghdag.forge import get_forge
        return get_forge(repo=issue_repo).get_issue_comments(issue_number)
    except Exception:
        pass
    try:
        from ghdag.forge import get_forge
        data = get_forge(repo=issue_repo).issue_get(issue_number, fields=["comments"])
        comments = data.get("comments")
        return comments if isinstance(comments, list) else []
    except Exception:
        return []


def build_context(
    issue_number: int,
    *,
    body: str | None = None,
    queue_dir: str | None = None,
) -> dict[str, str]:
    """Build the ghdag template context from an Issue number.

    Fetches the Issue body via the GitHub API, extracts the YAML metadata and builds the context.

    Args:
        issue_number: GitHub Issue number
        body: Issue body string (fetched from the GitHub API when omitted; for tests)
        queue_dir: directory to look for design files in (for tests; no GitHub API call when set)

    Returns:
        context dict injected into ghdag (all values are str)
    """
    caller_supplied_body = body is not None

    if body is None and queue_dir is not None:
        import pathlib
        design_file = pathlib.Path(queue_dir) / f"issue-{issue_number}-design.md"
        if design_file.exists():
            body = design_file.read_text(encoding="utf-8")
        else:
            body = ""

    if body is None:
        body = _fetch_issue_body_from_gh(issue_number)
        if body is None:
            print(
                f"Error: failed to fetch the body of Issue #{issue_number} via the GitHub API.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Extract the YAML metadata
    metadata: dict = {}
    metadata_parsed = False
    if body:
        try:
            metadata = parse_issue_metadata(body)
            metadata_parsed = True
        except (ValueError, Exception) as exc:
            logging.warning("Issue #%s: YAML metadata parse failed: %s", issue_number, exc)
            metadata = {}

    from ghdag.github_client import DEFAULT_REPO

    if metadata_parsed:
        raise_metadata_violations(
            validate_context_hook_security(
                metadata,
                enforce_empty_allow_paths=not caller_supplied_body,
            )
        )

    issue_repo = str(metadata.get("issue_repo", DEFAULT_REPO))
    base_branch = str(metadata.get("base_branch", "main"))

    # Resolve target_repo / cross-repo before pipeline_id (branch_reuse searches there)
    target_repo = str(metadata.get("target_repo", ""))
    if target_repo and target_repo == issue_repo:
        # target_repo equal to issue_repo itself (e.g. sumipan/nexus) is not cross-repo.
        # On the external clone/worktree path the M2 acceptance gate checks the contract
        # against a feature worktree that disappears after merge and yields false negatives
        # (#2567). Normalize to the native path.
        target_repo = ""
    if target_repo and target_repo not in SUPPORTED_REPOS:
        raise ValueError(
            f"target_repo '{target_repo}' is not supported."
            f" Supported repositories: {sorted(SUPPORTED_REPOS)}"
        )
    if target_repo:
        repo_name = target_repo.split("/")[-1]
        target_clone_path = f"{_EXTERNAL_REL}/{repo_name}"
        target_worktree_path_rel = f"{_EXTERNAL_REL}/{repo_name}/worktrees"
        is_cross_repo = "true"
        _search_repo_dir = Path(_REPO_ROOT) / target_clone_path
    else:
        repo_name = ""
        target_clone_path = ""
        target_worktree_path_rel = ""
        is_cross_repo = "false"
        _search_repo_dir = Path(_REPO_ROOT)

    # pipeline_id: 1) restore from pipeline-branch comment 2) reuse a branch 3) new uuid
    comments = _fetch_issue_comments_from_api(issue_number, issue_repo)
    issue_author_login = (
        None if caller_supplied_body else _fetch_issue_author_login(issue_number, issue_repo)
    )
    token_login = None if caller_supplied_body else _github_token_login()
    restored_pipeline_id = _pipeline_id_from_comments(
        issue_number,
        comments,
        issue_author_login=issue_author_login,
        token_login=token_login,
        enforce_author=not caller_supplied_body,
    )
    if restored_pipeline_id:
        pipeline_id = restored_pipeline_id
        previous_commits_str = ""
        reuse_source = "comment"
    else:
        reusable = find_reusable_branch(_search_repo_dir, issue_number, base_branch)
        if reusable:
            pipeline_id = reusable[len("feat/"):]
            commits = _prev_commits(_search_repo_dir, reusable, base_branch)
            previous_commits_str = "\n".join(commits)
            reuse_source = "recorded" if _is_base_recorded(_search_repo_dir, reusable) else "unrecorded"
        else:
            shortid = str(uuid.uuid4())[:8]
            pipeline_id = f"issue-{issue_number}-{shortid}"
            previous_commits_str = ""
            reuse_source = "none"

    worktree_path = f"{_REPO_ROOT}/{_WORKTREES_REL}/{pipeline_id}"
    branch = f"feat/{pipeline_id}"

    if target_repo:
        target_worktree_path = f"{target_worktree_path_rel}/{pipeline_id}"
        worktree_path = target_worktree_path
    else:
        target_worktree_path = ""

    allow_paths_raw = metadata.get("allow_paths", [])
    if isinstance(allow_paths_raw, str):
        allow_paths_raw = [allow_paths_raw]
    if allow_paths_raw:
        allow_paths = "\n".join(f"- {p}" for p in allow_paths_raw)
    else:
        # The "unrestricted" sentinel is owned by ops.publish (also matched by scope_gate
        # and order templates); it is translated together with ops, not here (#4471).
        # Imported lazily because ops.publish imports this module.
        from issuesmith.ops import publish as _publish

        allow_paths = _publish._UNRESTRICTED_ALLOW_PATHS

    source = str(metadata.get("source", ""))

    companion_allow_raw = companion_allow_paths_raw(metadata)

    has_host_changes = "true" if (target_repo and companion_allow_raw) else "false"

    if has_host_changes == "true":
        host_allow_paths = "\n".join(f"- {p}" for p in companion_allow_raw)
        host_worktree_path = _companion_worktree_path(pipeline_id)
    else:
        host_allow_paths = ""
        host_worktree_path = ""

    # Lint warning: target_repo set + host_allow_paths unset + host mentioned out of scope
    if target_repo and not companion_allow_raw and body:
        out_of_scope = get_config().language.out_of_scope_heading
        nodo_match = re.search(
            rf"## {re.escape(out_of_scope)}(.*?)(?=\n## |\Z)", body, re.DOTALL
        )
        if nodo_match:
            nodo_text = nodo_match.group(1).lower()
            if any(p in nodo_text for p in _HOST_AVOIDANCE_PATTERNS):
                print(
                    f"Warning: Issue #{issue_number}: '{out_of_scope}' mentions host-side"
                    " changes but host_allow_paths is not set."
                    " Consider setting host_allow_paths during the B1 brush-up.",
                    file=sys.stderr,
                )

    targets = targets_from_issue(metadata, issue_repo=issue_repo, body=body)
    targets_json = json.dumps(
        [
            {
                "repo": t.repo,
                "base": t.base,
                "allow_paths": list(t.allow_paths),
                "primary": t.primary,
            }
            for t in targets
        ],
        ensure_ascii=False,
    )

    return {
        "pipeline_id": pipeline_id,
        "worktree_path": worktree_path,
        "branch": branch,
        "base_branch": base_branch,
        "allow_paths": allow_paths,
        "source": source,
        "issue_repo": issue_repo,
        "target_repo": target_repo,
        "repo_name": repo_name,
        "target_clone_path": target_clone_path,
        "target_worktree_path": target_worktree_path,
        "is_cross_repo": is_cross_repo,
        "has_host_changes": has_host_changes,
        "host_worktree_path": host_worktree_path,
        "host_allow_paths": host_allow_paths,
        "targets_json": targets_json,
        "previous_commits": previous_commits_str,
        "reuse_source": reuse_source,
    }


def main() -> None:
    """CLI entry point: python -m issuesmith.context_hook <issue_number>

    stdout: JSON (ghdag context_hook protocol)
    stderr: error messages
    exit code: 0=success, 1=argument error / API error
    """
    if len(sys.argv) < 2:
        print(
            "Usage: python -m issuesmith.context_hook <issue_number>",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        issue_number = int(sys.argv[1])
    except ValueError:
        print(
            f"Error: issue_number must be an integer, got: {sys.argv[1]!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        context = build_context(issue_number)
    except ContextHookError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(context, ensure_ascii=False))


if __name__ == "__main__":
    main()
