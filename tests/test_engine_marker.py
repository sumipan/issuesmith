"""Verify PIPELINE_STATUS standalone-line detection tolerates LLM decoration (backticks, bold).

On 2026-09-09, codex emitted CP2's final line as `PIPELINE_STATUS: CP2_PASS` (with
backticks). The run was actually PASS, but run-guarded exited non-zero and fired
the CP2 FAIL handler.
"""
from __future__ import annotations

import pytest

from issuesmith.engine import _extract_status_values, _has_inline_marker

# Real incident strings from #4191 / #4202; built without CJK literals (#3385).
_CP2_PASS_GLUED_LONG = (
    "PIPELINE_STATUS: CP2_PASS"
    + chr(0x5168)
    + chr(0x4F53)
    + chr(0x30C6)
    + chr(0x30B9)
    + chr(0x30C8)
    + chr(0x306F)
    + chr(0x7D04)
    + "79%"
    + chr(0x3067)
    + chr(0x505C)
    + chr(0x6B62)
    + chr(0x3057)
    + chr(0x305F)
    + chr(0x3081)
    + chr(0x7D42)
    + chr(0x4E86)
    + chr(0x3057)
    + chr(0x307E)
    + chr(0x3057)
    + chr(0x305F)
    + chr(0x3002)
)
_REPAIR_DONE_GLUED_ISSUE = (
    "PIPELINE_STATUS: REPAIR_DONEIssue #4202 "
    + chr(0x306E)
    + chr(0x4FEE)
    + chr(0x5FA9)
    + chr(0x306F)
    + chr(0x5B8C)
    + chr(0x4E86)
    + chr(0x3057)
    + chr(0x3066)
    + chr(0x3044)
    + chr(0x307E)
    + chr(0x3059)
    + chr(0x3002)
)
_CP2_PASS_GLUED_PERIOD = "PIPELINE_STATUS: CP2_PASS" + chr(0x3002)
_CP2_PASS_GLUED_NOTE = (
    "PIPELINE_STATUS: CP2_PASS"
    + chr(0xFF08)
    + chr(0x88DC)
    + chr(0x8DB3)
    + chr(0xFF09)
)
_CP2_PASS_GLUED_SHORT = "PIPELINE_STATUS: CP2_PASS" + chr(0x88DC) + chr(0x8DB3)


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


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (_CP2_PASS_GLUED_LONG, "CP2_PASS"),
        (_REPAIR_DONE_GLUED_ISSUE, "REPAIR_DONE"),
        ("PIPELINE_STATUS: REPAIR_DONEderived_allow_paths:", "REPAIR_DONE"),
        (_CP2_PASS_GLUED_PERIOD, "CP2_PASS"),
        (_CP2_PASS_GLUED_NOTE, "CP2_PASS"),
    ],
)
def test_trailing_text_without_whitespace_is_stripped(line, expected):
    assert _extract_status_values(line + "\n") == [expected]


def test_cpassed_is_not_truncated_to_cpass():
    assert _extract_status_values("PIPELINE_STATUS: CP2_PASSED\n") == ["CP2_PASSED"]


@pytest.mark.parametrize(
    "line",
    [
        _CP2_PASS_GLUED_LONG,
        _REPAIR_DONE_GLUED_ISSUE,
        "PIPELINE_STATUS: REPAIR_DONEderived_allow_paths:",
    ],
)
def test_trailing_text_emits_stderr_diagnostic(line, capsys):
    _extract_status_values(line + "\n")
    err = capsys.readouterr().err
    assert "[issuesmith-engine] marker line had trailing text after" in err
    assert err.count("[issuesmith-engine] marker line had trailing text after") == 1


def test_clean_standalone_line_emits_no_trailing_diagnostic(capsys):
    _extract_status_values("PIPELINE_STATUS: CP2_PASS\n")
    assert capsys.readouterr().err == ""


def test_multiple_markers_preserve_order_with_trailing_split(capsys):
    stdout = f"{_CP2_PASS_GLUED_SHORT}\nPIPELINE_STATUS: IMPL_DONE\n"
    assert _extract_status_values(stdout) == ["CP2_PASS", "IMPL_DONE"]
    assert capsys.readouterr().err.count("marker line had trailing text after") == 1
