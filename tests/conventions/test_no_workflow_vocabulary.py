"""Verbs, gates, observe, andon and label projection must not hard-code the nexus workflow vocabulary."""
from __future__ import annotations

import re

import pytest

from tests.conventions._util import REPO_ROOT, py_files

VOCABULARY_FREE_PATHS = (
    "src/issuesmith/verbs",
    "src/issuesmith/gates",
    "src/issuesmith/observe",
    "src/issuesmith/andon.py",
    "src/issuesmith/ops/labels.py",
    "src/issuesmith/ops/dispatch.py",
    "src/issuesmith/engine.py",
)
_VOCAB = re.compile(r'"issuesmith:|\b(draft|develop|merge|sub)-(ready|running|done)\b|\b(b1|cp1|cp2|p2r|m1r|mg1|sub1)\b')

# Known remaining hits (file, line text fragment). Shrink; never grow.
KNOWN_HITS: frozenset[tuple[str, str]] = frozenset(
    {
        ("src/issuesmith/observe/__init__.py", 'startswith("issuesmith:")'),
        ("src/issuesmith/engine.py", "IMPLEMENTATION_STEP_IDS = frozenset("),
    }
)


def _targets():
    out = []
    for rel in VOCABULARY_FREE_PATHS:
        p = REPO_ROOT / rel
        out.extend(py_files(p) if p.is_dir() else [p])
    return out


def _hits():
    found: set[tuple[str, str]] = set()
    for path in _targets():
        rel = str(path.relative_to(REPO_ROOT))
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not _VOCAB.search(stripped):
                continue
            found.add((rel, stripped))
    return found


def test_no_new_workflow_vocabulary():
    found = _hits()
    new = {(f, t[:60]) for f, t in found if not any(f == kf and kt in t for kf, kt in KNOWN_HITS)}
    assert not new, f"nexus workflow vocabulary hard-coded in OSS modules: {sorted(new)}"


@pytest.mark.parametrize("rel", sorted({f for f, _ in KNOWN_HITS}))
def test_known_hits_still_present_or_entry_removed(rel: str):
    assert any(f == rel for f, _ in _hits()), f"KNOWN_HITS has a stale entry for {rel}; remove it"
