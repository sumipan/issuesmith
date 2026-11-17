"""Doctor CLI JSON output (#6779 / parent #6701)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import yaml

from issuesmith.cli import _cmd_doctor
from issuesmith.config import reset_config_cache
from issuesmith.lines import extract_line


def _failing_installed_ghdag() -> tuple[bool, str]:
    return False, "fail"


_failing_installed_ghdag.__name__ = "_check_installed_ghdag"


def test_doctor_json_fail_exits_1(capsys) -> None:
    """refs #6779: any failed check yields exit code 1 in JSON mode."""
    with patch(
        "issuesmith.ops.preflight._PREFLIGHT_CHECKS",
        (_failing_installed_ghdag,),
    ):
        rc = _cmd_doctor(["--json"])
    assert rc == 1
    rows = json.loads(capsys.readouterr().out)
    assert any(row.get("ok") is False for row in rows)


def test_doctor_step_modules_ok_for_extracted_config(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """refs #7197 AC3: doctor step_modules passes for extracted reference config."""
    dest = tmp_path / "host"
    extract_line("develop", dest)
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(dest / "issuesmith.yaml"))
    reset_config_cache()
    from issuesmith.ops import preflight as preflight_mod

    with patch.object(
        preflight_mod,
        "_PREFLIGHT_CHECKS",
        (preflight_mod._check_step_modules,),
    ):
        rc = _cmd_doctor(["--json"])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    row = next(r for r in rows if r.get("name") == "step_modules")
    assert row["ok"] is True
    detail = str(row["detail"])
    assert detail == "step_modules: ok"
    cfg = yaml.safe_load((dest / "issuesmith.yaml").read_text(encoding="utf-8"))
    for step in cfg.get("steps", {}).values():
        mod = step.get("module", "")
        if mod:
            assert mod.startswith("issuesmith.lines.develop.")


def test_doctor_step_modules_fail_names_step(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """refs #7197 AC4: doctor step_modules names a broken module."""
    dest = tmp_path / "host"
    extract_line("develop", dest)
    cfg_path = dest / "issuesmith.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg["steps"]["prepare"]["module"] = "issuesmith.nosuch.fake_module"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    from issuesmith.ops import preflight as preflight_mod

    with patch.object(
        preflight_mod,
        "_PREFLIGHT_CHECKS",
        (preflight_mod._check_step_modules,),
    ):
        rc = _cmd_doctor(["--json"])
    assert rc == 1
    rows = json.loads(capsys.readouterr().out)
    row = next(r for r in rows if r.get("name") == "step_modules")
    assert row["ok"] is False
    detail = str(row["detail"])
    assert "prepare" in detail
    assert "issuesmith.nosuch.fake_module" in detail
