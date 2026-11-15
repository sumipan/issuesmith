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
    phases = [
        {
            "name": "draft",
            "role": "design",
            "entry_step": "b1",
            "handler": "brushup",
            "advance_when": ["deps_terminal"],
        },
        {
            "name": "develop",
            "role": "implementation",
            "entry_step": "cp2",
            "handler": "impl",
            "preconditions": ["draft-done"],
            "advance_when": ["deps_terminal"],
        },
        {
            "name": "merge",
            "role": "implementation",
            "entry_step": "m2",
            "handler": "merge",
            "advance_when": ["deps_terminal", "closing_pr_exists"],
        },
    ]
    cfg.write_text(yaml.safe_dump({"repo": "example/repo", "phases": phases}), encoding="utf-8")
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


def _write_phases(tmp_path: Path, phases: list[dict] | None) -> None:
    data: dict = {"repo": "example/repo"}
    if phases is not None:
        data["phases"] = phases
    (tmp_path / "issuesmith.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    reset_config_cache()


def test_phase_for_handler_returns_none_for_unknown_or_undeclared_phase(env):
    assert resume_mod._phase_for_handler("migrate") is None  # no migrate phase
    assert resume_mod._phase_for_handler("unknown") is None
    assert resume_mod._phase_for_handler("subissue") is None  # env has no sub phase


def test_restore_running_state_warns_when_phase_is_unknown(env, capsys):
    _tmp_path, client, number = env
    before = {lb["name"] for lb in client.issue_get(number, fields=["labels"])["labels"]}

    resume_mod._restore_running_state(number, "unknown")

    assert "resume: no phase for handler unknown; labels not restored" in capsys.readouterr().err
    after = {lb["name"] for lb in client.issue_get(number, fields=["labels"])["labels"]}
    assert after == before
    assert QueueStore().snapshot().in_flight == []
