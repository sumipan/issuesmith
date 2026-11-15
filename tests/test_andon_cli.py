"""Tests for andon CLI subcommands and resume hook behavior (#3509)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_andon(
    *,
    andon_id: str = "issuesmith:42:cp2:0",
    kind: str = "decision",
    issue: int = 42,
    step: str = "cp2",
    summary: str = "test andon",
):
    from issuesmith.andon import Andon
    return Andon(
        id=andon_id,
        kind=kind,
        issue=issue,
        step=step,
        summary=summary,
    )


def _fake_client_with_andon(andon):
    from issuesmith.andon import to_comment
    client = MagicMock()
    comment_body = to_comment(andon)
    client.get_issue_comments.return_value = [{"body": comment_body, "id": 1}]
    client.list_issues.return_value = [{"number": andon.issue}]
    client.issue_comment.return_value = {}
    client.issue_update.return_value = {}
    return client


# ---------------------------------------------------------------------------
# andon CLI list
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _call_resume_hook: resume action → resume() called, dispatch_event not used
# ---------------------------------------------------------------------------

def test_call_resume_hook_handoff_no_dispatch_event():
    """handoff action must not call dispatch_event."""
    from issuesmith.andon import _call_resume_hook
    client = MagicMock()
    _call_resume_hook(client, "issuesmith:42:cp2:0", "handoff")
    client.dispatch_event.assert_not_called()


def test_call_resume_hook_resume_exception_suppressed():
    """Exceptions inside resume must not propagate (same contract as before)."""
    from issuesmith.andon import _call_resume_hook
    client = MagicMock()
    with patch("issuesmith.andon.resume", side_effect=RuntimeError("oops")):
        # Should not raise
        _call_resume_hook(client, "issuesmith:42:cp2:0", "resume")


def test_call_resume_hook_bad_andon_id_graceful():
    """Malformed andon_id must not raise."""
    from issuesmith.andon import _call_resume_hook
    client = MagicMock()
    with patch("issuesmith.andon.resume") as mock_resume:
        _call_resume_hook(client, "bad-id", "resume")
    mock_resume.assert_not_called()


# ---------------------------------------------------------------------------
# dispatch_event must not be called anywhere in andon.py (§3509 AC)
# ---------------------------------------------------------------------------

def test_dispatch_event_not_called_on_answer():
    """answer() must not call dispatch_event after #3509."""
    a = _make_andon()
    client = _fake_client_with_andon(a)
    with (
        patch("issuesmith.andon._iter_open_andon_issues") as mock_iter,
        patch("issuesmith.andon._write_metrics"),
        patch("issuesmith.resume.resume", return_value=0),
    ):
        mock_iter.return_value = iter([{"number": a.issue}])
        from issuesmith.andon import answer
        answer(client, a.id, "resume")
    client.dispatch_event.assert_not_called()


# ---------------------------------------------------------------------------
# AC-5 / AC-6: andon list sort order and --all flag
# ---------------------------------------------------------------------------

def _make_andons_mixed():
    """Return a list of andons in an unsorted order for testing default sort."""
    from issuesmith.andon import Andon
    return [
        Andon(id="observe:1:orphan:uuid1:0", kind="blocked", issue=1, step="observe", summary="orphan"),
        Andon(id="issuesmith:2:cp2:0", kind="broken", issue=2, step="cp2", summary="broken thing"),
        Andon(id="observe:3:stall:develop:0", kind="blocked", issue=3, step="observe", summary="stall"),
        Andon(id="issuesmith:4:cp2:0", kind="decision", issue=4, step="cp2", summary="needs decision"),
        Andon(id="issuesmith:5:p2:0", kind="blocked", issue=5, step="p2", summary="blocked non-observe"),
    ]


def test_andon_list_default_sort_order():
    """AC-5: default list sorts decision → broken → non-observe blocked → observe blocked."""
    from io import StringIO
    from unittest.mock import MagicMock
    from unittest.mock import patch as mpatch

    from issuesmith.cli import _cmd_andon

    andons = _make_andons_mixed()
    fake_client = MagicMock()
    output = StringIO()

    with mpatch("issuesmith.andon.list_open", return_value=andons), \
         mpatch("ghdag.forge.get_forge", return_value=fake_client), \
         mpatch("sys.stdout", output):
        ret = _cmd_andon(["list"])

    assert ret == 0
    lines = [ln for ln in output.getvalue().splitlines() if ln.strip()]
    ids_in_output = [ln.split("\t")[0] for ln in lines]
    kinds_order = [ln.split("\t")[1] for ln in lines]
    steps_order = [next(a.step for a in andons if a.id == aid) for aid in ids_in_output]

    # decision must come first
    assert kinds_order[0] == "decision", f"expected decision first, got: {kinds_order}"
    # broken before non-observe blocked
    broken_idx = kinds_order.index("broken")
    non_obs_blocked_idx = next(
        i for i, (k, s) in enumerate(zip(kinds_order, steps_order))
        if k == "blocked" and s != "observe"
    )
    obs_blocked_indices = [
        i for i, (k, s) in enumerate(zip(kinds_order, steps_order))
        if k == "blocked" and s == "observe"
    ]
    assert broken_idx < non_obs_blocked_idx, "broken should precede non-observe blocked"
    assert all(i > non_obs_blocked_idx for i in obs_blocked_indices), (
        "observe blocked should come after non-observe blocked"
    )


def test_andon_list_all_flag_shows_unsorted():
    """AC-6: --all flag shows all andons without sorting (original order)."""
    from io import StringIO
    from unittest.mock import MagicMock
    from unittest.mock import patch as mpatch

    from issuesmith.cli import _cmd_andon

    andons = _make_andons_mixed()
    fake_client = MagicMock()
    output = StringIO()

    with mpatch("issuesmith.andon.list_open", return_value=andons), \
         mpatch("ghdag.forge.get_forge", return_value=fake_client), \
         mpatch("sys.stdout", output):
        ret = _cmd_andon(["list", "--all"])

    assert ret == 0
    lines = [ln for ln in output.getvalue().splitlines() if ln.strip()]
    ids_in_output = [ln.split("\t")[0] for ln in lines]
    ids_original = [a.id for a in andons]
    assert ids_in_output == ids_original, "--all should preserve original order"


# ---------------------------------------------------------------------------
# andon list --json / andon note (nexus #3678)
# ---------------------------------------------------------------------------

def _record(andon, *, raised_at: str = "2026-09-26T00:00:00Z", notes: dict | None = None) -> dict:
    from dataclasses import asdict
    return {**asdict(andon), "raised_at": raised_at, "notes": notes or {}}


def test_andon_list_json_all_keeps_order(capsys):
    import json

    from issuesmith.cli import _cmd_andon

    records = [_record(a) for a in _make_andons_mixed()]
    with patch("issuesmith.andon.list_open_records", return_value=records), \
         patch("ghdag.forge.get_forge", return_value=MagicMock()):
        ret = _cmd_andon(["list", "--all", "--json"])

    assert ret == 0
    assert [r["id"] for r in json.loads(capsys.readouterr().out)] == [r["id"] for r in records]


def test_andon_note_success(capsys):
    from issuesmith.cli import _cmd_andon

    client = MagicMock()
    with patch("issuesmith.andon.note") as mock_note, \
         patch("ghdag.forge.get_forge", return_value=client):
        ret = _cmd_andon(["note", "wf:1:cp2:0", "--key", "asked", "--value", "2026-09-26T00:00:00Z"])

    assert ret == 0
    mock_note.assert_called_once_with(client, "wf:1:cp2:0", "asked", "2026-09-26T00:00:00Z")
    assert "noted wf:1:cp2:0: asked=2026-09-26T00:00:00Z" in capsys.readouterr().out


def test_andon_note_not_found_exit_1(capsys):
    from issuesmith.cli import _cmd_andon

    with patch("issuesmith.andon.note", side_effect=KeyError("Andon not found: wf:1:cp2:0")), \
         patch("ghdag.forge.get_forge", return_value=MagicMock()):
        ret = _cmd_andon(["note", "wf:1:cp2:0", "--key", "k", "--value", "v"])

    assert ret == 1
    err = capsys.readouterr().err
    assert err.splitlines()[0].startswith("error[NOT_FOUND]:")
    assert "wf:1:cp2:0" in err


def test_show_missing_andon_is_not_found(capsys):
    """refs #6779: andon show on a missing id is NOT_FOUND."""
    from issuesmith.cli import _cmd_andon

    with patch("issuesmith.andon.list_open", return_value=[]), \
         patch("ghdag.forge.get_forge", return_value=MagicMock()):
        ret = _cmd_andon(["show", "wf:1:cp2:0"])

    assert ret == 1
    err = capsys.readouterr().err
    assert err.splitlines()[0].startswith("error[NOT_FOUND]:")


def test_andon_usage_mentions_json_and_note():
    from issuesmith.cli import _ANDON_USAGE

    assert "--json" in _ANDON_USAGE
    assert "note <andon-id>" in _ANDON_USAGE


def test_cli_answer_by_agent_records_agent(tmp_path_factory):
    """Refs sumipan/nexus#6611."""
    from issuesmith.cli import _cmd_andon

    andon = _make_andon(andon_id="wf:42:cp2:0", issue=42)
    client = _fake_client_with_andon(andon)
    metrics = tmp_path_factory.getbasetemp() / "metrics.jsonl"
    with (
        patch("ghdag.forge.get_forge", return_value=client),
        patch("issuesmith.andon.project_issue"),
        patch("issuesmith.andon._call_resume_hook"),
    ):
        assert _cmd_andon(["answer", andon.id, "handoff", "--by", "agent"]) == 0
    line = json.loads(metrics.read_text().strip().splitlines()[-1])
    assert line["answered_by"] == "agent"
    assert line["action"] == "handoff"


def test_andon_auto_answer_json_empty_rules(tmp_path, monkeypatch, capsys):
    import yaml

    from issuesmith.cli import _cmd_andon
    from issuesmith.config import reset_config_cache

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "example/repo"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    monkeypatch.setenv("GHDAG_FORGE", "local")
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(tmp_path / "forge"))
    reset_config_cache()
    with patch("ghdag.forge.get_forge", return_value=MagicMock(list_issues=lambda **k: [])):
        assert _cmd_andon(["auto-answer", "--json"]) == 0
    out = capsys.readouterr().out
    assert "answers" in out
    reset_config_cache()


def test_console_entry_runs_andon_list_json(monkeypatch, capsys):
    """AC-1 (#6697): cli.main runs andon list --json through the console entry table."""
    from issuesmith import cli

    records = [{"id": "a1", "kind": "decision", "issue": 1, "step": "cp2", "summary": "s"}]
    with patch("ghdag.forge.get_forge", return_value=MagicMock()), \
         patch("issuesmith.andon.list_open_records", return_value=records):
        with pytest.raises(SystemExit) as exc:
            cli.main(["andon", "list", "--json"])
        assert exc.value.code == 0
    out = capsys.readouterr().out
    assert json.loads(out) == records


@pytest.mark.parametrize("cmd", ["andon", "labels"])
def test_module_and_console_entry_agree(cmd, capsys):
    """AC-2 (#6697): -m issuesmith and cli.main share usage for andon and labels."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    src_path = str(repo_root / "src")
    env = {**os.environ, "PYTHONPATH": src_path}
    mod = subprocess.run(
        [sys.executable, "-m", "issuesmith", cmd],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    from issuesmith import cli

    with pytest.raises(SystemExit) as exc:
        cli.main([cmd])
    console_err = capsys.readouterr().err
    assert mod.returncode == 2
    assert exc.value.code == 2
    assert mod.stderr.strip() == console_err.strip()
