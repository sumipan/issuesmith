"""GitHub API rate-limit state from audit.jsonl (#3769, AC-2 / AC-3)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from issuesmith.quota_gate import (
    GitHubApiState,
    is_github_api_low,
    read_github_api_notified,
    read_github_api_state,
    write_github_api_notified,
)

_NOW = datetime(2026, 9, 25, 4, 0, tzinfo=timezone.utc)


def _rate_record(remaining: int, reset: datetime, **extra) -> dict:
    return {
        "event": "github_rate_limit",
        "timestamp": "2026-09-25T03:59:00+00:00",
        "remaining": remaining,
        "limit": 5000,
        "reset": int(reset.timestamp()),
        "used": 5000 - remaining,
        "correlation_id": None,
        **extra,
    }


def _write_audit(path: Path, records: list[dict | str]) -> Path:
    lines = [r if isinstance(r, str) else json.dumps(r) for r in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_missing_file_returns_none(tmp_path):
    assert read_github_api_state(tmp_path / "audit.jsonl") is None


def test_empty_file_returns_none(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text("", encoding="utf-8")
    assert read_github_api_state(path) is None


def test_no_rate_limit_record_returns_none(tmp_path):
    path = _write_audit(tmp_path / "audit.jsonl", [
        {"event": "task_started", "uuid": "a"},
        {"event": "task_finished", "uuid": "a"},
    ])
    assert read_github_api_state(path) is None


def test_skips_truncated_tail_line(tmp_path):
    reset = _NOW + timedelta(minutes=30)
    path = _write_audit(tmp_path / "audit.jsonl", [
        _rate_record(700, reset),
        '{"event": "github_rate_limit", "remaining": 1',
    ])
    state = read_github_api_state(path)
    assert state is not None and state.remaining == 700


def test_malformed_latest_record_returns_none(tmp_path):
    path = _write_audit(tmp_path / "audit.jsonl", [
        {"event": "github_rate_limit", "remaining": "x", "reset": "y"},
    ])
    assert read_github_api_state(path) is None


def _state(remaining: int, reset_at: datetime) -> GitHubApiState:
    return GitHubApiState(remaining=remaining, reset_at=reset_at, observed_at=_NOW)


def test_is_low_none_state_is_false():
    assert is_github_api_low(None, 800, _NOW) is False


def test_is_low_below_threshold_future_reset():
    assert is_github_api_low(_state(500, _NOW + timedelta(minutes=10)), 800, _NOW) is True


def test_is_low_at_threshold_is_false():
    assert is_github_api_low(_state(800, _NOW + timedelta(minutes=10)), 800, _NOW) is False


def test_is_low_past_reset_is_false():
    """AC-2: the budget has already been refilled once reset_at is past."""
    assert is_github_api_low(_state(0, _NOW - timedelta(seconds=1)), 800, _NOW) is False


def test_is_low_naive_now_treated_as_utc():
    naive = _NOW.replace(tzinfo=None)
    assert is_github_api_low(_state(10, _NOW + timedelta(minutes=1)), 800, naive) is True


def test_notified_flag_missing_file(tmp_path):
    path = tmp_path / "quota-gate.json"
    assert read_github_api_notified(path) is False
    assert write_github_api_notified(path, False) is False
    assert not path.exists()


def test_notified_read_true_when_resources_flag_set(tmp_path):
    path = tmp_path / "quota-gate.json"
    path.write_text(
        json.dumps({"schema_version": 1, "resources": {"github_api_notified": True}}),
        encoding="utf-8",
    )
    assert read_github_api_notified(path) is True


def test_notified_read_false_when_resources_not_dict(tmp_path):
    path = tmp_path / "quota-gate.json"
    path.write_text(
        json.dumps({"schema_version": 1, "resources": "bad"}),
        encoding="utf-8",
    )
    assert read_github_api_notified(path) is False


def test_notified_read_oserror_returns_false(tmp_path):
    path = tmp_path / "quota-gate.json"
    with patch("ghdag.quota.QuotaGate") as mock_cls:
        mock_cls.return_value.read_state.side_effect = OSError("denied")
        assert read_github_api_notified(path) is False


def test_notified_read_value_error_returns_false(tmp_path):
    path = tmp_path / "quota-gate.json"
    path.write_text("{not json", encoding="utf-8")
    assert read_github_api_notified(path) is False


