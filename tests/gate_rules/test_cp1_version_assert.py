"""tests/gate_rules/test_cp1_version_assert.py — exact version-assert gate in tests (#3065)."""

from __future__ import annotations

from pathlib import Path

from issuesmith.gate_rules.cp1 import check_test_version_exact_assert

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "version_exact_assert"
_RULE_ID = "cp1.test_version_exact_assert"
def _exact_assert_diff() -> str:
    return (_FIXTURES / "mltgnt_exact_assert.diff").read_text(encoding="utf-8")


def _pin_only_diff() -> str:
    """Only the real-commit aaf46d1 hunk (no pattern-a '+' lines)."""
    parts = _exact_assert_diff().split("diff --git ")
    assert len(parts) >= 3, "fixture needs 2 hunks for patterns a/b"
    return "diff --git " + parts[2]


def test_exact_version_assert_fails():
    """Pattern a: project[\"version\"] == \"X.Y.Z\" → Violation."""
    vs = check_test_version_exact_assert(_exact_assert_diff())
    assert vs, "should detect exact version assert"
    assert vs[0].rule_id == _RULE_ID
    assert vs[0].severity == "fail"
    assert vs[0].auto_fixable is False
    assert vs[0].fix_hint is not None
    assert ">=" in vs[0].fix_hint
    assert "publish" in vs[0].fix_hint


def test_pin_exact_assert_fails():
    """Pattern b: exact pin of git+https://…@vX.Y.Z → Violation."""
    vs = check_test_version_exact_assert(_pin_only_diff())
    assert vs, "should detect git pin exact assert"
    assert vs[0].rule_id == _RULE_ID
    assert vs[0].severity == "fail"
    assert vs[0].auto_fixable is False
    assert vs[0].fix_hint is not None
    assert ">=" in vs[0].fix_hint
    assert "publish" in vs[0].fix_hint


def test_ge_version_check_passes():
    """Lower-bound check parts >= [0, 43, 0] yields no Violation."""
    diff = (_FIXTURES / "mltgnt_ge_check.diff").read_text(encoding="utf-8")
    assert check_test_version_exact_assert(diff) == []


def test_non_test_file_skipped():
    """version == lines under src/ are out of scope."""
    diff = """\
diff --git a/src/pkg/version.py b/src/pkg/version.py
index 1111111..2222222 100644
--- a/src/pkg/version.py
+++ b/src/pkg/version.py
@@ -1,3 +1,4 @@
+assert project["version"] == "0.27.0"
+dependency = "ghdag @ git+https://github.com/sumipan/ghdag.git@v0.43.0"
+assert deps.count(dependency) == 1
"""
    assert check_test_version_exact_assert(diff) == []
