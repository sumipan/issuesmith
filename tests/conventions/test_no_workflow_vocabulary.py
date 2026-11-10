"""OSS src must not hard-code consumer workflow vocabulary (#4790)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.conventions._util import REPO_ROOT

_VOCABULARY_FREE_PATHS = (
    "src/issuesmith/preconditions.py",
    "src/issuesmith/queue.py",
    "src/issuesmith/resume.py",
    "src/issuesmith/ops/dispatch.py",
)

_VOCAB = re.compile(
    r'"issuesmith:|\b(draft|develop|merge|sub)-(ready|running|done)\b'
    r'|"(draft|develop|merge|sub|brushup|impl|subissue)"'
    r'|\b(MERGE_DONE|IMPL_DONE|REPORT_DONE)\b'
    r"|scope:milestone"
    r'|"(b1|cp1|cp2|p0|p1|p3|m1|m2|mg1|sub1)"'
    r'|\b(BRUSHUP_(DONE|FAILED)|IMPL_FAILED|CP2_(PASS|FAIL)|SUB_(CREATED|BODY_READY|BODY_FAILED)|P3_[A-Z_]+|MIGRATE_DONE|B1_RECOVERY)\b'
    r"|issuesmith:(rejected|superseded)"
    r"|\b(cp1|m2|b1_ac_format|b1_migration|b1_milestone_subdesign)\.[a-z_]"
    r"|\b(P0_FETCH_LOCK_WAIT|INTENTIONAL_HOLD)\b"
)

_LEDGER_PATH = REPO_ROOT / "tests/conventions/known_vocabulary_hits.txt"


def _src_py_files() -> list[Path]:
    root = REPO_ROOT / "src/issuesmith"
    return sorted(p for p in root.rglob("*.py") if p.is_file())


def _count_hits(path: Path) -> int:
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


@pytest.mark.parametrize(
    "sample",
    [
        '"b1"',
        "BRUSHUP_DONE",
        "issuesmith:rejected",
    ],
)
def test_vocab_matches_consumer_workflow_literals(sample: str):
    assert _VOCAB.search(sample), f"_VOCAB must match {sample!r}"


@pytest.mark.parametrize(
    "sample",
    [
        '"cp1.intentional_hold"',
        '"m2.unchecked"',
        '"b1_ac_format.missing_section"',
        '"b1_migration.post_merge_schema"',
        '"b1_milestone_subdesign.sub_count_mismatch"',
        "P0_FETCH_LOCK_WAIT",
        "INTENTIONAL_HOLD",
    ],
)
def test_vocab_matches_legacy_workflow_literals(sample: str):
    assert _VOCAB.search(sample), f"_VOCAB must match {sample!r}"


def _ledger_total(ledger: dict[str, int]) -> int:
    return sum(ledger.values())


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

    missing = sorted(rel for rel in ledger if not (REPO_ROOT / rel).is_file())
    assert not missing, f"stale ledger entries (remove paths missing under src): {missing}"

    total_actual = sum(actual.values())
    total_ledger = _ledger_total(ledger)
    assert total_actual <= total_ledger, (
        f"vocabulary hit total {total_actual} exceeds ledger cap {total_ledger}"
    )


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


# #6077: the compatibility shims and legacy aliases were removed; nothing may
# reference them again.
_LEGACY_REFERENCE = re.compile(
    r"\bissuesmith\.(cp1_gate|m2_gate|b1_verify)\b"
    r"|\bfrom issuesmith import (cp1_gate|m2_gate|b1_verify)\b"
    r"|\bissuesmith\.gates\.(m1|m2)\b"
    r"|\bissuesmith\.gate_rules\.(cp1|m2|b1_ac_format|b1_migration|b1_milestone_subdesign)\b"
    r"|\b(Cp1Rules|M2Rules|B1AcFormatRules|B1MigrationRules|B1MilestoneSubdesignRules|check_m2)\b"
)

# Files that assert the removal itself and therefore spell the legacy names.
_LEGACY_REFERENCE_EXEMPT = frozenset(
    {
        "tests/conventions/test_no_workflow_vocabulary.py",
        "tests/test_gates.py",
    }
)


def test_source_and_tests_use_canonical_module_references():
    hits: list[str] = []
    for root in (REPO_ROOT / "src/issuesmith", REPO_ROOT / "tests"):
        for path in sorted(root.rglob("*.py")):
            rel = str(path.relative_to(REPO_ROOT))
            if rel in _LEGACY_REFERENCE_EXEMPT:
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if _LEGACY_REFERENCE.search(line):
                    hits.append(f"{rel}:{lineno}")
    assert not hits, "legacy shim / alias references remain:\n" + "\n".join(hits)
