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


def _minimal_config(tmp_path: Path, monkeypatch, **metrics_kwargs) -> None:
    payload = {"repo": "example/app", "timezone": "Asia/Tokyo"}
    if metrics_kwargs:
        payload["metrics"] = metrics_kwargs
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
    def test_fixture_aggregation(self, tmp_path, monkeypatch):
        _minimal_config(tmp_path, monkeypatch, cause_targets={"tests.": "Tests gate"})
        from issuesmith.config import get_config

        cfg = get_config()
        result = compute(
            METRICS_FIXTURE,
            audit_path=AUDIT_FIXTURE,
            config=cfg,
        )
        assert result["skipped_rows"] == 1
        assert result["timezone"] == "Asia/Tokyo"
        assert len(result["weeks"]) >= 1

        week = next(w for w in result["weeks"] if w["week"] == "2026-W40")
        assert week["q1_first_pass_rate"] == 0.5
        assert week["done_issues"] == 2
        assert week["first_pass_issues"] == 1
        assert week["rework"] == {"rerun": 1, "repair": 1, "human": 1}
        assert week["noise"] == 1
        assert week["active_issues"] == 6
        assert week["andon_raised"] == 1
        assert week["q3_andon_rate"] == pytest.approx(1 / 6)
        assert week["cost_usd"] == 1.6
        assert week["unpriced"] == {"calls": 1, "tokens": 2000}
        assert week["by_step"]["p1"]["runs"] == 5
        assert week["by_step"]["p1"]["rerun"] == 1
        assert week["by_step"]["p1"]["noise"] == 1

        cause = next(c for c in week["by_cause"] if c["cause"] == "tests.pytest_failure")
        assert cause["target"] == "Tests gate"
        assert cause["count"] == 1
        assert 4402 in cause["issues"]

        issue_4402 = next(i for i in result["issues"] if i["issue"] == 4402)
        assert issue_4402["runs"]["p1"] == 1
        assert any(r["kind"] == "repair" for r in issue_4402["rework"])

    def test_duplicate_parent_uuid_counts_one_run(self, tmp_path, monkeypatch):
        _minimal_config(tmp_path, monkeypatch)
        from issuesmith.config import get_config

        result = compute(METRICS_FIXTURE, audit_path=AUDIT_FIXTURE, config=get_config())
        issue_4406 = next(i for i in result["issues"] if i["issue"] == 4406)
        assert issue_4406["runs"]["b1"] == 1


class TestTransientNoise:
    def test_timeout_rerun_is_noise_not_rerun(self, tmp_path, monkeypatch):
        _minimal_config(tmp_path, monkeypatch)
        from issuesmith.config import get_config

        metrics = tmp_path / "metrics.jsonl"
        audit = tmp_path / "audit.jsonl"
        metrics.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "event": "step_started",
                            "ts": "2026-09-28T10:00:00+00:00",
                            "parent_uuid": "u-a",
                            "issue": 5001,
                            "step": "p1",
                            "workflow": "impl",
                        }
                    ),
                    json.dumps(
                        {
                            "uuid": "u-a",
                            "engine": "claude",
                            "status": "failed",
                            "timestamp": "2026-09-28T10:01:00+00:00",
                            "parent_uuid": "u-a",
                            "failure_class": "TIMEOUT",
                        }
                    ),
                    json.dumps(
                        {
                            "event": "step_started",
                            "ts": "2026-09-28T11:00:00+00:00",
                            "parent_uuid": "u-b",
                            "issue": 5001,
                            "step": "p1",
                            "workflow": "impl",
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        audit.write_text(
            json.dumps(
                {
                    "event": "task_failed",
                    "uuid": "u-a",
                    "failure_class": "TIMEOUT",
                    "timestamp": "2026-09-28T10:01:00+00:00",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        result = compute(metrics, audit_path=audit, config=get_config())
        week = result["weeks"][0]
        assert week["rework"]["rerun"] == 0
        assert week["noise"] == 1

    def test_pipeline_failed_rerun_counts_as_rework(self, tmp_path, monkeypatch):
        _minimal_config(tmp_path, monkeypatch)
        from issuesmith.config import get_config

        metrics = tmp_path / "metrics.jsonl"
        audit = tmp_path / "audit.jsonl"
        metrics.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "event": "step_started",
                            "ts": "2026-09-28T10:00:00+00:00",
                            "parent_uuid": "u-a",
                            "issue": 5002,
                            "step": "p1",
                            "workflow": "impl",
                        }
                    ),
                    json.dumps(
                        {
                            "uuid": "u-a",
                            "engine": "claude",
                            "status": "failed",
                            "timestamp": "2026-09-28T10:01:00+00:00",
                            "parent_uuid": "u-a",
                            "failure_class": "PIPELINE_FAILED",
                        }
                    ),
                    json.dumps(
                        {
                            "event": "step_started",
                            "ts": "2026-09-28T11:00:00+00:00",
                            "parent_uuid": "u-b",
                            "issue": 5002,
                            "step": "p1",
                            "workflow": "impl",
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        audit.write_text(
            json.dumps(
                {
                    "event": "task_failed",
                    "uuid": "u-a",
                    "failure_class": "PIPELINE_FAILED",
                    "timestamp": "2026-09-28T10:01:00+00:00",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        result = compute(metrics, audit_path=audit, config=get_config())
        week = result["weeks"][0]
        assert week["rework"]["rerun"] == 1
        assert week["noise"] == 0


class TestCauseTargets:
    def test_prefix_match_and_null_target(self, tmp_path, monkeypatch):
        _minimal_config(
            tmp_path,
            monkeypatch,
            cause_targets={"tests.": "Tests gate", "cp2.": "CP2"},
        )
        from issuesmith.config import get_config

        result = compute(METRICS_FIXTURE, audit_path=AUDIT_FIXTURE, config=get_config())
        week = next(w for w in result["weeks"] if w["week"] == "2026-W40")
        by_cause = {c["cause"]: c for c in week["by_cause"]}
        assert by_cause["tests.pytest_failure"]["target"] == "Tests gate"
        assert by_cause["failure:PIPELINE_FAILED"]["target"] is None


class TestConfig:
    def test_defaults_when_metrics_section_missing(self, tmp_path, monkeypatch):
        _minimal_config(tmp_path, monkeypatch)
        cfg = load_config()
        assert cfg.metrics.done_step == "m2"
        assert cfg.metrics.repair_templates == ()
        assert cfg.metrics.cause_targets == {}

    def test_unknown_metrics_key_raises(self, tmp_path, monkeypatch):
        _write_config(tmp_path, monkeypatch, {"repo": "example/app", "metrics": {"extra": 1}})
        with pytest.raises(ConfigError, match="unknown keys"):
            load_config()


class TestCli:
    def test_json_output(self, tmp_path, monkeypatch):
        _minimal_config(tmp_path, monkeypatch, cause_targets={"tests.": "Tests gate"})
        metrics = tmp_path / "jobs" / "metrics.jsonl"
        metrics.parent.mkdir(parents=True)
        metrics.write_text(METRICS_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        cfg_path = Path(__import__("os").environ["ISSUESMITH_CONFIG"])
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        data.setdefault("paths", {})["metrics"] = "jobs/metrics.jsonl"
        cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")

        proc = _run_cli(
            "--json",
            "--audit",
            str(AUDIT_FIXTURE),
            cwd=tmp_path,
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        week = next(w for w in payload["weeks"] if w["week"] == "2026-W40")
        assert week["q1_first_pass_rate"] == 0.5

    def test_missing_metrics_file_exit_1(self, tmp_path, monkeypatch):
        _minimal_config(tmp_path, monkeypatch)
        proc = _run_cli(cwd=tmp_path)
        assert proc.returncode == 1
        assert "metrics" in proc.stderr.lower()

    def test_invalid_since_exit_1(self, tmp_path, monkeypatch):
        _minimal_config(tmp_path, monkeypatch)
        metrics = tmp_path / "jobs" / "metrics.jsonl"
        metrics.parent.mkdir(parents=True)
        metrics.write_text("{}\n", encoding="utf-8")
        proc = _run_cli("--since", "2026-13-01", cwd=tmp_path)
        assert proc.returncode == 1
        assert "since" in proc.stderr.lower()

    def test_text_output(self, tmp_path, monkeypatch):
        _minimal_config(tmp_path, monkeypatch)
        metrics = tmp_path / "jobs" / "metrics.jsonl"
        metrics.parent.mkdir(parents=True)
        metrics.write_text(METRICS_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        proc = _run_cli("--audit", str(AUDIT_FIXTURE), cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert "2026-W40" in proc.stdout
        assert "Q1" in proc.stdout


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
