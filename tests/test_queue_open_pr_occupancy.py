"""Open PR allow_paths occupancy (#5123)."""

from __future__ import annotations

from unittest.mock import MagicMock

from issuesmith.queue import (
    _allow_paths_conflict,
    _conflict_entry_for_issue,
    _conflict_overlap_path,
    _format_allow_paths_wait_line,
    _open_pr_issue_refs,
    _open_pr_occupancy,
)
from issuesmith.queue_store import QueueSnapshot, QueueStore


def _yaml_body(repo: str, paths: list[str]) -> str:
    lines = ["```yaml", f"target_repo: {repo}", "base_branch: main", "allow_paths:"]
    for path in paths:
        lines.append(f'  - "{path}"')
    lines.append("```")
    return "\n".join(lines) + "\n"


def _snap(in_flight: list[dict] | None = None) -> QueueSnapshot:
    store = QueueStore()
    for entry in in_flight or []:
        store.add_in_flight(
            int(entry["issue"]),
            str(entry.get("engine") or "codex"),
            role=entry.get("role"),
            phase=entry.get("phase"),
            allow_paths=tuple(entry.get("allow_paths") or ()),
            target_repo=entry.get("target_repo"),
        )
    return store.snapshot()


class TestOpenPrIssueRefs:
    def test_head_feat_issue_branch(self):
        client = MagicMock()
        client.pr_list.return_value = [
            {"number": 9, "headRefName": "feat/issue-100-abc"},
        ]
        assert _open_pr_issue_refs(client) == [(100, 9)]

    def test_body_refs_fallback_uses_pr_get(self):
        client = MagicMock()
        client.pr_list.return_value = [
            {"number": 12, "headRefName": "fix/misc"},
        ]
        client.pr_get.return_value = {
            "number": 12,
            "title": "fix",
            "body": "Refs #100",
        }
        assert _open_pr_issue_refs(client) == [(100, 12)]

    def test_dedupes_by_issue_keeps_smaller_pr(self):
        client = MagicMock()
        client.pr_list.return_value = [
            {"number": 20, "headRefName": "feat/issue-100-a"},
            {"number": 5, "headRefName": "feat/issue-100-b"},
        ]
        assert _open_pr_issue_refs(client) == [(100, 5)]

    def test_pr_list_failure_returns_empty(self, capsys):
        client = MagicMock()
        client.pr_list.side_effect = RuntimeError("api down")
        assert _open_pr_issue_refs(client) == []
        err = capsys.readouterr().err
        assert "pr_list failed" in err

    def test_pr_get_failure_skips_pr_only(self, capsys):
        client = MagicMock()
        client.pr_list.return_value = [
            {"number": 1, "headRefName": "other"},
            {"number": 2, "headRefName": "feat/issue-200-x"},
        ]
        client.pr_get.side_effect = RuntimeError("not found")
        assert _open_pr_issue_refs(client) == [(200, 2)]
        err = capsys.readouterr().err
        assert "pr_get #1 failed" in err


class TestOpenPrOccupancy:
    def test_builds_entry_from_open_pr(self):
        client = MagicMock()
        client.pr_list.return_value = [
            {"number": 7, "headRefName": "feat/issue-100-abc"},
        ]
        client.issue_get.return_value = {
            "number": 100,
            "state": "OPEN",
            "labels": [],
            "body": _yaml_body("sumipan/nexus", ["tools/x.py"]),
        }
        snap = _snap([])
        entries = _open_pr_occupancy(client, snap)
        assert len(entries) == 1
        assert entries[0]["issue"] == 100
        assert entries[0]["open_pr_number"] == 7
        assert entries[0]["occupancy_source"] == "open_pr"
        assert entries[0]["allow_paths"] == ["tools/x.py"]

    def test_skips_when_issue_already_in_flight(self):
        client = MagicMock()
        client.pr_list.return_value = [
            {"number": 7, "headRefName": "feat/issue-100-abc"},
        ]
        snap = _snap(
            [
                {
                    "issue": 100,
                    "engine": "codex",
                    "phase": "develop",
                    "target_repo": "sumipan/nexus",
                    "allow_paths": ["tools/x.py"],
                }
            ]
        )
        assert _open_pr_occupancy(client, snap) == []
        client.issue_get.assert_not_called()

    def test_skips_closed_or_terminal(self):
        client = MagicMock()
        client.pr_list.return_value = [
            {"number": 7, "headRefName": "feat/issue-100-abc"},
        ]
        client.issue_get.return_value = {
            "number": 100,
            "state": "CLOSED",
            "labels": [],
            "body": _yaml_body("sumipan/nexus", ["tools/x.py"]),
        }
        snap = _snap([])
        assert _open_pr_occupancy(client, snap) == []

    def test_issue_get_failure_skips_entry_only(self):
        client = MagicMock()
        client.pr_list.return_value = [
            {"number": 1, "headRefName": "feat/issue-100-a"},
            {"number": 2, "headRefName": "feat/issue-200-b"},
        ]

        def _get(num, fields=None):
            if num == 100:
                raise RuntimeError("gone")
            return {
                "number": 200,
                "state": "OPEN",
                "labels": [],
                "body": _yaml_body("sumipan/nexus", ["tools/y.py"]),
            }

        client.issue_get.side_effect = _get
        entries = _open_pr_occupancy(client, _snap([]))
        assert len(entries) == 1 and entries[0]["issue"] == 200


class TestAllowPathsConflictCandidateIssue:
    def test_self_open_pr_does_not_block(self):
        occupancy = [
            {
                "issue": 100,
                "phase": "develop",
                "target_repo": "sumipan/nexus",
                "allow_paths": ["tools/x.py"],
                "occupancy_source": "open_pr",
            }
        ]
        assert (
            _allow_paths_conflict(
                "sumipan/nexus",
                ("tools/x.py",),
                occupancy,
                candidate_phase="develop",
                candidate_issue=100,
            )
            is None
        )

    def test_other_issue_still_blocked(self):
        occupancy = [
            {
                "issue": 100,
                "phase": "develop",
                "target_repo": "sumipan/nexus",
                "allow_paths": ["tools/x.py"],
                "occupancy_source": "open_pr",
            }
        ]
        assert (
            _allow_paths_conflict(
                "sumipan/nexus",
                ("tools/x.py",),
                occupancy,
                candidate_phase="develop",
                candidate_issue=200,
            )
            == 100
        )

    def test_draft_not_blocked_by_open_pr(self):
        occupancy = [
            {
                "issue": 100,
                "phase": "develop",
                "target_repo": "sumipan/nexus",
                "allow_paths": ["tools/x.py"],
            }
        ]
        assert (
            _allow_paths_conflict(
                "sumipan/nexus",
                ("tools/x.py",),
                occupancy,
                candidate_phase="draft",
                candidate_issue=200,
            )
            is None
        )


class TestStatusWaitLine:
    def test_open_pr_conflict_shows_issue_pr_and_path(self):
        occupancy = [
            {
                "issue": 100,
                "phase": "develop",
                "target_repo": "sumipan/nexus",
                "allow_paths": ["tools/x.py"],
                "occupancy_source": "open_pr",
                "open_pr_number": 55,
            }
        ]
        entry = _conflict_entry_for_issue(100, [], occupancy)
        overlap = _conflict_overlap_path(("tools/x.py",), entry)
        line = _format_allow_paths_wait_line(200, 100, entry, overlap)
        assert line == "    waiting: #200 (conflict with #100 via open PR #55 on tools/x.py)"

    def test_in_flight_conflict_keeps_legacy_format(self):
        in_flight = [{"issue": 100, "allow_paths": ["tools/x.py"]}]
        entry = _conflict_entry_for_issue(100, in_flight, [])
        line = _format_allow_paths_wait_line(200, 100, entry, "tools/x.py")
        assert line == "    waiting: #200 (conflict with #100 on tools/x.py)"
