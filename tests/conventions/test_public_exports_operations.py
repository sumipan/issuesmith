"""Public export contract for operations modules (sumipan/nexus#6559)."""
from __future__ import annotations

import issuesmith.andon as andon_mod
import issuesmith.queue as queue_mod
import issuesmith.queue_store as queue_store_mod
from issuesmith import andon as issuesmith_andon
from issuesmith import queue as issuesmith_queue

ANDON_PUBLIC_NAMES = frozenset(
    {
        "Andon",
        "AndonSink",
        "AutoAnswerPlan",
        "raise_andon",
        "list_open",
        "list_open_records",
        "answer",
        "from_comment",
        "to_comment",
        "plan_auto_answers",
        "apply_auto_answers",
    }
)

QUEUE_PUBLIC_NAMES = frozenset({"QueueStore", "DispatchResult", "dispatch_one"})


def test_andon_public_exports():
    """issuesmith.andon __all__ matches the operations contract; each name resolves."""
    assert frozenset(andon_mod.__all__) == ANDON_PUBLIC_NAMES
    for name in ANDON_PUBLIC_NAMES:
        assert hasattr(issuesmith_andon, name), f"andon missing attribute {name}"


def test_queue_public_exports():
    """issuesmith.queue __all__ matches the operations contract."""
    assert frozenset(queue_mod.__all__) == QUEUE_PUBLIC_NAMES
    for name in QUEUE_PUBLIC_NAMES:
        assert hasattr(issuesmith_queue, name), f"queue missing attribute {name}"


def test_queue_store_identity():
    """issuesmith.queue.QueueStore is the same object as issuesmith.queue_store.QueueStore."""
    assert issuesmith_queue.QueueStore is queue_store_mod.QueueStore
