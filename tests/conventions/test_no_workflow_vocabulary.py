"""OSS src must not hard-code consumer workflow vocabulary (#4790)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.conventions._util import REPO_ROOT

_VOCABULARY_FREE_PATHS = (
    "src/issuesmith/config.py",
    "src/issuesmith/preconditions.py",
    "src/issuesmith/queue.py",
    "src/issuesmith/queue_triage.py",
    "src/issuesmith/resume.py",
    "src/issuesmith/ops/dispatch.py",
)

_VOCAB = re.compile(
    r'"issuesmith:|\b(draft|develop|merge|sub)-(ready|running|done)\b'
    r'|"(draft|develop|merge|sub|brushup|impl|subissue)"'
    r'|\b(MERGE_DONE|IMPL_DONE|REPORT_DONE)\b'
    r"|scope:milestone"
)

_LEDGER_PATH = REPO_ROOT / "tests/conventions/known_vocabulary_hits.txt"


def _src_py_files() -> list[Path]:
    root = REPO_ROOT / "src/issuesmith"
    return sorted(p for p in root.rglob("*.py") if p.is_file())


def _count_hits(path: Path) -> int:
    rel = str(path.relative_to(REPO_ROOT))
    if rel in _VOCABULARY_FREE_PATHS:
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if _VOCAB.search(stripped):
            count += 1
    return count


def _read_ledger() -> dict[str, int]:
    if not _LEDGER_PATH.is_file():
        return {}
    ledger: dict[str, int] = {}
    for line in _LEDGER_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rel, count_str = line.rsplit(" ", 1)
        ledger[rel] = int(count_str)
    return ledger


def test_vocabulary_gate_matches_ledger():
    ledger = _read_ledger()
    actual: dict[str, int] = {}
    for path in _src_py_files():
        rel = str(path.relative_to(REPO_ROOT))
        hits = _count_hits(path)
        if hits:
            actual[rel] = hits

    unknown = sorted(set(actual) - set(ledger))
    assert not unknown, f"new vocabulary hits (add to ledger after fixing): {unknown}"

    extra = sorted(set(ledger) - set(actual))
    assert not extra, f"stale ledger entries (shrink ledger): {extra}"

    over = {rel: (actual[rel], ledger[rel]) for rel in actual if actual[rel] > ledger[rel]}
    assert not over, f"vocabulary hits increased: {over}"

    under = {rel: (actual[rel], ledger[rel]) for rel in actual if actual[rel] < ledger[rel]}
    assert not under, f"vocabulary hits decreased (shrink ledger): {under}"


@pytest.mark.parametrize("rel", sorted(_VOCABULARY_FREE_PATHS))
def test_vocabulary_free_paths_have_zero_hits(rel: str):
    path = REPO_ROOT / rel
    assert _count_hits(path) == 0, f"{rel} must not contain workflow vocabulary literals"


# #5065: unlike the ledger above (shrinks over time), this keeps the count at zero.
_DIARY = re.compile("diary", re.IGNORECASE)


def _diary_hits(root: Path) -> list[str]:
    hits: list[str] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _DIARY.search(line):
                hits.append(f"{path.relative_to(root.parent.parent)}:{lineno}")
    return hits


def test_src_has_no_diary_word():
    hits = _diary_hits(REPO_ROOT / "src/issuesmith")
    assert not hits, "src/issuesmith must not mention 'diary':\n" + "\n".join(hits)


def test_diary_hits_reports_path_and_line(tmp_path: Path):
    pkg = tmp_path / "src/issuesmith"
    pkg.mkdir(parents=True)
    (pkg / "mod.py").write_text("x = 1\n# legacy Diary note\n", encoding="utf-8")
    (pkg / "data.yaml").write_text("diary: true\n", encoding="utf-8")
    assert _diary_hits(pkg) == ["src/issuesmith/data.yaml:1", "src/issuesmith/mod.py:2"]
