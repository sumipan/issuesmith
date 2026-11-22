"""Refs #7816 — queue/recovery redispatch keys follow workflow definition name."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from issuesmith import queue as qmod
from issuesmith import recovery as rmod
from issuesmith.config import get_config, reset_config_cache


def _write_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    label_namespace: str,
    workflow_yaml: str | None,
    workflow_rel: str = "workflows/wf.yml",
    exec_rel: str = "exec.jsonl",
) -> Path:
    host = tmp_path / "host"
    host.mkdir()
    workflow_path = host / workflow_rel
    if workflow_yaml is not None:
        workflow_path.parent.mkdir(parents=True, exist_ok=True)
        workflow_path.write_text(workflow_yaml, encoding="utf-8")
    payload: dict[str, Any] = {
        "repo": "example/app",
        "label_namespace": label_namespace,
        "paths": {
            "workflow": workflow_rel,
            "exec_jsonl": exec_rel,
        },
    }
    cfg_path = host / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    return host


def _write_exec(path: Path, keys: list[str]) -> None:
    lines = [
        json.dumps({"uuid": f"u-{i}", "idempotency_key": k})
        for i, k in enumerate(keys)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _clear_config() -> Any:
    reset_config_cache()
    yield
    reset_config_cache()


def test_handler_key_consumed_uses_workflow_name_from_definition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _write_host(
        tmp_path,
        monkeypatch,
        label_namespace="develop",
        workflow_yaml="name: develop\nhandlers: {}\n",
    )
    exec_path = host / "exec.jsonl"
    _write_exec(exec_path, ["develop:develop:7790"])
    monkeypatch.setattr(qmod, "EXEC_PATH", exec_path)
    assert qmod._handler_key_consumed("develop", 7790) is True


def test_redispatch_command_uses_workflow_name_from_definition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_host(
        tmp_path,
        monkeypatch,
        label_namespace="develop",
        workflow_yaml="name: develop\nhandlers: {}\n",
    )
    calls: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        qmod.subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)) or _Proc()
    )
    monkeypatch.setattr(qmod, "EXEC_PATH", tmp_path / "unused.jsonl")
    assert qmod._trigger_ghdag_redispatch(7790, "develop", reason="recovery") == 0
    assert calls[0][calls[0].index("--workflow") + 1] == "develop"


def test_recovery_idempotency_key_uses_workflow_name_from_definition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_host(
        tmp_path,
        monkeypatch,
        label_namespace="develop",
        workflow_yaml="name: develop\nhandlers: {}\n",
    )
    assert rmod._idempotency_key("develop", 7790) == "develop:develop:7790"
    assert rmod._idempotency_key("develop", 7790, 2) == "develop:develop:7790:2"


def test_workflow_name_falls_back_to_label_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_host(
        tmp_path,
        monkeypatch,
        label_namespace="develop",
        workflow_yaml=None,
        workflow_rel="workflows/missing.yml",
    )
    assert get_config().workflow_name() == "develop"


def test_issuesmith_named_workflow_keeps_key_and_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _write_host(
        tmp_path,
        monkeypatch,
        label_namespace="issuesmith",
        workflow_yaml="name: issuesmith\nhandlers: {}\n",
    )
    exec_path = host / "exec.jsonl"
    _write_exec(exec_path, ["issuesmith:impl:2980"])
    monkeypatch.setattr(qmod, "EXEC_PATH", exec_path)
    assert qmod._handler_key_consumed("impl", 2980) is True

    calls: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        qmod.subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)) or _Proc()
    )
    assert qmod._trigger_ghdag_redispatch(2980, "impl", reason="recovery") == 0
    assert calls[0][calls[0].index("--workflow") + 1] == "issuesmith"
