"""Task failure record round-trip and legacy parsing. Refs #8076."""
from __future__ import annotations

import yaml

from issuesmith.lines.develop.hooks import COMMENT_MARKER
from issuesmith.task_failure import format_record, parse_records


def test_format_record_round_trips_special_characters():
    """Refs #8076 AC1."""
    stderr_tail = "first:\n```\nlast"
    block = format_record(
        task_uuid="u1",
        step="p1",
        returncode=1,
        failure_class="TIMEOUT",
        stderr_tail=stderr_tail,
    )
    records = parse_records(block)
    assert len(records) == 1
    record = records[0]
    assert record["task"] == "u1"
    assert record["step"] == "p1"
    assert record["returncode"] == 1
    assert record["failure_class"] == "TIMEOUT"
    assert record["stderr_tail"] == stderr_tail


def test_parse_records_reads_legacy_marker_comment():
    """Refs #8076 AC2."""
    body = (
        f"{COMMENT_MARKER}\n"
        "## Task failed (verify)\n\n"
        "- step: `verify`\n"
        "- task: `legacy-u`\n"
        "- engine: `-`\n"
        "- exit code: `2`\n"
        "- failure class: `-`\n\n"
        "```text\nlegacy stderr\n```\n"
    )
    records = parse_records(body)
    assert len(records) == 1
    assert records[0]["task"] == "legacy-u"
    assert records[0]["step"] == "verify"
    assert records[0]["returncode"] == 2
    assert records[0]["failure_class"] == ""
    assert records[0]["stderr_tail"] == "legacy stderr"

    no_marker = body.replace(COMMENT_MARKER, "")
    assert parse_records(no_marker) == []


def test_parse_records_ignores_bad_new_format_without_legacy_fallback():
    """Refs #8076 AC3."""
    good = format_record(
        task_uuid="good-u",
        step="p1",
        returncode=1,
        failure_class="TIMEOUT",
        stderr_tail="ok",
    )
    body = "\n".join(
        [
            "```issuesmith-task-failure",
            ": not yaml",
            "```",
            "```issuesmith-task-failure",
            yaml.safe_dump(["not", "a", "map"]),
            "```",
            "```issuesmith-task-failure",
            yaml.safe_dump({"task": "", "step": "x"}),
            "```",
            good.strip(),
            COMMENT_MARKER,
            "- task: `legacy`",
        ]
    )
    records = parse_records(body)
    assert len(records) == 1
    assert records[0]["task"] == "good-u"

    unclosed = "\n".join(
        [
            COMMENT_MARKER,
            "- task: `legacy`",
            "```issuesmith-task-failure",
            "task: [",
        ]
    )
    assert parse_records(unclosed) == []
