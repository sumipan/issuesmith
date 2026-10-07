"""steps: config — unset keeps current behavior; custom values apply to dispatch."""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest
import yaml

from issuesmith.config import StepConfig, get_config, load_config, reset_config_cache
from issuesmith.ops import dispatch as dispatch_mod


@pytest.fixture(autouse=True)
def _clear_config_cache():
    reset_config_cache()
    yield
    reset_config_cache()
    # restore dispatch override used by legacy tests / this module
    dispatch_mod._STEP_MODULES = None


def _write_config(tmp_path, monkeypatch, payload: dict) -> None:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def test_default_steps_has_no_m2_role_dispatch(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"repo": "example/app"})
    cfg = load_config()

    assert "m2-role-dispatch" not in cfg.steps


def test_custom_step_module_loaded_by_dispatch(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {
                "custom-step": {"module": "issuesmith.steps.custom"},
            },
        },
    )
    cfg = get_config()
    assert cfg.steps["custom-step"] == StepConfig(
        module="issuesmith.steps.custom",
        template=None,
    )

    loaded: dict[str, object] = {}

    class FakeResult:
        exit_code = 0
        pipeline_status = "OK"
        recovery = None

    def fake_run(ctx, step=None):
        loaded["step"] = step
        loaded["module"] = "issuesmith.steps.custom"
        return FakeResult()

    fake_mod = type("mod", (), {"run": staticmethod(fake_run)})

    def fake_import(name: str):
        loaded["imported"] = name
        if name == "issuesmith.steps.custom":
            return fake_mod
        raise ImportError(name)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    monkeypatch.setattr(dispatch_mod.importlib, "import_module", fake_import)

    rc = dispatch_mod.main(
        [
            "custom-step",
            "issue_number=1",
            "base_branch=main",
            "handler_name=x",
            "is_cross_repo=false",
            "target_clone_path=",
            "source=",
            "workflow_name=issuesmith",
            "m1_result_filename=",
            "m1r_result_filename=",
        ]
    )
    assert rc == 0
    assert loaded["imported"] == "issuesmith.steps.custom"
    assert loaded["step"] == StepConfig(module="issuesmith.steps.custom", template=None)


def test_unknown_step_returns_empty_config_without_warning(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"repo": "example/app"})
    resolved = dispatch_mod.resolve_step_config("some-new-step")
    assert resolved.module == ""
    assert resolved.template is None


def test_unregistered_step_runs_bash_template(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"repo": "example/app"})
    called: dict[str, object] = {}

    def fake_bash(step_id: str, context: dict[str, str]) -> int:
        called["step_id"] = step_id
        called["context"] = context
        return 0

    monkeypatch.setattr(dispatch_mod, "_run_bash_step", fake_bash)

    rc = dispatch_mod.main(
        [
            "some-new-step",
            "issue_number=1",
            "base_branch=main",
            "handler_name=x",
            "is_cross_repo=false",
            "target_clone_path=",
            "source=",
            "workflow_name=issuesmith",
            "m1_result_filename=",
            "m1r_result_filename=",
        ]
    )
    assert rc == 0
    assert called["step_id"] == "some-new-step"


def test_unregistered_step_missing_template_raises_andon(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"repo": "example/app"})
    with patch.object(dispatch_mod, "map_step_result", return_value=1) as mock_map:
        rc = dispatch_mod.main(
            [
                "missing-template-step",
                "issue_number=1",
                "base_branch=main",
                "handler_name=x",
                "is_cross_repo=false",
                "target_clone_path=",
                "source=",
                "workflow_name=issuesmith",
                "m1_result_filename=",
                "m1r_result_filename=",
            ]
        )
    assert rc == 1
    result = mock_map.call_args[0][0]
    assert result.status == "andon"
    assert result.andon is not None
    assert "not in config.steps" in result.andon.summary
    assert "missing-template-step" in result.andon.summary


def test_registered_step_import_failure_raises_andon(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {
                "broken-step": {"module": "nonexistent.module"},
            },
        },
    )

    def fake_import(name: str):
        raise ImportError(f"No module named {name!r}")

    monkeypatch.setattr(importlib, "import_module", fake_import)
    monkeypatch.setattr(dispatch_mod.importlib, "import_module", fake_import)

    with patch.object(dispatch_mod, "map_step_result", return_value=1) as mock_map:
        rc = dispatch_mod.main(
            [
                "broken-step",
                "issue_number=1",
                "base_branch=main",
                "handler_name=x",
                "is_cross_repo=false",
                "target_clone_path=",
                "source=",
                "workflow_name=issuesmith",
                "m1_result_filename=",
                "m1r_result_filename=",
            ]
        )
    assert rc == 1
    result = mock_map.call_args[0][0]
    assert result.status == "andon"
    assert result.andon is not None
    assert "could not be imported" in result.andon.summary
    assert "broken-step" in result.andon.summary
    assert "nonexistent.module" in result.andon.summary


def test_registered_step_missing_run_raises_andon(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {
                "no-run-step": {"module": "mod.without.run"},
            },
        },
    )

    fake_mod = type("mod", (), {})

    def fake_import(name: str):
        if name == "mod.without.run":
            return fake_mod
        raise ImportError(name)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    monkeypatch.setattr(dispatch_mod.importlib, "import_module", fake_import)

    with patch.object(dispatch_mod, "map_step_result", return_value=1) as mock_map:
        rc = dispatch_mod.main(
            [
                "no-run-step",
                "issue_number=1",
                "base_branch=main",
                "handler_name=x",
                "is_cross_repo=false",
                "target_clone_path=",
                "source=",
                "workflow_name=issuesmith",
                "m1_result_filename=",
                "m1r_result_filename=",
            ]
        )
    assert rc == 1
    result = mock_map.call_args[0][0]
    assert result.status == "andon"
    assert result.andon is not None
    assert "has no run()" in result.andon.summary
    assert "no-run-step" in result.andon.summary


def test_step_without_module_returns_andon_broken(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {
                "llm-only": {"module": None, "template": "design.md"},
            },
        },
    )
    with patch.object(dispatch_mod, "map_step_result", return_value=1) as mock_map:
        rc = dispatch_mod.main(
            [
                "llm-only",
                "issue_number=1",
                "base_branch=main",
                "handler_name=x",
                "is_cross_repo=false",
                "target_clone_path=",
                "source=",
                "workflow_name=issuesmith",
                "m1_result_filename=",
                "m1r_result_filename=",
            ]
        )
    assert rc == 1
    result = mock_map.call_args[0][0]
    assert result.status == "andon"
    assert result.andon is not None
    assert "no module" in result.andon.summary
