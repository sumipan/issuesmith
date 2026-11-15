"""Unit tests for issuesmith.andon — Andon dataclass, serialize/parse, raise/list/answer."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from issuesmith.andon import (
    Andon,
    answer,
    answer_if_open,
    from_comment,
    list_open,
    raise_andon,
    to_comment,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_andon(
    *,
    andon_id: str = "myflow:42:cp2:0",
    kind: str = "decision",
    issue: int = 42,
    step: str = "cp2",
    summary: str = "Should we proceed?",
    evidence: str = "test evidence",
    options: list[str] | None = None,
    default: str = "yes",
    mention: str = "@sumipan",
) -> Andon:
    return Andon(
        id=andon_id,
        kind=kind,
        issue=issue,
        step=step,
        summary=summary,
        evidence=evidence,
        options=options if options is not None else ["yes", "no"],
        default=default,
        mention=mention,
    )


class RecordingForgeClient:
    """Records forge issue_comment / issue_update calls for behaviour assertions."""

    def __init__(
        self,
        *,
        comments: list[dict] | None = None,
        issues: list[dict] | None = None,
        labels: list | None = None,
    ) -> None:
        self.issue_comments: list[tuple[int, str]] = []
        self.issue_updates: list[tuple[int, dict]] = []
        self.get_issue_comments_calls: list[int] = []
        self._stored_comments = comments or []
        self._issues = issues or []
        self.issue_get = MagicMock(return_value={"labels": labels or []})

    def issue_comment(self, issue: int, body: str) -> dict:
        self.issue_comments.append((issue, body))
        return {"id": 999, "body": body}

    def get_issue_comments(self, issue: int) -> list[dict]:
        self.get_issue_comments_calls.append(issue)
        return self._stored_comments

    def list_issues(self, **kwargs):
        return self._issues

    def issue_update(self, issue: int, **kwargs) -> dict:
        self.issue_updates.append((issue, kwargs))
        return {}


class RecordingSink:
    def __init__(self) -> None:
        self.emitted: list[Andon] = []

    def emit(self, andon: Andon) -> None:
        self.emitted.append(andon)


def _fake_client(
    *,
    comments: list[dict] | None = None,
    issues: list[dict] | None = None,
    labels: list | None = None,
) -> RecordingForgeClient:
    return RecordingForgeClient(comments=comments, issues=issues, labels=labels)


# ---------------------------------------------------------------------------
# Andon dataclass — basic construction
# ---------------------------------------------------------------------------

def test_andon_kind_blocked():
    a = _make_andon(kind="blocked")
    assert a.kind == "blocked"


def test_andon_kind_broken():
    a = _make_andon(kind="broken")
    assert a.kind == "broken"


# ---------------------------------------------------------------------------
# Serialize / parse (to_comment / from_comment)
# ---------------------------------------------------------------------------

def test_to_comment_returns_string():
    a = _make_andon()
    body = to_comment(a)
    assert isinstance(body, str)


def test_to_comment_contains_andon_fence():
    a = _make_andon()
    body = to_comment(a)
    assert "```andon" in body


def test_to_comment_contains_id():
    a = _make_andon()
    body = to_comment(a)
    assert "myflow:42:cp2:0" in body


def test_to_comment_contains_kind():
    a = _make_andon()
    body = to_comment(a)
    assert "decision" in body


def test_to_comment_omits_empty_namespace():
    a = _make_andon()
    assert "namespace:" not in to_comment(a)


def test_from_comment_without_namespace_defaults_empty():
    legacy = to_comment(_make_andon())
    parsed = from_comment(legacy)
    assert parsed is not None
    assert parsed.namespace == ""


def test_from_comment_no_andon_block():
    assert from_comment("just a regular comment") is None


def test_from_comment_empty_body():
    assert from_comment("") is None


def test_from_comment_other_fence():
    assert from_comment("```yaml\nfoo: bar\n```") is None


def test_round_trip_options_empty():
    a = _make_andon(options=[])
    body = to_comment(a)
    parsed = from_comment(body)
    assert parsed is not None
    assert parsed.options == []


# ---------------------------------------------------------------------------
# raise_andon
# ---------------------------------------------------------------------------

def _ns() -> str:
    from issuesmith.config import get_config
    return get_config().label_namespace


# ---------------------------------------------------------------------------
# list_open
# ---------------------------------------------------------------------------

def test_list_open_returns_empty_when_no_issues():
    client = _fake_client(issues=[])
    with patch("issuesmith.andon._andon_label_kinds", return_value=["decision", "blocked", "broken"]):
        result = list_open(client)
    assert result == []


def test_list_open_skips_comments_without_andon_block():
    client = _fake_client(
        issues=[{"number": 5}],
        comments=[{"body": "regular comment", "id": 1}],
    )
    with patch("issuesmith.andon._iter_open_andon_issues") as mock_iter:
        mock_iter.return_value = iter([{"number": 5}])
        client._stored_comments = [{"body": "regular comment", "id": 1}]
        result = list_open(client)
    assert result == []


# ---------------------------------------------------------------------------
# answer
# ---------------------------------------------------------------------------

def test_answer_rejects_unknown_answered_by(tmp_path):
    client = _fake_client()
    mfile = tmp_path / "metrics.jsonl"
    with pytest.raises(ValueError):
        answer(client, "wf:15:s:0", "handoff", metrics_path=mfile, answered_by="robot")
    assert not mfile.exists()
    assert client.issue_comments == []


def test_answer_raises_if_andon_not_found():
    client = _fake_client(comments=[{"body": "no andon here", "id": 1}])
    with (
        patch("issuesmith.andon._iter_open_andon_issues") as mock_iter,
    ):
        mock_iter.return_value = iter([{"number": 5}])
        client._stored_comments = [{"body": "no andon here", "id": 1}]
        with pytest.raises(KeyError):
            answer(client, "wf:5:s:0", "yes")


# ---------------------------------------------------------------------------
# AndonSink protocol
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# CLI via __main__
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# LocalForge round trip: list_open_records / note / answer (nexus #3678)
# ---------------------------------------------------------------------------

@pytest.fixture
def local_client(tmp_path, monkeypatch):
    import yaml
    from ghdag.forge import get_forge

    from issuesmith.config import reset_config_cache

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "example/repo"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    monkeypatch.setenv("GHDAG_FORGE", "local")
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(tmp_path / "forge"))
    reset_config_cache()
    yield get_forge()
    reset_config_cache()


def _raise_local(client, tmp_path, *, andon_id_suffix: str = "cp2:0", number: int | None = None) -> Andon:
    if number is None:
        number = client.issue_create("andon target", "body")
    a = _make_andon(
        andon_id=f"wf:{number}:{andon_id_suffix}",
        issue=number,
        options=["resume", "reject"],
        default="resume",
    )
    raise_andon(client, a, metrics_path=tmp_path / "metrics.jsonl")
    return a


def test_note_does_not_touch_labels(local_client, tmp_path):
    from issuesmith.andon import note

    a = _raise_local(local_client, tmp_path)
    with patch.object(local_client, "issue_update") as mock_update:
        note(local_client, a.id, "asked", "yes")
    mock_update.assert_not_called()


def test_answered_andon_excluded_when_sibling_is_open(local_client, tmp_path):
    from issuesmith.andon import list_open_records

    first = _raise_local(local_client, tmp_path, andon_id_suffix="cp2:0")
    second = _raise_local(local_client, tmp_path, andon_id_suffix="cp2:1", number=first.issue)
    with patch("issuesmith.andon._call_resume_hook"):
        answer(local_client, first.id, "resume", metrics_path=tmp_path / "metrics.jsonl")
    # answer() removed the shared label; put it back so the Issue is still listed.
    local_client.issue_update(first.issue, labels_add=[f"{_ns()}:andon-decision"])

    assert [x.id for x in list_open(local_client)] == [second.id]
    assert [r["id"] for r in list_open_records(local_client)] == [second.id]


def test_note_unknown_id_raises_key_error(local_client, tmp_path):
    from issuesmith.andon import note

    a = _raise_local(local_client, tmp_path)
    before = len(local_client.get_issue_comments(a.issue))
    with pytest.raises(KeyError):
        note(local_client, "wf:999:cp2:0", "asked", "yes")
    assert len(local_client.get_issue_comments(a.issue)) == before


def test_note_empty_key_raises_value_error(local_client, tmp_path):
    from issuesmith.andon import note

    a = _raise_local(local_client, tmp_path)
    before = len(local_client.get_issue_comments(a.issue))
    with pytest.raises(ValueError):
        note(local_client, a.id, "", "yes")
    assert len(local_client.get_issue_comments(a.issue)) == before


def test_andon_label_kinds_come_from_contract():
    from issuesmith.andon import _andon_label_kinds
    from issuesmith.contract import ANDON_KINDS

    assert _andon_label_kinds() == list(ANDON_KINDS)


# ---------------------------------------------------------------------------
# raise_andon holds the in_flight entry (#4816)
# ---------------------------------------------------------------------------

def _tmp_store(tmp_path):
    from issuesmith.queue_store import QueueStore

    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


@pytest.mark.parametrize("fail_on", ["init", "hold"])
def test_raise_andon_continues_when_queue_store_fails(tmp_path, caplog, fail_on):
    client = _fake_client()
    sink = RecordingSink()
    a = _make_andon(issue=10, kind="blocked")
    mfile = tmp_path / "metrics.jsonl"
    if fail_on == "init":
        store_patch = patch("issuesmith.andon.QueueStore", side_effect=OSError("boom"))
    else:
        broken = MagicMock()
        broken.hold_in_flight.side_effect = OSError("boom")
        store_patch = patch("issuesmith.andon.QueueStore", return_value=broken)
    with store_patch, caplog.at_level("WARNING", logger="issuesmith.andon"):
        raise_andon(client, a, sinks=[sink], metrics_path=mfile)
    warnings = [r for r in caplog.records if r.name == "issuesmith.andon"]
    assert len(warnings) == 1
    assert warnings[0].levelname == "WARNING"
    assert warnings[0].exc_info is not None
    assert len(client.issue_comments) == 1
    assert len(client.issue_updates) == 1
    assert f"{_ns()}:andon-blocked" in client.issue_updates[0][1].get("labels_add", [])
    assert sink.emitted == [a]
    assert json.loads(mfile.read_text().strip())["event"] == "andon_raised"


def test_answer_if_open_releases_hold(tmp_path):
    store = _tmp_store(tmp_path)
    store.add_in_flight(16, "claude")
    andon_id = "wf:16:s:0"
    store.hold_in_flight(16, by=andon_id)
    a = _make_andon(issue=16, andon_id=andon_id, kind="broken")
    client = _fake_client(comments=[{"body": to_comment(a), "id": 10}])
    with (
        patch("issuesmith.andon.QueueStore", return_value=store),
        patch("issuesmith.andon._iter_open_andon_issues", return_value=iter([{"number": 16}])),
        patch("issuesmith.andon._write_metrics"),
    ):
        answer_if_open(client, andon_id, "auto")
    assert "held" not in store.snapshot().in_flight[0]


@pytest.mark.parametrize("fail_on", ["init", "release"])
def test_answer_continues_when_release_hold_fails(tmp_path, caplog, fail_on):
    store = _tmp_store(tmp_path)
    store.add_in_flight(10, "claude")
    store.hold_in_flight(10, by="wf:10:p1:0")
    a = _make_andon(issue=10, andon_id="wf:10:p1:0", kind="blocked", step="p1")
    client = _fake_client(comments=[{"body": to_comment(a)}])
    if fail_on == "init":
        store_patch = patch("issuesmith.andon.QueueStore", side_effect=OSError("boom"))
    else:
        broken = MagicMock()
        broken.release_hold.side_effect = OSError("boom")
        store_patch = patch("issuesmith.andon.QueueStore", return_value=broken)
    with (
        store_patch,
        caplog.at_level("WARNING", logger="issuesmith.andon"),
        patch("issuesmith.andon._iter_open_andon_issues", return_value=iter([{"number": 10}])),
        patch("issuesmith.andon._write_metrics"),
        patch("issuesmith.andon._call_resume_hook"),
    ):
        answer(client, "wf:10:p1:0", "resume")
    warnings = [r for r in caplog.records if r.name == "issuesmith.andon"]
    assert len(warnings) == 1
    assert len(client.issue_comments) == 1
