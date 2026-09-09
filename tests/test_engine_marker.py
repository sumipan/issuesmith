"""PIPELINE_STATUS 独立行判定が LLM の装飾（バッククォート・太字）を許容することの検証。

2026-09-09、codex が CP2 の最終行を `PIPELINE_STATUS: CP2_PASS`（バッククォート付き）で
出力し、実体は PASS なのに run-guarded が非ゼロ終了して CP2 FAIL ハンドラが発火した。
"""
from __future__ import annotations

import pytest

from issuesmith.engine import _extract_status_values, _has_inline_marker


@pytest.mark.parametrize(
    "line",
    [
        "PIPELINE_STATUS: CP2_PASS",
        "`PIPELINE_STATUS: CP2_PASS`",
        "**PIPELINE_STATUS: CP2_PASS**",
        "PIPELINE_STATUS: CP2_PASS   ",
    ],
)
def test_standalone_marker_accepts_decoration(line):
    stdout = f"レビュー完了。\n{line}\n"
    assert _extract_status_values(stdout) == ["CP2_PASS"]


def test_status_with_colon_suffix_is_kept_whole():
    assert _extract_status_values("PIPELINE_STATUS: MERGE_FAILED:PERMISSION_ERROR\n") == [
        "MERGE_FAILED:PERMISSION_ERROR"
    ]


@pytest.mark.parametrize(
    "line",
    [
        "- PIPELINE_STATUS: CP2_PASS",
        "最後に PIPELINE_STATUS: CP2_PASS を出力した。",
        "PIPELINE_STATUS: CP2_PASS と CP2_FAIL",
    ],
)
def test_non_standalone_marker_is_rejected(line):
    assert _extract_status_values(line + "\n") == []
    assert _has_inline_marker(line + "\n", ["CP2_PASS"]) is True
