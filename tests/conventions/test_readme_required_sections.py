"""README required headings and OSS doc surface rules (sumipan/nexus#7353)."""
from __future__ import annotations

import re

from tests.conventions._util import REPO_ROOT

_README = REPO_ROOT / "README.md"

_REQUIRED_HEADINGS = (
    "## Status",
    "## Installation",
    "## Quick Start",
    "## CLI Reference",
    "## Public API",
    "## Architecture",
    "## Configuration",
    "## Error Reference",
    "## License",
)

_HOST_WORD = re.compile(r"\b(nexus|diary)\b", re.IGNORECASE)

# Names dropped from the product; README must not document them (CHANGELOG / OSS §4.3).
_REMOVED_README_SUBSTRINGS = (
    "diary_allow_paths",
    "normalize_similar_title",
    "diary_worktree",
)


def _level2_heading_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in text.splitlines():
        if line.startswith("## ") and not line.startswith("### "):
            counts[line] = counts.get(line, 0) + 1
    return counts


def test_readme_required_headings_present_once():
    """refs #7353 — OSS §4.1 section headings appear exactly once."""
    text = _README.read_text(encoding="utf-8")
    assert text.startswith("# "), "README must open with a level-1 title"
    counts = _level2_heading_counts(text)
    missing = [heading for heading in _REQUIRED_HEADINGS if counts.get(heading, 0) != 1]
    assert not missing, f"missing or duplicated headings: {missing}"


def test_readme_avoids_host_specific_vocabulary():
    """refs #7353 — README must not name host-only products."""
    text = _README.read_text(encoding="utf-8")
    hits = sorted({m.group(0).lower() for m in _HOST_WORD.finditer(text)})
    assert not hits, f"host vocabulary in README: {hits}"


def test_readme_omits_removed_surface_names():
    """refs #7353 — removed config and API names stay out of README."""
    lowered = _README.read_text(encoding="utf-8").lower()
    present = [tok for tok in _REMOVED_README_SUBSTRINGS if tok.lower() in lowered]
    assert not present, f"removed surface still mentioned: {present}"
