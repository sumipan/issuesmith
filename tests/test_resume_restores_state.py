"""resume --from restores what a running DAG needs: results cleared, running label, in_flight.

Real LocalForge and a real QueueStore; only ghdag recover itself is replaced by a recorder.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from ghdag.forge import get_forge
from ghdag.status import StepStatus

from issuesmith import resume as resume_mod
from issuesmith.config import reset_config_cache
from issuesmith.queue_store import QueueStore


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "issuesmith.yaml"
    cfg.write_text(yaml.safe_dump({"repo": "example/repo"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg))
    monkeypatch.setenv("ISSUESMITH_QUEUE_DIR", str(tmp_path / "queue"))
    (tmp_path / "queue").mkdir()
    monkeypatch.setenv("GHDAG_FORGE", "local")
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(tmp_path / "forge"))
    reset_config_cache()
    client = get_forge()
    number = client.issue_create("impl", "```yaml\ntarget_repo: example/repo\nallow_paths: [\"src/**\"]\n```\n")
    client.issue_update(number, labels_add=["issuesmith:draft-done", "issuesmith:develop-ready"])
    yield tmp_path, client, number
    reset_config_cache()


def _steps(tmp_path: Path) -> list[StepStatus]:
    results = {}
    for name in ("p1", "p2", "p3"):
        f = tmp_path / f"result-{name}.md"
        f.write_text(f"PIPELINE_STATUS: OLD_{name.upper()}\n", encoding="utf-8")
        results[name] = str(f)
    return [
        StepStatus("u-p1", "p1", [], "success", None, None, results["p1"]),
        StepStatus("u-p2", "p2", ["u-p1"], "failed", None, None, results["p2"]),
        StepStatus("u-p3", "p3", ["u-p2"], "dep_failed", None, None, results["p3"]),
    ]


def test_from_step_restores_label_in_flight_and_clears_downstream_results(env, monkeypatch):
    tmp_path, client, number = env
    steps = _steps(tmp_path)
    calls: list[tuple] = []
    monkeypatch.setattr(resume_mod, "_generation_keys_available", lambda: True)
    monkeypatch.setattr(resume_mod, "_load_step_statuses", lambda *a, **k: steps)
    monkeypatch.setattr(resume_mod, "_run_ghdag_recover", lambda *a, **k: calls.append(a) or 0)

    rc = resume_mod.resume(number, from_step="p2", handler="impl")

    assert rc == 0 and len(calls) == 1
    labels = {lb["name"] for lb in client.issue_get(number, fields=["labels"])["labels"]}
    assert "issuesmith:develop-running" in labels
    assert "issuesmith:develop-ready" not in labels
    assert "issuesmith:draft-done" in labels
    in_flight = QueueStore().snapshot().in_flight
    assert [e["issue"] for e in in_flight] == [number]
    assert in_flight[0]["role"] == "implementation"
    assert not (tmp_path / "result-p2.md").exists()  # rerun steps start from an empty result
    assert not (tmp_path / "result-p3.md").exists()
    assert (tmp_path / "result-p1.md").exists()  # upstream success untouched


def test_failed_recover_does_not_restore(env, monkeypatch):
    tmp_path, client, number = env
    monkeypatch.setattr(resume_mod, "_generation_keys_available", lambda: True)
    monkeypatch.setattr(resume_mod, "_load_step_statuses", lambda *a, **k: _steps(tmp_path))
    monkeypatch.setattr(resume_mod, "_run_ghdag_recover", lambda *a, **k: 1)

    rc = resume_mod.resume(number, from_step="p2", handler="impl")

    assert rc == 1
    labels = {lb["name"] for lb in client.issue_get(number, fields=["labels"])["labels"]}
    assert "issuesmith:develop-running" not in labels
    assert QueueStore().snapshot().in_flight == []


def test_succeeded_downstream_results_are_kept_without_force(env, monkeypatch):
    """--from p2 with p3 already succeeded: recover re-runs p2 only, p3 keeps its result."""
    tmp_path, client, number = env
    steps = _steps(tmp_path)
    steps[2] = StepStatus("u-p3", "p3", ["u-p2"], "success", None, None, steps[2].result_path)
    monkeypatch.setattr(resume_mod, "_generation_keys_available", lambda: True)
    monkeypatch.setattr(resume_mod, "_load_step_statuses", lambda *a, **k: steps)
    monkeypatch.setattr(resume_mod, "_run_ghdag_recover", lambda *a, **k: 0)

    assert resume_mod.resume(number, from_step="p2", handler="impl") == 0
    assert not (tmp_path / "result-p2.md").exists()
    assert (tmp_path / "result-p3.md").exists()


def test_force_resets_succeeded_markers_and_results(env, monkeypatch):
    """--from p2 --force: p2 and p3 lose their done markers and results even though they succeeded."""
    tmp_path, client, number = env
    from issuesmith.config import get_config

    steps = [
        StepStatus("u-p1", "p1", [], "success", None, None, str(tmp_path / "result-p1.md")),
        StepStatus("u-p2", "p2", ["u-p1"], "success", None, None, str(tmp_path / "result-p2.md")),
        StepStatus("u-p3", "p3", ["u-p2"], "success", None, None, str(tmp_path / "result-p3.md")),
    ]
    for st in steps:
        Path(st.result_path).write_text("PIPELINE_STATUS: OLD\n", encoding="utf-8")
    done_dir = get_config().paths.done_dir
    done_dir.mkdir(parents=True, exist_ok=True)
    for st in steps:
        (done_dir / st.uuid).write_text("0", encoding="utf-8")
    monkeypatch.setattr(resume_mod, "_generation_keys_available", lambda: True)
    monkeypatch.setattr(resume_mod, "_load_step_statuses", lambda *a, **k: steps)
    monkeypatch.setattr(resume_mod, "_run_ghdag_recover", lambda *a, **k: 0)

    assert resume_mod.resume(number, from_step="p2", handler="impl", force=True) == 0
    assert (done_dir / "u-p1").exists()
    assert not (done_dir / "u-p2").exists() and not (done_dir / "u-p3").exists()
    assert (tmp_path / "result-p1.md").exists()
    assert not (tmp_path / "result-p2.md").exists() and not (tmp_path / "result-p3.md").exists()
