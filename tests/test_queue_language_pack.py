"""Queue / triage comments come from the language pack (nexus #4472).

A pack whose messages differ from EN (ASCII only) is written to a tmp YAML file,
loaded with ``load_language_pack`` and installed as ``Config.language``; the queue
must then post that wording.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

import issuesmith.config as config_module
from issuesmith.language import EN, LanguagePack, load_language_pack
from issuesmith.queue_store import QueueRequest, QueueStore

_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc).isoformat()
_REPO_ROOT = Path(__file__).resolve().parents[1]
_PREFIX = "[custom-pack]"

_CJK_RE = re.compile(
    "["
    + "".join(
        f"{chr(lo)}-{chr(hi)}"
        for lo, hi in (
            (0x2E80, 0x2FFF),
            (0x3000, 0x9FFF),
            (0xAC00, 0xD7AF),
            (0xF900, 0xFAFF),
            (0xFF00, 0xFFEF),
        )
    )
    + "]"
)

_MIGRATED_FILES = (
    "src/issuesmith/queue.py",
    "src/issuesmith/queue_triage.py",
    "src/issuesmith/ac_complete_entry.py",
    "src/issuesmith/scope_gate.py",
    "src/issuesmith/design_lint_entry.py",
    "src/issuesmith/step_verify.py",
    "src/issuesmith/ac_contract.py",
)


def _pack_dict(pack: LanguagePack, **overrides) -> dict:
    data = {}
    for f in dataclasses.fields(LanguagePack):
        value = getattr(pack, f.name)
        if isinstance(value, tuple):
            value = list(value)
        elif not isinstance(value, str):
            value = dict(value)
        data[f.name] = value
    data.update(overrides)
    return data


def install_custom_pack(tmp_path: Path, monkeypatch) -> LanguagePack:
    """Write an ASCII pack differing from EN to tmp YAML and install it as Config.language."""
    messages = {key: f"{_PREFIX} {value}" for key, value in EN.messages.items()}
    path = tmp_path / "custom-pack.yaml"
    path.write_text(
        yaml.safe_dump(_pack_dict(EN, messages=messages), sort_keys=False), encoding="utf-8"
    )
    pack = load_language_pack(path)
    cfg = dataclasses.replace(config_module.get_config(), language=pack)
    monkeypatch.setattr(config_module, "_cached", cfg)
    return pack


@pytest.fixture
def custom_pack(tmp_path, monkeypatch) -> LanguagePack:
    return install_custom_pack(tmp_path, monkeypatch)


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def _request(issue: int = 7, phase: str = "develop") -> QueueRequest:
    return QueueRequest(
        "11111111-1111-1111-1111-111111111111", issue, phase, "s",
        "automation", "low", _NOW, ("bot",),
    )


# --- no CJK in the migrated modules -------------------------------------------


@pytest.mark.parametrize("rel", _MIGRATED_FILES)
def test_migrated_module_has_no_cjk(rel):
    text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
    offending = [n for n, line in enumerate(text.splitlines(), 1) if _CJK_RE.search(line)]
    assert offending == []


# --- queue comments -----------------------------------------------------------


def test_repair_limit_reason_uses_pack(tmp_path, monkeypatch, custom_pack):
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    r = store.enqueue(
        issue=51, phase="develop", source="skill", actor_kind="human",
        priority="normal", requested_by=["alice"], requested_at=_NOW,
    )
    store.update_meta(r.request_id, {"repair_count": qmod.MAX_REPAIR_PER_REQUEST})
    req = store.effective_request(store.snapshot(), r.request_id)
    client = MagicMock()
    client.get_issue_comments.return_value = []

    qmod._apply_repair(store, client, r.request_id, req, {"labels": []}, "bad yaml")

    body = client.issue_comment.call_args.args[1]
    assert body.startswith(custom_pack.message("queue.repair_limit", reason="bad yaml"))


def test_dequeue_comment_uses_pack(tmp_path, monkeypatch, custom_pack):
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    r = store.enqueue(
        issue=50, phase="draft", source="skill", actor_kind="human",
        priority="normal", requested_by=["alice"], requested_at=_NOW,
    )
    client = MagicMock()
    client.issue_get.return_value = {"number": 50, "state": "OPEN"}
    monkeypatch.setattr(qmod, "get_forge", lambda **kwargs: client)
    monkeypatch.setattr(qmod, "QueueStore", lambda **kwargs: store)

    code = qmod.main(["dequeue", "--request-id", r.request_id, "--reason", "cleanup"])

    assert code == 0
    body = client.issue_comment.call_args.args[1]
    assert body == custom_pack.message(
        "queue.dequeued", request_id=r.request_id, reason="cleanup"
    )


# --- milestone child cross-repo note wording (#7329) -----------------------------


def test_child_cross_repo_note_describes_pin_not_release_watcher():
    """Refs #7329."""
    from issuesmith.language import EN

    note = EN.messages["milestone.child_cross_repo_note"]
    assert "release-watcher" not in note
    assert "dependency update" in note
    assert "external repository release" in note
