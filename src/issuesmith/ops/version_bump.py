#!/usr/bin/env python3
"""Deterministic pyproject.toml version bump for issuesmith publish (#2766).

Y if any of A1–A3 / B1–B4 match; otherwise Z. Never touches the X (major) digit.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_PUBLIC_DEF = re.compile(r"^([+-])((?:async\s+)?def)\s+([A-Za-z][A-Za-z0-9_]*)\s*\((.*)$")
_PUBLIC_CLASS = re.compile(r"^([+-])class\s+([A-Za-z][A-Za-z0-9_]*)\b(.*)$")
_ALL_LINE = re.compile(r"""^([+-])__all__\s*=\s*\[(.*)\]\s*$""")
_BREAKING_MSG = re.compile(r"(breaking|BREAKING|feat!:|!:)")
_ENTRY_SECTION = re.compile(
    r"^\[project\.(?:scripts|entry-points(?:\.[^\]]+)?)\]\s*$"
)
_ENTRY_KEY = re.compile(r"^([+-])\s*([A-Za-z0-9_.-]+)\s*=")
_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


@dataclass(frozen=True)
class BumpDecision:
    bump_type: str  # "Y" | "Z"
    reason_code: str
    reason: str


def _run_git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _parse_all_names(inner: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"""['"]([^'"]+)['"]""", inner)}


def _iter_src_diff_lines(diff: str) -> list[tuple[str, str]]:
    """Yield (path, line) for lines inside src/**/*.py file hunks."""
    path: str | None = None
    in_src_py = False
    out: list[tuple[str, str]] = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path = None
            in_src_py = False
            m = re.search(r" b/(.+)$", line)
            if m:
                path = m.group(1)
                in_src_py = path.startswith("src/") and path.endswith(".py")
            continue
        if line.startswith("+++ b/"):
            path = line[6:]
            in_src_py = path.startswith("src/") and path.endswith(".py")
            continue
        if not in_src_py or path is None:
            continue
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        out.append((path, line))
    return out


def _check_a1_a2_b2_b3(diff: str) -> BumpDecision | None:
    added_defs: dict[str, str] = {}
    removed_defs: dict[str, str] = {}
    added_classes: set[str] = set()
    removed_classes: set[str] = set()
    all_removed: set[str] = set()
    all_added: set[str] = set()

    for path, line in _iter_src_diff_lines(diff):
        m_all = _ALL_LINE.match(line)
        if m_all:
            names = _parse_all_names(m_all.group(2))
            if m_all.group(1) == "+":
                all_added |= names
            else:
                all_removed |= names
            continue

        m_def = _PUBLIC_DEF.match(line)
        if m_def:
            sign, _kind, name, rest = m_def.groups()
            # rest is args starting after '('; strip trailing ':' etc.
            args = rest.rsplit(")", 1)[0] if ")" in rest else rest
            key = f"{path}::{name}"
            if sign == "+":
                added_defs[key] = args
            else:
                removed_defs[key] = args
            continue

        m_cls = _PUBLIC_CLASS.match(line)
        if m_cls:
            sign, name, _ = m_cls.groups()
            if sign == "+":
                added_classes.add(f"{path}::{name}")
            else:
                removed_classes.add(f"{path}::{name}")

    added_only_defs = set(added_defs) - set(removed_defs)
    if added_only_defs:
        name = sorted(added_only_defs)[0]
        return BumpDecision("Y", "A1", f"A1 — public def/class added ({name})")

    added_only_cls = added_classes - removed_classes
    if added_only_cls:
        name = sorted(added_only_cls)[0]
        return BumpDecision("Y", "A1", f"A1 — public class added ({name})")

    new_all = all_added - all_removed
    if new_all:
        return BumpDecision(
            "Y", "A2", f"A2 — __all__ gained {sorted(new_all)!r}"
        )

    removed_only_defs = set(removed_defs) - set(added_defs)
    if removed_only_defs:
        name = sorted(removed_only_defs)[0]
        return BumpDecision("Y", "B2", f"B2 — public def deleted ({name})")

    removed_only_cls = removed_classes - added_classes
    if removed_only_cls:
        name = sorted(removed_only_cls)[0]
        return BumpDecision("Y", "B2", f"B2 — public class deleted ({name})")

    for key in set(added_defs) & set(removed_defs):
        if added_defs[key] != removed_defs[key]:
            return BumpDecision(
                "Y",
                "B3",
                f"B3 — public signature changed ({key})",
            )

    return None


def _iter_pyproject_lines(diff: str) -> list[str]:
    in_file = False
    lines: list[str] = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            in_file = "pyproject.toml" in line
            continue
        if line.startswith("+++ b/"):
            in_file = line[6:].endswith("pyproject.toml")
            continue
        if not in_file:
            continue
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        lines.append(line)
    return lines


def _check_a3_b4(diff: str) -> BumpDecision | None:
    """Detect entry-points / [project.scripts] key add/remove/change."""
    section: str | None = None
    # Track keys relative to current section using +/- lines only.
    # For context-aware section: also track unchanged section headers (space prefix).
    added: set[tuple[str, str]] = set()
    removed: set[tuple[str, str]] = set()

    # Re-scan with section tracking including context lines
    in_file = False
    for raw in diff.splitlines():
        if raw.startswith("diff --git "):
            in_file = "pyproject.toml" in raw
            section = None
            continue
        if raw.startswith("+++ b/"):
            in_file = raw[6:].endswith("pyproject.toml")
            section = None
            continue
        if not in_file:
            continue
        if raw.startswith("+++") or raw.startswith("---") or raw.startswith("@@"):
            continue
        if not raw:
            continue
        sign = raw[0]
        body = raw[1:]
        if _ENTRY_SECTION.match(body.strip()):
            if sign in " +-":
                section = body.strip()
            continue
        if section is None:
            continue
        if sign not in "+-":
            continue
        m = _ENTRY_KEY.match(raw)
        if not m:
            continue
        key = (section, m.group(2))
        if m.group(1) == "+":
            added.add(key)
        else:
            removed.add(key)

    only_added = added - removed
    if only_added:
        sec, key = sorted(only_added)[0]
        return BumpDecision("Y", "A3", f"A3 — entry-point key added ({sec} {key})")

    only_removed = removed - added
    if only_removed:
        sec, key = sorted(only_removed)[0]
        return BumpDecision("Y", "B4", f"B4 — entry-point key removed ({sec} {key})")

    # same key both removed and added → value change counts as B4
    changed = added & removed
    if changed:
        # Need to verify values actually differ — if both present, treat as change
        sec, key = sorted(changed)[0]
        return BumpDecision("Y", "B4", f"B4 — entry-point key changed ({sec} {key})")

    return None


def _check_b1(worktree: Path, base: str) -> BumpDecision | None:
    result = _run_git(worktree, "log", f"{base}..HEAD", "--format=%s%n%b")
    if result.returncode != 0:
        return None
    if _BREAKING_MSG.search(result.stdout):
        return BumpDecision("Y", "B1", "B1 — commit message indicates breaking change")
    return None


def decide_bump(worktree: Path, base: str) -> BumpDecision:
    """Return Y/Z decision. On unparseable / missing diff → Z."""
    try:
        b1 = _check_b1(worktree, base)
        if b1:
            return b1

        diff_proc = _run_git(worktree, "diff", f"{base}..HEAD", "--")
        if diff_proc.returncode != 0:
            return BumpDecision("Z", "Z", "Z — diff unavailable; defaulting to Z")
        diff = diff_proc.stdout
        if not diff.strip():
            return BumpDecision("Z", "Z", "Z — empty diff")

        for checker in (_check_a1_a2_b2_b3, _check_a3_b4):
            hit = checker(diff)
            if hit:
                return hit

        return BumpDecision("Z", "Z", "Z — no public API / breaking change detected")
    except Exception as exc:  # noqa: BLE001 — fail soft to Z
        return BumpDecision("Z", "Z", f"Z — decision error ({exc}); defaulting to Z")


def apply_bump(content: str, bump_type: str) -> tuple[str, str, str] | None:
    """Return (new_content, old_ver, new_ver) or None if version missing."""
    match = _VERSION_RE.search(content)
    if not match:
        return None
    old_ver = match.group(1)
    parts = old_ver.split(".")
    if len(parts) < 3:
        return None
    # Never touch X; only rewrite Y/Z (and keep any trailing segments dropped)
    x, y, z = parts[0], parts[1], parts[2]
    # strip pep440 local/dev suffixes from z for arithmetic
    z_num = re.match(r"^(\d+)", z)
    if not z_num:
        return None
    y_i = int(y)
    z_i = int(z_num.group(1))
    if bump_type == "Y":
        y_i += 1
        z_i = 0
    else:
        z_i += 1
    new_ver = f"{x}.{y_i}.{z_i}"
    new_content = _VERSION_RE.sub(f'version = "{new_ver}"', content, count=1)
    return new_content, old_ver, new_ver


def run_bump(worktree: Path, base: str) -> int:
    pyproject = worktree / "pyproject.toml"
    if not pyproject.is_file():
        print("VERSION_BUMP_TYPE: Z")
        print("VERSION_BUMP_REASON: Z — pyproject.toml missing; skip")
        return 0

    decision = decide_bump(worktree, base)
    print(f"VERSION_BUMP_TYPE: {decision.bump_type}")
    print(f"VERSION_BUMP_REASON: {decision.reason}")

    content = pyproject.read_text(encoding="utf-8")
    applied = apply_bump(content, decision.bump_type)
    if applied is None:
        print("pyproject.toml: version field not found, skipping bump", file=sys.stderr)
        return 0

    new_content, old_ver, new_ver = applied
    if new_content == content:
        return 0

    pyproject.write_text(new_content, encoding="utf-8")
    add = _run_git(worktree, "add", "pyproject.toml")
    if add.returncode != 0:
        print(add.stderr, file=sys.stderr)
        return 1
    msg = f"chore: bump version to {new_ver} ({decision.bump_type}: {decision.reason})"
    commit = _run_git(worktree, "commit", "-m", msg)
    if commit.returncode != 0:
        print(commit.stderr, file=sys.stderr)
        return 1
    print(f"pyproject.toml: {decision.bump_type}-bumped {old_ver} → {new_ver}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--base", required=True)
    args = parser.parse_args(argv)
    return run_bump(Path(args.worktree), args.base)


if __name__ == "__main__":
    sys.exit(main())
