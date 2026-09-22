"""test_dep_terminal_labels.py - AC-3/AC-4: terminal_labels completion check."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from issuesmith.config import reset_config_cache
from issuesmith.dep_extractor import DepStatus, _has_terminal_label, is_satisfied


@pytest.fixture(autouse=True)
def _clear_config():
    reset_config_cache()
    yield
    reset_config_cache()


_MERGE_DONE_LABELS = [{"name": "issuesmith:merge-done"}]
_BUMP_DONE_LABELS = [{"name": "bump:done"}]
_SUB_DONE_MILESTONE_LABELS = [
    {"name": "scope:milestone"},
    {"name": "issuesmith:sub-done"},
]
_EMPTY_LABELS: list[dict] = []
_OTHER_LABELS = [{"name": "other"}]


class TestHasTerminalLabel:
    def test_merge_done_is_terminal(self):
        assert _has_terminal_label(
            _MERGE_DONE_LABELS, ("issuesmith:merge-done", "bump:done")
        ) is True

    def test_bump_done_is_terminal(self):
        # AC-3: bump:done in default terminal_labels
        assert _has_terminal_label(
            _BUMP_DONE_LABELS, ("issuesmith:merge-done", "bump:done")
        ) is True

    def test_empty_labels_is_not_terminal(self):
        assert _has_terminal_label(
            _EMPTY_LABELS, ("issuesmith:merge-done", "bump:done")
        ) is False

    def test_other_label_is_not_terminal(self):
        assert _has_terminal_label(
            _OTHER_LABELS, ("issuesmith:merge-done", "bump:done")
        ) is False

    def test_custom_terminal_label(self):
        labels = [{"name": "custom:done"}]
        assert _has_terminal_label(labels, ("custom:done",)) is True

    def test_empty_terminal_labels_tuple(self):
        assert _has_terminal_label(_MERGE_DONE_LABELS, ()) is False


class TestIsSatisfiedWithTerminalLabels:
    def test_bump_done_closed_is_satisfied(self):
        # AC-3: bump:done + CLOSED = dependency satisfied
        status = DepStatus(
            issue=100,
            state="CLOSED",
            has_merge_done=True,
            rescue_pr=None,
            title="bump issuesmith to v0.42.0",
            is_exempt=False,
        )
        assert is_satisfied(status) is True

    def test_bump_done_open_is_not_satisfied(self):
        status = DepStatus(
            issue=100,
            state="OPEN",
            has_merge_done=True,
            rescue_pr=None,
            title="bump issuesmith to v0.42.0",
            is_exempt=False,
        )
        assert is_satisfied(status) is False

    def test_no_terminal_label_not_exempt_not_rescue_blocked(self):
        status = DepStatus(
            issue=200,
            state="CLOSED",
            has_merge_done=False,
            rescue_pr=None,
            title="regular issue",
            is_exempt=False,
        )
        assert is_satisfied(status) is False

    def test_exempt_closed_milestone_sub_done_satisfied(self):
        # AC-4: scope:milestone + sub-done + CLOSED -> is_exempt=True -> satisfied
        status = DepStatus(
            issue=300,
            state="CLOSED",
            has_merge_done=False,
            rescue_pr=None,
            title="Milestone: implement feature X",
            is_exempt=True,
        )
        assert is_satisfied(status) is True

    def test_exempt_open_not_satisfied(self):
        # AC-4 guard: is_exempt only satisfies when CLOSED
        status = DepStatus(
            issue=300,
            state="OPEN",
            has_merge_done=False,
            rescue_pr=None,
            title="Milestone: implement feature X",
            is_exempt=True,
        )
        assert is_satisfied(status) is False

    def test_has_terminal_label_property_alias(self):
        # has_terminal_label property returns same value as has_merge_done
        status = DepStatus(
            issue=100,
            state="CLOSED",
            has_merge_done=True,
            rescue_pr=None,
            title="bump issue",
            is_exempt=False,
        )
        assert status.has_terminal_label is True
        status2 = DepStatus(
            issue=101,
            state="CLOSED",
            has_merge_done=False,
            rescue_pr=None,
            title="regular",
            is_exempt=False,
        )
        assert status2.has_terminal_label is False


class TestGetDepStatusWithBumpDone:
    def test_bump_done_issue_recognized_as_terminal(self, monkeypatch):
        # AC-3: get_dep_status uses _has_terminal_label with config.terminal_labels
        from issuesmith import config as cfgmod
        import issuesmith.dep_extractor as dext

        client = MagicMock()
        client.issue_get.return_value = {
            "state": "CLOSED",
            "labels": [{"name": "bump:done"}],
            "title": "chore: bump issuesmith to v0.42.0",
        }

        status = dext.get_dep_status(client, 3501)
        assert status.has_terminal_label is True
        assert is_satisfied(status) is True

    def test_merge_done_issue_recognized_as_terminal(self, monkeypatch):
        import issuesmith.dep_extractor as dext

        client = MagicMock()
        client.issue_get.return_value = {
            "state": "CLOSED",
            "labels": [{"name": "issuesmith:merge-done"}],
            "title": "feat: implement something",
        }

        status = dext.get_dep_status(client, 3000)
        assert status.has_terminal_label is True
        assert is_satisfied(status) is True

    def test_milestone_sub_done_recognized_as_exempt(self, monkeypatch):
        # AC-4: scope:milestone + sub-done makes is_exempt=True
        import issuesmith.dep_extractor as dext

        client = MagicMock()
        client.issue_get.return_value = {
            "state": "CLOSED",
            "labels": [
                {"name": "scope:milestone"},
                {"name": "issuesmith:sub-done"},
            ],
            "title": "Milestone: phase 3",
        }

        status = dext.get_dep_status(client, 2820)
        assert status.is_exempt is True
        assert is_satisfied(status) is True
