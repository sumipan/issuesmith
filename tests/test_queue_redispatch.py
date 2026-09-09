"""queue が ready ラベル付与時に ghdag の世代を上げる経路の検証。

2026-09-09、#2980 の CP2 FAIL 復旧で `redispatch` → queue dispatch → `develop-ready` 付与
まで進んだのに、ghdag watcher が「dispatch skipped (already dispatched) key=issuesmith:impl:2980」
で起動しなかった。冪等キーが消費済みの場合は `ghdag trigger --redispatch` 相当が必要。
"""
from __future__ import annotations

import json

from issuesmith import queue as qmod


def _write_exec(path, keys: list[str]) -> None:
    lines = [json.dumps({"uuid": f"u-{i}", "idempotency_key": k}) for i, k in enumerate(keys)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_key_consumed_detects_base_and_generation_keys(tmp_path, monkeypatch):
    exec_path = tmp_path / "exec.jsonl"
    monkeypatch.setattr(qmod, "EXEC_PATH", exec_path)
    assert qmod._handler_key_consumed("impl", 2980) is False  # exec.jsonl なし

    _write_exec(exec_path, ["issuesmith:brushup:2980", "issuesmith:impl:2967:1"])
    assert qmod._handler_key_consumed("brushup", 2980) is True
    assert qmod._handler_key_consumed("impl", 2967) is True  # 世代付きキーのみ
    assert qmod._handler_key_consumed("impl", 2980) is False
    assert qmod._handler_key_consumed("merge", 2967) is False


def test_trigger_ghdag_redispatch_builds_generation_bump_command(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(qmod, "EXEC_PATH", tmp_path / "exec.jsonl")
    monkeypatch.setattr(
        qmod.subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)) or _Proc()
    )
    rc = qmod._trigger_ghdag_redispatch(2980, "impl", reason="queue request abc (recovery)")
    assert rc == 0
    cmd = calls[0]
    assert cmd[1:4] == ["-m", "ghdag", "trigger"]
    assert "2980" in cmd and "--handler" in cmd and cmd[cmd.index("--handler") + 1] == "impl"
    assert "--redispatch" in cmd
    assert cmd[cmd.index("--workflow") + 1] == "issuesmith"
    assert cmd[cmd.index("--exec-md") + 1] == str(tmp_path / "exec.jsonl")


def test_phase_handler_map_matches_workflow_triggers():
    assert qmod._PHASE_HANDLER == {
        "draft": "brushup",
        "develop": "impl",
        "merge": "merge",
        "sub": "subissue",
    }
