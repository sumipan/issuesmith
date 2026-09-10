"""tests/gate_rules/test_cp1_version_assert.py — テスト完全一致 assert ゲート (#3065)."""

from __future__ import annotations

from pathlib import Path

from issuesmith.gate_rules.cp1 import check_test_version_exact_assert

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "version_exact_assert"
_RULE_ID = "cp1.test_version_exact_assert"
_FIX_HINT = "版・pin は下限（`>=`）で検査するか、テストを書かない。bump は publish が決定論的に行う"


def _exact_assert_diff() -> str:
    return (_FIXTURES / "mltgnt_exact_assert.diff").read_text(encoding="utf-8")


def _pin_only_diff() -> str:
    """実コミット aaf46d1 側 hunk のみ（pattern a の + 行を含まない）。"""
    parts = _exact_assert_diff().split("diff --git ")
    assert len(parts) >= 3, "fixture に pattern a/b の 2 hunk が必要"
    return "diff --git " + parts[2]


def test_exact_version_assert_fails():
    """パターン a: project[\"version\"] == \"X.Y.Z\" → Violation."""
    vs = check_test_version_exact_assert(_exact_assert_diff())
    assert vs, "exact version assert を検出すべき"
    assert vs[0].rule_id == _RULE_ID
    assert vs[0].severity == "fail"
    assert vs[0].auto_fixable is False
    assert vs[0].fix_hint is not None
    assert _FIX_HINT in vs[0].fix_hint


def test_pin_exact_assert_fails():
    """パターン b: git+https://…@vX.Y.Z の pin 完全一致 → Violation."""
    vs = check_test_version_exact_assert(_pin_only_diff())
    assert vs, "git pin exact assert を検出すべき"
    assert vs[0].rule_id == _RULE_ID
    assert vs[0].severity == "fail"
    assert vs[0].auto_fixable is False
    assert vs[0].fix_hint is not None
    assert _FIX_HINT in vs[0].fix_hint


def test_ge_version_check_passes():
    """下限検査 parts >= [0, 43, 0] は Violation なし."""
    diff = (_FIXTURES / "mltgnt_ge_check.diff").read_text(encoding="utf-8")
    assert check_test_version_exact_assert(diff) == []


def test_non_test_file_skipped():
    """src/ 以下の version == 行は対象外."""
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
