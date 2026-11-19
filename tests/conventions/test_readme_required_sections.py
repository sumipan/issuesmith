"""README required headings and OSS doc surface rules (sumipan/nexus#7353)."""
from __future__ import annotations

from tests.conventions._util import REPO_ROOT

_README = REPO_ROOT / "README.md"

_REQUIRED_HEADINGS = (
    "## Status",
    "## Installation",
    "## Quick Start",
    "## CLI Reference",
    "## Output contract",
    "## Public API",
    "## Public API Stability",
    "## Architecture",
    "## Gates",
    "## Configuration",
    "## Error Reference",
    "## License",
)

# Removed symbols (CHANGELOG Removed / BREAKING) and host-only vocabulary.
_FORBIDDEN_TERMS = (
    "brake_state",
    "normalize_similar_title",
    "cp1-gate",
    "m2-gate",
    "b1-verify",
    "b1_tier",
    "cp2_tier",
    "call_managed",
    "--exec-md",
    "nexus",
    "diary",
)


def _level2_heading_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in text.splitlines():
        if line.startswith("## "):
            counts[line] = counts.get(line, 0) + 1
    return counts


def _section(text: str, heading: str) -> str:
    lines = text.splitlines()
    start = lines.index(heading) + 1
    end = next(
        (i for i in range(start, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_readme_has_required_sections():
    """refs #7353 AC1 — OSS §4.1 headings appear exactly once; License names MIT."""
    text = _README.read_text(encoding="utf-8")
    assert text.startswith("# "), "README must open with a level-1 title"
    counts = _level2_heading_counts(text)
    missing = [h for h in _REQUIRED_HEADINGS if counts.get(h, 0) != 1]
    assert not missing, f"missing or duplicated headings: {missing}"
    assert "MIT" in _section(text, "## License")


def test_readme_has_no_removed_or_host_terms():
    """refs #7353 AC11 — removed symbols and host vocabulary stay out of README."""
    lowered = _README.read_text(encoding="utf-8").lower()
    present = [t for t in _FORBIDDEN_TERMS if t.lower() in lowered]
    assert not present, f"forbidden terms in README: {present}"
