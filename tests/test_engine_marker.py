"""Verify PIPELINE_STATUS standalone-line detection tolerates LLM decoration (backticks, bold).

On 2026-09-09, codex emitted CP2's final line as `PIPELINE_STATUS: CP2_PASS` (with
backticks). The run was actually PASS, but run-guarded exited non-zero and fired
the CP2 FAIL handler.
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
    # Synthetic prose around the marker (not a product contract string)
    stdout = f"Review complete.\n{line}\n"
    assert _extract_status_values(stdout) == ["CP2_PASS"]


def test_status_with_colon_suffix_is_kept_whole():
    assert _extract_status_values("PIPELINE_STATUS: MERGE_FAILED:PERMISSION_ERROR\n") == [
        "MERGE_FAILED:PERMISSION_ERROR"
    ]


@pytest.mark.parametrize(
    "line",
    [
        "- PIPELINE_STATUS: CP2_PASS",
        "Finally output PIPELINE_STATUS: CP2_PASS.",
        "PIPELINE_STATUS: CP2_PASS and CP2_FAIL",
    ],
)
def test_non_standalone_marker_is_rejected(line):
    assert _extract_status_values(line + "\n") == []
    assert _has_inline_marker(line + "\n", ["CP2_PASS"]) is True
