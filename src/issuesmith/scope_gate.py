"""Reusable allow_paths scope measurement for issuesmith consumers (#4276).

Public API for resolving measurement roots, counting matched files/lines,
evaluating thresholds, and formatting P0 scope comments. Does not reference
workflow phase names, labels, comments, templates, Slack, or ``jobs/``
paths beyond optional metrics append.
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from issuesmith.config import IssuesmithConfig, ScopeGateConfig, get_config

__all__ = [
    "ScopeMeasure",
    "ScopeVerdict",
    "evaluate",
    "format_comment",
    "measure_scope",
    "override_from_metadata",
    "parse_allow_paths_from_ctx",
    "record_p0_trip_metric",
    "resolve_scope_root",
]


@dataclass(frozen=True)
class ScopeMeasure:
    files: int
    lines: int
    by_dir: dict[str, int]
    skipped_binary: int
    skipped_jsonl: int


@dataclass(frozen=True)
class ScopeVerdict:
    exceeded: bool
    reason: str
    measure: ScopeMeasure


def resolve_scope_root(metadata: dict, cfg: IssuesmithConfig) -> Path | None:
    """Measurement root for ``metadata['target_repo']``: nexus root, or
    ``paths.external_dir/<repo>`` for cross-repo (#3487).

    Single source of truth for "where does allow_paths get measured", shared by
    the CP1 breadth gate (``gate_rules.scope_breadth``), ``gate-preflight``, and
    the P0 step. Same directory layout ``context_hook.target_clone_path`` builds
    and ``worktree`` clones into, so every caller measures the same tree.
    Returns ``None`` when the target is cross-repo and no clone exists yet, or
    when the clone cannot be fast-forwarded to ``origin/<base_branch>`` (#4452)
    — callers must fail closed, never substitute a default.
    """
    target_repo = (metadata.get("target_repo") or "").strip()
    if not target_repo or target_repo == "sumipan/nexus":
        return cfg.root
    parts = target_repo.split("/", 1)
    if len(parts) != 2:
        return None
    external = cfg.paths.external_dir / parts[1]
    if not (external / ".git").exists():
        return None
    reason = _freshen_clone(external, metadata.get("base_branch") or "main")
    if reason is not None:
        print(f"scope_root: {target_repo} stale: {reason}", file=sys.stderr)
        return None
    return external


_FETCH_THROTTLE_SEC = 60
_GIT_TIMEOUT_SEC = 60


def _git_run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=_GIT_TIMEOUT_SEC,
    )


def _freshen_clone(root: Path, base: str) -> str | None:
    """Fast-forward the clone's checkout to ``origin/<base>`` (#4452).

    Returns ``None`` on success (or when the clone has no origin to follow),
    otherwise a one-line reason. Never resets, checks out, or touches dirty
    tracked files; untracked files (P0's ``worktrees/``) are ignored.
    """
    try:
        return _freshen_clone_inner(root, base)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"git failed: {type(exc).__name__}: {exc}"


def _freshen_clone_inner(root: Path, base: str) -> str | None:
    # --git-dir keeps git from walking up to a parent repo when .git is bogus.
    origin = _git_run(["--git-dir", str(root / ".git"), "config", "--get", "remote.origin.url"])
    if origin.returncode != 0:
        return None

    c = ["-C", str(root)]
    fetch_head = root / ".git" / "FETCH_HEAD"
    try:
        recent = time.time() - fetch_head.stat().st_mtime <= _FETCH_THROTTLE_SEC
    except OSError:
        recent = False
    fetch_err = ""
    if not recent:
        fetch = _git_run([*c, "fetch", "-q", "origin", base])
        if fetch.returncode != 0:
            last = (fetch.stderr or "").strip().splitlines()[-1:]
            fetch_err = f" (fetch failed: {last[0] if last else 'unknown error'})"

    def _heads() -> tuple[str, str | None]:
        head = _git_run([*c, "rev-parse", "HEAD"]).stdout.strip()
        upstream = _git_run([*c, "rev-parse", "--verify", "-q", f"origin/{base}"])
        return head, upstream.stdout.strip() if upstream.returncode == 0 else None

    head, upstream = _heads()
    if upstream is None:
        return f"origin/{base} not found{fetch_err}"
    if head == upstream:
        return None

    branch = _git_run([*c, "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if branch != base:
        return f"checked out {branch or '?'} instead of {base}{fetch_err}"
    status = _git_run([*c, "status", "--porcelain", "--untracked-files=no"])
    if status.returncode != 0 or status.stdout.strip():
        return f"tracked files modified{fetch_err}"

    merge = _git_run([*c, "merge", "--ff-only", "-q", f"origin/{base}"])
    head, upstream = _heads()
    if upstream is not None and head == upstream:
        return None
    if merge.returncode != 0:
        return f"cannot fast-forward to origin/{base}{fetch_err}"
    return f"HEAD differs from origin/{base}{fetch_err}"


def record_p0_trip_metric(cfg: IssuesmithConfig, issue_number: int) -> None:
    """Best-effort JSONL append: P0 stopped on a scope this CP1 should have caught."""
    record = {
        "event": "scope_gate.p0_trip",
        "issue_number": issue_number,
        "timestamp": time.time(),
    }
    try:
        with open(cfg.paths.metrics, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — metrics are best-effort, never fail the step
        pass


def _top_dir(path: str) -> str:
    parts = Path(path).parts
    if len(parts) <= 1:
        return "."
    return f"{parts[0]}/"


def _matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pat) for pat in patterns)


def _is_jsonl(path: str) -> bool:
    return path.endswith(".jsonl") or fnmatch.fnmatch(path, "*.jsonl")


def _numstat_binary(worktree_root: Path, rel: str) -> bool:
    """Return True when git treats the file as binary (numstat shows '-')."""
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(worktree_root),
            "diff",
            "--numstat",
            "--no-index",
            "--",
            "/dev/null",
            rel,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = (proc.stdout or "").strip()
    if not stdout:
        try:
            return b"\x00" in (worktree_root / rel).read_bytes()[:8192]
        except OSError:
            return True
    first = stdout.splitlines()[0]
    parts = first.split("\t")
    return len(parts) >= 2 and parts[0] == "-"


def _count_lines(worktree_root: Path, rel: str) -> int:
    path = worktree_root / rel
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0


def measure_scope(worktree_root: Path, allow_paths: list[str]) -> ScopeMeasure:
    """Count tracked files matching allow_paths under worktree_root."""
    if not allow_paths:
        return ScopeMeasure(files=0, lines=0, by_dir={}, skipped_binary=0, skipped_jsonl=0)

    proc = subprocess.run(
        ["git", "-C", str(worktree_root), "ls-files", "-z"],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return ScopeMeasure(files=0, lines=0, by_dir={}, skipped_binary=0, skipped_jsonl=0)

    tracked = [p for p in proc.stdout.decode("utf-8", errors="replace").split("\0") if p]
    matched = sorted({p for p in tracked if _matches(p, allow_paths)})

    lines = 0
    skipped_binary = 0
    skipped_jsonl = 0
    dir_counts: Counter[str] = Counter()

    for rel in matched:
        dir_counts[_top_dir(rel)] += 1
        if _is_jsonl(rel):
            skipped_jsonl += 1
            continue
        if _numstat_binary(worktree_root, rel):
            skipped_binary += 1
            continue
        lines += _count_lines(worktree_root, rel)

    by_dir = dict(dir_counts.most_common(5))
    return ScopeMeasure(
        files=len(matched),
        lines=lines,
        by_dir=by_dir,
        skipped_binary=skipped_binary,
        skipped_jsonl=skipped_jsonl,
    )


def evaluate(
    measure: ScopeMeasure,
    config: ScopeGateConfig,
    override: ScopeGateConfig | None = None,
) -> ScopeVerdict:
    """Compare measure against thresholds (override wins over config)."""
    effective = override if override is not None else config
    reasons: list[str] = []
    if measure.files > effective.max_files:
        reasons.append(f"files: {measure.files} > {effective.max_files}")
    if measure.lines > effective.max_lines:
        reasons.append(f"lines: {measure.lines} > {effective.max_lines}")
    if reasons:
        return ScopeVerdict(exceeded=True, reason="; ".join(reasons), measure=measure)
    return ScopeVerdict(exceeded=False, reason="", measure=measure)


def format_comment(verdict: ScopeVerdict, *, preflight_contradiction: bool = False) -> str:
    """Markdown Issue comment for SCOPE_TOO_LARGE (text from the language pack)."""
    lang = get_config().language
    m = verdict.measure
    rows = "\n".join(f"| `{d}` | {n} |" for d, n in m.by_dir.items()) or lang.message(
        "scope_gate.no_rows"
    )
    contradiction_note = (
        lang.message("scope_gate.preflight_contradiction") if preflight_contradiction else ""
    )
    body = lang.message(
        "scope_gate.too_large",
        contradiction_note=contradiction_note,
        reason=verdict.reason,
        files=m.files,
        lines=m.lines,
        skipped_binary=m.skipped_binary,
        skipped_jsonl=m.skipped_jsonl,
        rows=rows,
    )
    # Machine marker stays outside the pack so hosts cannot break parsers by translating it.
    return f"{body}\nPIPELINE_STATUS: SCOPE_TOO_LARGE\n"


# context_hook writes a parenthesised "no restriction" placeholder in full-width
# parentheses when the Issue has no allow_paths; no real path starts with one.
_FULLWIDTH_PARENS = (chr(0xFF08), chr(0xFF09))


def _is_unrestricted_placeholder(raw: str) -> bool:
    s = raw.strip()
    return "\n" not in s and s.startswith(_FULLWIDTH_PARENS[0]) and s.endswith(_FULLWIDTH_PARENS[1])


def parse_allow_paths_from_ctx(raw: str) -> list[str]:
    """Parse StepContext.allow_paths (`- path` lines or comma-separated)."""
    if not raw or not raw.strip() or _is_unrestricted_placeholder(raw):
        return []
    paths: list[str] = []
    for line in raw.replace(",", "\n").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("- "):
            s = s[2:].strip()
        s = s.strip('"').strip("'")
        if s:
            paths.append(s)
    return paths


def override_from_metadata(
    metadata: dict,
    base: ScopeGateConfig,
) -> ScopeGateConfig | None:
    """Build ScopeGateConfig override from Issue YAML `scope_gate` mapping."""
    raw = metadata.get("scope_gate")
    if not isinstance(raw, dict):
        return None
    return ScopeGateConfig(
        enabled=bool(raw["enabled"]) if "enabled" in raw else base.enabled,
        max_files=int(raw["max_files"]) if "max_files" in raw else base.max_files,
        max_lines=int(raw["max_lines"]) if "max_lines" in raw else base.max_lines,
        hard_max_files=(
            int(raw["hard_max_files"]) if "hard_max_files" in raw else base.hard_max_files
        ),
    )
