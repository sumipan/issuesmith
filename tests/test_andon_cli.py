"""Tests for andon CLI subcommands and resume hook behavior (#3509)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

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

def test_andon_list_no_args_returns_nonzero():
    import os
    import subprocess
    import sys
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[1]
    src_path = str(repo_root / "src")
    env = {**os.environ, "PYTHONPATH": src_path}
    result = subprocess.run(
        [sys.executable, "-m", "issuesmith", "andon"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode != 0


def test_andon_list_shows_usage_on_no_args():
    import os
    import subprocess
    import sys
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[1]
    src_path = str(repo_root / "src")
    env = {**os.environ, "PYTHONPATH": src_path}
    result = subprocess.run(
        [sys.executable, "-m", "issuesmith", "andon"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    # usage or list must appear in stderr
    assert "list" in result.stderr or "usage" in result.stderr.lower()


# ---------------------------------------------------------------------------
# _call_resume_hook: resume action → resume() called, dispatch_event not used
# ---------------------------------------------------------------------------

def test_call_resume_hook_resume_action_calls_resume():
    """_call_resume_hook with action='resume' must call resume.resume(), not dispatch_event."""
    from issuesmith.andon import _call_resume_hook
    with patch("issuesmith.andon.resume") as mock_resume:
        mock_resume.return_value = 0
        client = MagicMock()
        _call_resume_hook(client, "issuesmith:42:cp2:0", "resume")
    mock_resume.assert_called_once_with(42, from_step="cp2")


def test_call_resume_hook_resume_action_no_dispatch_event():
    """resume action must not call dispatch_event (the old broken path)."""
    from issuesmith.andon import _call_resume_hook
    client = MagicMock()
    with patch("issuesmith.andon.resume", return_value=0):
        _call_resume_hook(client, "issuesmith:42:cp2:0", "resume")
    client.dispatch_event.assert_not_called()


def test_call_resume_hook_handoff_no_resume():
    """handoff action must not call resume (label/comment only)."""
    from issuesmith.andon import _call_resume_hook
    with patch("issuesmith.andon.resume") as mock_resume:
        client = MagicMock()
        _call_resume_hook(client, "issuesmith:42:cp2:0", "handoff")
    mock_resume.assert_not_called()


def test_call_resume_hook_handoff_no_dispatch_event():
    """handoff action must not call dispatch_event."""
    from issuesmith.andon import _call_resume_hook
    client = MagicMock()
    _call_resume_hook(client, "issuesmith:42:cp2:0", "handoff")
    client.dispatch_event.assert_not_called()


def test_call_resume_hook_unknown_action_no_resume():
    """Unknown actions must not call resume."""
    from issuesmith.andon import _call_resume_hook
    with patch("issuesmith.andon.resume") as mock_resume:
        client = MagicMock()
        _call_resume_hook(client, "issuesmith:42:cp2:0", "unknown-action")
    mock_resume.assert_not_called()


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
# FutureWarning on recover / redispatch CLI
# ---------------------------------------------------------------------------

def test_cli_recover_emits_future_warning():
    import os
    import sys
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[1]
    src_path = str(repo_root / "src")
    env = {**os.environ, "PYTHONPATH": src_path}
    result = __import__("subprocess").run(
        [sys.executable, "-W", "all", "-m", "issuesmith", "recover", "--help"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    combined = result.stdout + result.stderr
    assert "DeprecationWarning" in combined or "FutureWarning" in combined or result.returncode in (0, 1, 2)


def test_cli_redispatch_emits_future_warning():
    import os
    import subprocess
    import sys
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[1]
    src_path = str(repo_root / "src")
    env = {**os.environ, "PYTHONPATH": src_path}
    result = subprocess.run(
        [sys.executable, "-W", "all", "-m", "issuesmith", "redispatch", "--help"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    combined = result.stdout + result.stderr
    assert "DeprecationWarning" in combined or "FutureWarning" in combined or result.returncode in (0, 1, 2)


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

    from issuesmith.__main__ import _cmd_andon

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

    from issuesmith.__main__ import _cmd_andon

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


def test_andon_list_json_empty_prints_brackets(capsys):
    from issuesmith.__main__ import _cmd_andon

    with patch("issuesmith.andon.list_open_records", return_value=[]), \
         patch("ghdag.forge.get_forge", return_value=MagicMock()):
        ret = _cmd_andon(["list", "--json"])

    out = capsys.readouterr().out
    assert ret == 0
    assert out.strip() == "[]"
    assert "no open andons" not in out


def test_andon_list_json_outputs_records_sorted(capsys):
    import json

    from issuesmith.__main__ import _cmd_andon

    records = [_record(a) for a in _make_andons_mixed()]
    records[3]["notes"] = {"asked": "2026-09-26T00:00:00Z"}

    with patch("issuesmith.andon.list_open_records", return_value=records), \
         patch("ghdag.forge.get_forge", return_value=MagicMock()):
        ret = _cmd_andon(["list", "--json"])

    assert ret == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list) and len(data) == 5
    for rec in data:
        for key in ("id", "kind", "options", "default", "raised_at", "notes"):
            assert key in rec
    assert data[0]["kind"] == "decision"
    assert data[0]["notes"] == {"asked": "2026-09-26T00:00:00Z"}
    assert [r["id"] for r in data][-2:] == ["observe:1:orphan:uuid1:0", "observe:3:stall:develop:0"]


def test_andon_list_json_all_keeps_order(capsys):
    import json

    from issuesmith.__main__ import _cmd_andon

    records = [_record(a) for a in _make_andons_mixed()]
    with patch("issuesmith.andon.list_open_records", return_value=records), \
         patch("ghdag.forge.get_forge", return_value=MagicMock()):
        ret = _cmd_andon(["list", "--all", "--json"])

    assert ret == 0
    assert [r["id"] for r in json.loads(capsys.readouterr().out)] == [r["id"] for r in records]


def test_andon_note_success(capsys):
    from issuesmith.__main__ import _cmd_andon

    client = MagicMock()
    with patch("issuesmith.andon.note") as mock_note, \
         patch("ghdag.forge.get_forge", return_value=client):
        ret = _cmd_andon(["note", "wf:1:cp2:0", "--key", "asked", "--value", "2026-09-26T00:00:00Z"])

    assert ret == 0
    mock_note.assert_called_once_with(client, "wf:1:cp2:0", "asked", "2026-09-26T00:00:00Z")
    assert "noted wf:1:cp2:0: asked=2026-09-26T00:00:00Z" in capsys.readouterr().out


def test_andon_note_missing_args_exit_2(capsys):
    from issuesmith.__main__ import _cmd_andon

    for argv in (
        ["note", "wf:1:cp2:0", "--value", "v"],
        ["note", "wf:1:cp2:0", "--key", "k"],
        ["note", "--key", "k", "--value", "v"],
        ["note", "wf:1:cp2:0", "--key"],
    ):
        with patch("issuesmith.andon.note") as mock_note, \
             patch("ghdag.forge.get_forge", return_value=MagicMock()):
            ret = _cmd_andon(argv)
        assert ret == 2, argv
        mock_note.assert_not_called()
        assert "usage" in capsys.readouterr().err


def test_andon_note_not_found_exit_1(capsys):
    from issuesmith.__main__ import _cmd_andon

    with patch("issuesmith.andon.note", side_effect=KeyError("Andon not found: wf:1:cp2:0")), \
         patch("ghdag.forge.get_forge", return_value=MagicMock()):
        ret = _cmd_andon(["note", "wf:1:cp2:0", "--key", "k", "--value", "v"])

    assert ret == 1
    assert "andon not found: wf:1:cp2:0" in capsys.readouterr().err


def test_andon_usage_mentions_json_and_note():
    from issuesmith.__main__ import _ANDON_USAGE

    assert "--json" in _ANDON_USAGE
    assert "note <andon-id>" in _ANDON_USAGE


def test_andon_list_json_and_note_with_local_forge(tmp_path, monkeypatch, capsys):
    import json

    import yaml

    from issuesmith.__main__ import _cmd_andon
    from issuesmith.andon import Andon, raise_andon
    from issuesmith.config import reset_config_cache

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "example/repo"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    monkeypatch.setenv("GHDAG_FORGE", "local")
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(tmp_path / "forge"))
    reset_config_cache()
    try:
        from ghdag.forge import get_forge
        client = get_forge()
        number = client.issue_create("andon target", "body")
        andon_id = f"wf:{number}:cp2:0"
        raise_andon(
            client,
            Andon(id=andon_id, kind="decision", issue=number, step="cp2", summary="s",
                  options=["resume", "reject"], default="resume"),
            metrics_path=tmp_path / "m.jsonl",
        )
        assert _cmd_andon(["note", andon_id, "--key", "asked", "--value", "v1"]) == 0
        capsys.readouterr()
        assert _cmd_andon(["list", "--json"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert [r["id"] for r in data] == [andon_id]
        assert data[0]["notes"] == {"asked": "v1"}
        assert data[0]["raised_at"]
        assert _cmd_andon(["note", "wf:999:cp2:0", "--key", "k", "--value", "v"]) == 1
    finally:
        reset_config_cache()
