"""Tests for issuesmith metrics rework aggregation (#4431)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from issuesmith.config import ConfigError, load_config, reset_config_cache
from issuesmith.metrics_rework import compute, format_text, load_jsonl, parse_since

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "metrics_rework"
METRICS_FIXTURE = FIXTURES / "metrics.jsonl"
AUDIT_FIXTURE = FIXTURES / "audit.jsonl"


@pytest.fixture(autouse=True)
def _clear_config_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _write_config(tmp_path: Path, monkeypatch, payload: dict) -> Path:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    return cfg_path


_MINIMAL_PHASES = [
    {"name": "draft", "role": "design", "entry_step": "b1", "handler": "brushup"},
]


def _minimal_config(tmp_path: Path, monkeypatch, **metrics_kwargs) -> None:
    payload = {
        "repo": "example/app",
        "timezone": "Asia/Tokyo",
        "phases": _MINIMAL_PHASES,
        "metrics": {"done_step": "m2", **metrics_kwargs},
    }
    _write_config(tmp_path, monkeypatch, payload)


def _run_cli(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "issuesmith", "metrics", "rework", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env={**dict(__import__("os").environ), "PYTHONPATH": str(REPO_ROOT / "src")},
    )


class TestComputeFixture:

    def test_duplicate_parent_uuid_counts_one_run(self, tmp_path, monkeypatch):
        _minimal_config(tmp_path, monkeypatch)
        from issuesmith.config import get_config

        result = compute(METRICS_FIXTURE, audit_path=AUDIT_FIXTURE, config=get_config())
        issue_4406 = next(i for i in result["issues"] if i["issue"] == 4406)
        assert issue_4406["runs"]["b1"] == 1


class TestConfig:
    def test_defaults_when_metrics_section_missing(self, tmp_path, monkeypatch):
        _write_config(tmp_path, monkeypatch, {"repo": "example/app"})
        cfg = load_config()
        assert cfg.metrics.done_step is None
        assert cfg.metrics.repair_templates == ()
        assert cfg.metrics.cause_targets == {}

    def test_compute_requires_declared_done_step(self, tmp_path, monkeypatch):
        _write_config(tmp_path, monkeypatch, {"repo": "example/app"})
        from issuesmith.config import get_config

        with pytest.raises(ConfigError, match="metrics.done_step"):
            compute(METRICS_FIXTURE, config=get_config())

    def test_null_done_step_is_undeclared(self, tmp_path, monkeypatch):
        _write_config(
            tmp_path, monkeypatch, {"repo": "example/app", "metrics": {"done_step": None}}
        )
        assert load_config().metrics.done_step is None

    def test_cause_targets_normalize_legacy_prefix(self, tmp_path, monkeypatch):
        _minimal_config(
            tmp_path,
            monkeypatch,
            cause_targets={"cp1.": "Design gate"},
        )
        from issuesmith.config import get_config
        from issuesmith.metrics_rework import resolve_cause_target

        cfg = get_config()
        assert cfg.metrics.cause_targets == {"design_lint.": "Design gate"}
        assert resolve_cause_target("cp1.foo", cfg.metrics.cause_targets) == "Design gate"
        assert resolve_cause_target("design_lint.foo", cfg.metrics.cause_targets) == "Design gate"

    def test_unknown_metrics_key_raises(self, tmp_path, monkeypatch):
        _write_config(tmp_path, monkeypatch, {"repo": "example/app", "metrics": {"extra": 1}})
        with pytest.raises(ConfigError) as exc_info:
            load_config()
        assert exc_info.value.code == "CONFIG_UNKNOWN_KEY"
        assert "metrics.extra" in exc_info.value.message


class TestCli:

    def test_missing_metrics_file_exit_1(self, tmp_path, monkeypatch):
        _minimal_config(tmp_path, monkeypatch)
        proc = _run_cli(cwd=tmp_path)
        assert proc.returncode == 1
        assert "metrics" in proc.stderr.lower()


class TestHelpers:
    def test_load_jsonl(self, tmp_path):
        path = tmp_path / "rows.jsonl"
        path.write_text('{"a": 1}\n\n{"b": 2}\n', encoding="utf-8")
        assert load_jsonl(path) == [{"a": 1}, {"b": 2}]

    def test_parse_since_valid(self):
        from zoneinfo import ZoneInfo

        dt = parse_since("2026-09-28", ZoneInfo("Asia/Tokyo"))
        assert dt.year == 2026 and dt.month == 9 and dt.day == 28

    def test_parse_since_invalid(self):
        from zoneinfo import ZoneInfo

        with pytest.raises(ValueError):
            parse_since("2026-13-01", ZoneInfo("Asia/Tokyo"))

    def test_format_text_includes_week(self):
        text = format_text(
            {
                "weeks": [
                    {
                        "week": "2026-W40",
                        "start": "2026-09-28",
                        "q1_first_pass_rate": 0.5,
                        "q2_rework_per_issue": 0.5,
                        "q3_andon_rate": 0.1,
                        "q4_cost_per_issue_usd": 1.0,
                        "by_cause": [{"cause": "tests.fail", "target": "T", "count": 2}],
                    }
                ]
            }
        )
        assert "2026-W40" in text
        assert "tests.fail" in text


class TestCostAttribution:
    def _rows(self) -> list[dict]:
        return [
            {"event": "step_started", "ts": "2026-09-28T01:00:00+00:00", "parent_uuid": "u-1", "issue": 6001, "step": "p1"},
            {"uuid": "l-1", "engine": "claude", "status": "success", "timestamp": "2026-09-28T01:01:00+00:00", "cost_usd": 1.0, "parent_uuid": "u-1"},
            {"event": "step_started", "ts": "2026-10-05T01:00:00+00:00", "parent_uuid": "u-2", "issue": 6001, "step": "m2"},
            {"uuid": "l-2", "engine": "claude", "status": "success", "timestamp": "2026-10-05T01:01:00+00:00", "cost_usd": 2.0, "parent_uuid": "u-2"},
            {"event": "step_started", "ts": "2026-09-28T02:00:00+00:00", "parent_uuid": "u-3", "issue": 6002, "step": "p1"},
            {"uuid": "l-3", "engine": "claude", "status": "success", "timestamp": "2026-09-28T02:01:00+00:00", "cost_usd": 4.0, "parent_uuid": "u-3"},
        ]


    def test_issue_filter_keeps_llm_rows_without_issue(self, tmp_path, monkeypatch):
        _minimal_config(tmp_path, monkeypatch)
        from issuesmith.config import get_config

        metrics = tmp_path / "metrics.jsonl"
        metrics.write_text("\n".join(json.dumps(r) for r in self._rows()) + "\n", encoding="utf-8")
        result = compute(metrics, config=get_config(), issue_filter=6001)
        assert [i["issue"] for i in result["issues"]] == [6001]
        assert result["issues"][0]["cost_usd"] == pytest.approx(3.0)


