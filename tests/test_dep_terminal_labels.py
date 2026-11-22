"""test_dep_terminal_labels.py - AC-3/AC-4: terminal_labels completion check."""

from __future__ import annotations

import pytest

from issuesmith.config import reset_config_cache
from issuesmith.dep_extractor import _has_terminal_label


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


def test_dep_satisfied_by_develop_done_single_develop(tmp_path, monkeypatch) -> None:
    """#7811 AC6."""
    import yaml

    from issuesmith.config import get_config, reset_config_cache
    from issuesmith.dep_extractor import get_dep_status, is_satisfied

    phases = [
        {
            "name": "draft",
            "role": "design",
            "entry_step": "b1",
            "handler": "brushup",
            "writes_files": False,
            "advance_when": ["deps_terminal"],
        },
        {
            "name": "develop",
            "role": "implementation",
            "entry_step": "prepare",
            "handler": "develop",
            "advance_when": ["deps_terminal"],
        },
    ]
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {"repo": "sumipan/issuesmith", "phases": phases, "terminal_labels": []}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    _, _, develop_done = get_config().phase_labels("develop")

    class _Client:
        def issue_get(self, issue_number: int, *, fields=None):
            return {
                "state": "CLOSED",
                "title": "dep",
                "labels": [{"name": develop_done}],
            }

    assert is_satisfied(get_dep_status(_Client(), 1)) is True




