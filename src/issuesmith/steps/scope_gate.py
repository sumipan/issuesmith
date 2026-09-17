"""P0 allow_paths scope gate — measure / evaluate / comment (#3349)."""

from __future__ import annotations

import fnmatch
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from issuesmith.config import ScopeGateConfig


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
        # Fallback: NUL byte in the first chunk.
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


def format_comment(verdict: ScopeVerdict) -> str:
    """Markdown Issue comment for SCOPE_TOO_LARGE."""
    m = verdict.measure
    rows = "\n".join(f"| `{d}` | {n} |" for d, n in m.by_dir.items()) or "| (none) | 0 |"
    return (
        "## P0 中断: allow_paths のスコープが大きすぎます\n"
        "\n"
        f"**理由**: `{verdict.reason}`\n"
        "\n"
        "| 指標 | 値 |\n"
        "|---|---|\n"
        f"| ファイル数 | {m.files} |\n"
        f"| 行数（テキスト） | {m.lines} |\n"
        f"| バイナリ（行数除外） | {m.skipped_binary} |\n"
        f"| `*.jsonl`（行数除外） | {m.skipped_jsonl} |\n"
        "\n"
        "### ディレクトリ別ファイル数（上位 5）\n"
        "\n"
        "| ディレクトリ | ファイル数 |\n"
        "|---|---|\n"
        f"{rows}\n"
        "\n"
        "### 分割ヒント\n"
        "\n"
        "Issue をディレクトリ単位（上表の上位エントリ）や機能単位に分割し、"
        "各子 Issue の `allow_paths` を狭めてから "
        "`issuesmith:scope-too-large` を外して `issuesmith:develop-ready` を付与してください。\n"
        "\n"
        "PIPELINE_STATUS: SCOPE_TOO_LARGE\n"
    )


def parse_allow_paths_from_ctx(raw: str) -> list[str]:
    """Parse StepContext.allow_paths (`- path` lines or comma-separated)."""
    if not raw or raw.strip() in {"", "（制限なし）"}:
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
