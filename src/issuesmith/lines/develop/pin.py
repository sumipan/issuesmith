"""Deterministic dependency pin bump step for the reference develop line."""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path

import tomllib
import yaml
from packaging.version import InvalidVersion, Version

from issuesmith.config import StepConfig, get_config
from issuesmith.contract import Andon, StepContext, StepResult
from issuesmith.forge import get_forge

_TITLE_RE = re.compile(
    r"^.+:\s+bump\s+(?P<package>[A-Za-z0-9_.-]+)\s+to\s+\S+$"
)
_DEP_RE = re.compile(
    r"^(?P<pkg>[A-Za-z0-9_.-]+)(?:\[[^\]]*\])?\s+@\s+"
    r"(?P<spec>git\+[^@]+)@(?P<tag>v?\d+\.\d+\.\d+)$"
)
_TAG_LINE_RE = re.compile(r"^([0-9a-f]{40}|.+\^?)\trefs/tags/(v?\d+\.\d+\.\d+)(\^\{\})?$")


def _worktree(ctx: StepContext) -> Path:
    raw = ctx.worktree_path.strip()
    path = Path(raw)
    if path.is_absolute():
        return path
    return get_config().root / path


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _andon(kind: str, rule_id: str, summary: str) -> StepResult:
    return StepResult(
        status="andon",
        andon=Andon(kind=kind, rule_id=rule_id, summary=summary),
    )


def _normalize_package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _reset_tracked_to_base(worktree: Path, base: str) -> str | None:
    fetch = _git(worktree, "fetch", "origin", f"refs/heads/{base}:refs/remotes/origin/{base}")
    if fetch.returncode != 0:
        return (fetch.stderr or fetch.stdout or "").strip() or "git fetch failed"
    reset = _git(worktree, "reset", "--hard", f"origin/{base}")
    if reset.returncode != 0:
        return (reset.stderr or reset.stdout or "").strip() or "git reset failed"
    return None


def _read_pyproject(worktree: Path) -> tuple[dict, str] | tuple[None, None]:
    path = worktree / "pyproject.toml"
    try:
        raw = path.read_bytes()
    except OSError:
        return None, None
    text = raw.decode("utf-8")
    try:
        return tomllib.loads(text), text
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, TypeError):
        try:
            return tomllib.loads(raw), text
        except (tomllib.TOMLDecodeError, UnicodeDecodeError, TypeError):
            return None, None


def _find_dependency_line(
    deps: list[str],
    package_norm: str,
) -> tuple[str | None, str | None, str | None]:
    """Return (line, pkg, tag) for exactly one matching git URL pin, else Nones."""
    matches: list[tuple[str, str, str]] = []
    for line in deps:
        if not isinstance(line, str):
            continue
        stripped = line.strip()
        m = _DEP_RE.match(stripped)
        if not m:
            continue
        pkg = m.group("pkg")
        if _normalize_package(pkg) != package_norm:
            continue
        matches.append((stripped, pkg, m.group("tag")))
    if len(matches) != 1:
        return None, None, None
    return matches[0]


def _repo_url_from_dep_line(line: str) -> str | None:
    m = _DEP_RE.match(line.strip())
    if not m:
        return None
    spec = m.group("spec")
    if not spec.startswith("git+"):
        return None
    return spec[4:]


def _ls_remote_tags(repo_url: str) -> tuple[list[tuple[str, Version]], str | None]:
    proc = subprocess.run(
        ["git", "ls-remote", "--tags", repo_url],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return [], detail or "git ls-remote failed"
    by_version: dict[Version, list[str]] = {}
    for line in (proc.stdout or "").splitlines():
        m = _TAG_LINE_RE.match(line.strip())
        if not m:
            continue
        tag_raw = m.group(2)
        if tag_raw.endswith("^{}"):
            continue
        ver_str = tag_raw[1:] if tag_raw.startswith("v") else tag_raw
        try:
            ver = Version(ver_str)
        except InvalidVersion:
            continue
        if ver.pre is not None or ver.post is not None or ver.dev is not None:
            continue
        if ver.local is not None:
            continue
        by_version.setdefault(ver, []).append(tag_raw)
    if not by_version:
        return [], None
    latest_ver = max(by_version)
    tags = sorted(by_version[latest_ver])
    chosen = tags[-1]
    return [(chosen, latest_ver)], None


def _replace_tag_in_line(line: str, new_tag: str) -> str:
    prefix, _old = line.rsplit("@", 1)
    return f"{prefix}@{new_tag}"


def _language_pack_path(worktree: Path) -> Path | None:
    cfg_path = worktree / "issuesmith.yaml"
    if not cfg_path.is_file():
        return None
    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    pack = data.get("language_pack")
    if not isinstance(pack, str) or not pack.strip():
        return None
    rel = Path(pack.strip())
    if rel.is_absolute():
        return rel
    return worktree / rel


def _language_pack_fields_from_upstream(
    repo_url: str,
    tag: str,
    *,
    scratch: Path,
) -> tuple[dict[str, object] | None, str | None]:
    repo = scratch / "bare.git"
    if repo.exists():
        shutil.rmtree(repo)
    repo.mkdir(parents=True)
    init = subprocess.run(["git", "init", "--bare", str(repo)], capture_output=True, text=True)
    if init.returncode != 0:
        return None, "bare init failed"
    fetch = subprocess.run(
        ["git", "--git-dir", str(repo), "fetch", "--depth", "1", repo_url, f"refs/tags/{tag}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if fetch.returncode != 0:
        detail = (fetch.stderr or fetch.stdout or "").strip()
        return None, detail or "fetch language source failed"
    show = subprocess.run(
        ["git", "--git-dir", str(repo), "show", "FETCH_HEAD:src/issuesmith/language.py"],
        capture_output=True,
        text=True,
        check=False,
    )
    if show.returncode != 0:
        detail = (show.stderr or show.stdout or "").strip()
        return None, detail or "git show language.py failed"
    try:
        tree = ast.parse(show.stdout)
    except SyntaxError as exc:
        return None, str(exc)
    fields: list[str] = []
    en_values: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "LanguagePack":
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    fields.append(item.target.id)
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "EN":
                    if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
                        if node.value.func.id == "LanguagePack":
                            for kw in node.value.keywords:
                                if kw.arg:
                                    try:
                                        en_values[kw.arg] = ast.literal_eval(kw.value)
                                    except (ValueError, SyntaxError):
                                        return None, f"non-literal EN field {kw.arg!r}"
    if not fields:
        return None, "LanguagePack fields not found"
    ordered: dict[str, object] = {}
    for name in fields:
        if name not in en_values:
            return None, f"missing EN field {name!r}"
        ordered[name] = en_values[name]
    return ordered, None


def _top_level_block(text: str, key: str) -> str | None:
    pat = re.compile(
        rf"(?ms)^({re.escape(key)}:.*?)(?=^[a-zA-Z_][a-zA-Z0-9_]*:|\Z)",
    )
    m = pat.search(text)
    if not m:
        return None
    return m.group(1).rstrip() + "\n"


def _sync_language_pack_file(
    pack_path: Path,
    en_fields: dict[str, object],
    tag: str,
) -> str | None:
    if pack_path.is_file():
        existing_text = pack_path.read_text(encoding="utf-8")
        existing = yaml.safe_load(existing_text) or {}
    else:
        existing_text = ""
        existing = {}
    if not isinstance(existing, dict):
        return "language pack root must be a mapping"
    new_keys = [k for k in en_fields if k not in existing]
    removed_keys = [k for k in existing if k not in en_fields]
    if not new_keys and not removed_keys:
        return None
    out_parts: list[str] = []
    for key in en_fields:
        if key in existing and existing_text.strip():
            block = _top_level_block(existing_text, key)
            if block:
                out_parts.append(block)
                continue
        if key not in new_keys:
            continue
        value = en_fields[key]
        try:
            ast.literal_eval(repr(value))
        except (ValueError, SyntaxError):
            return "non-literal default"
        out_parts.append(f"# English default (added in issuesmith {tag})\n")
        out_parts.append(yaml.safe_dump({key: value}, allow_unicode=True))
    pack_path.parent.mkdir(parents=True, exist_ok=True)
    pack_path.write_text("".join(out_parts), encoding="utf-8")
    loaded = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return "language pack invalid after edit"
    if tuple(loaded.keys()) != tuple(en_fields.keys()):
        return "language pack key order mismatch"
    return None


def run(ctx: StepContext, step: StepConfig) -> StepResult:
    _ = step
    worktree = _worktree(ctx)
    base = ctx.base_branch.strip() or "main"
    issue_client = get_forge(repo=ctx.issue_repo.strip() or get_config().repo)
    try:
        issue = issue_client.issue_get(int(ctx.issue_number), fields=["title"])
    except Exception as exc:  # noqa: BLE001
        return _andon("broken", "pin.git_failed", f"issue_get failed: {exc}")
    title = str(issue.get("title") or "")
    m_title = _TITLE_RE.match(title)
    if not m_title:
        return _andon("decision", "pin.title_format", f"title does not match bump pattern: {title!r}")
    package_raw = m_title.group("package")
    package_norm = _normalize_package(package_raw)

    err = _reset_tracked_to_base(worktree, base)
    if err:
        return _andon("broken", "pin.git_failed", err)

    parsed, text = _read_pyproject(worktree)
    if parsed is None or text is None:
        return _andon("broken", "pin.invalid_pyproject", "pyproject.toml is not valid TOML")

    project = parsed.get("project")
    if not isinstance(project, dict):
        return _andon("decision", "pin.unsupported_requirement", "missing project table")
    deps = project.get("dependencies")
    if not isinstance(deps, list):
        return _andon("decision", "pin.unsupported_requirement", "project.dependencies missing")

    dep_line, _pkg, current_tag = _find_dependency_line(deps, package_norm)
    if dep_line is None:
        return _andon(
            "decision",
            "pin.unsupported_requirement",
            f"no unique git URL pin for {package_raw!r} in project.dependencies",
        )

    repo_url = _repo_url_from_dep_line(dep_line)
    if repo_url is None:
        return _andon("decision", "pin.unsupported_requirement", "dependency is not a git+ URL pin")

    tags, ls_err = _ls_remote_tags(repo_url)
    if ls_err or not tags:
        _reset_tracked_to_base(worktree, base)
        detail = ls_err or "no stable semver tags"
        return _andon("broken", "pin.tags_fetch_failed", detail)

    chosen_tag, latest_ver = tags[0]
    try:
        current_ver = Version(current_tag[1:] if current_tag.startswith("v") else current_tag)
    except InvalidVersion:
        return _andon("decision", "pin.unsupported_requirement", f"invalid current tag {current_tag!r}")

    if current_ver >= latest_ver:
        return StepResult(status="done", markers=["PIN_CURRENT"])

    new_line = _replace_tag_in_line(dep_line, chosen_tag)
    new_text = text.replace(dep_line, new_line, 1)
    (worktree / "pyproject.toml").write_text(new_text, encoding="utf-8")

    paths_to_add = ["pyproject.toml"]
    if package_norm == "issuesmith":
        pack_path = _language_pack_path(worktree)
        if pack_path is not None:
            scratch = worktree / ".issuesmith-pin-fetch"
            scratch.mkdir(exist_ok=True)
            fields, lang_err = _language_pack_fields_from_upstream(
                repo_url,
                chosen_tag,
                scratch=scratch,
            )
            if fields is None:
                _reset_tracked_to_base(worktree, base)
                if lang_err and "non-literal" in lang_err:
                    return _andon(
                        "decision",
                        "pin.language_pack_default",
                        lang_err,
                    )
                return _andon(
                    "broken",
                    "pin.language_source_failed",
                    lang_err or "language source failed",
                )
            sync_err = _sync_language_pack_file(pack_path, fields, chosen_tag)
            if sync_err == "non-literal default":
                _reset_tracked_to_base(worktree, base)
                return _andon(
                    "decision",
                    "pin.language_pack_default",
                    "language pack default not literal",
                )
            if sync_err == "language pack key order mismatch":
                _reset_tracked_to_base(worktree, base)
                return _andon("broken", "pin.language_pack_invalid", sync_err)
            if sync_err:
                _reset_tracked_to_base(worktree, base)
                return _andon("broken", "pin.language_pack_invalid", sync_err)
            if pack_path.is_relative_to(worktree):
                paths_to_add.append(str(pack_path.relative_to(worktree)))
            else:
                paths_to_add.append(str(pack_path))

    add = _git(worktree, "add", "--", *paths_to_add)
    if add.returncode != 0:
        _reset_tracked_to_base(worktree, base)
        return _andon("broken", "pin.git_failed", (add.stderr or "").strip() or "git add failed")
    commit = _git(
        worktree,
        "commit",
        "-m",
        f"chore: bump {package_raw} to {chosen_tag}",
    )
    if commit.returncode != 0:
        _reset_tracked_to_base(worktree, base)
        return _andon("broken", "pin.git_failed", (commit.stderr or "").strip() or "git commit failed")
    return StepResult(status="done", markers=["PIN_DONE"])
