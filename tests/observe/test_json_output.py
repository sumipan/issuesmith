"""#3590: observe --json must serialize every event (no frozenset fields)."""

from __future__ import annotations

import dataclasses
import json

from issuesmith.cli import _json_default
from issuesmith.observe.events import LabelDriftEvent


def test_label_drift_event_is_json_serializable():
    event = LabelDriftEvent(issue=3548, add=("ns:queued",), remove=("ns:draft-done",))
    payload = json.dumps([dataclasses.asdict(event)], ensure_ascii=False, default=_json_default)
    decoded = json.loads(payload)
    assert decoded[0]["add"] == ["ns:queued"]
    assert decoded[0]["remove"] == ["ns:draft-done"]


def test_json_default_sorts_sets():
    assert _json_default(frozenset({"b", "a"})) == ["a", "b"]
