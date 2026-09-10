"""steps: 設定 — 無設定は現行動作、カスタムは dispatch に反映。"""

from __future__ import annotations

import importlib
from pathlib import Path

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


_DEFAULT_M2 = StepConfig(
    module="issuesmith.steps.m2_finalize",
    template="m2-compact.md",
)


def _write_config(tmp_path, monkeypatch, payload: dict) -> None:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def test_default_steps_resolve_m2_role_dispatch(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"repo": "example/app"})
    cfg = load_config()

    assert "m2-role-dispatch" in cfg.steps
    assert cfg.steps["m2-role-dispatch"] == _DEFAULT_M2

    resolved = dispatch_mod.resolve_step_config("m2-role-dispatch")
    assert resolved.module == "issuesmith.steps.m2_finalize"
    assert resolved.template == "m2-compact.md"


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
    # 既定エントリは部分上書きでも維持
    assert cfg.steps["m2-role-dispatch"] == _DEFAULT_M2

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


def test_unknown_step_falls_back_to_hyphen_module(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"repo": "example/app"})
    resolved = dispatch_mod.resolve_step_config("some-new-step")
    assert resolved.module == "issuesmith.steps.some_new_step"
    assert resolved.template is None


def test_m2_finalize_has_no_m2_compact_literal():
    src = Path(__file__).resolve().parents[1] / "src/issuesmith/steps/m2_finalize.py"
    text = src.read_text(encoding="utf-8")
    assert "m2-compact.md" not in text
