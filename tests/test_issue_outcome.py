"""Issue close-outcome mapping (#7965 AC1)."""

from __future__ import annotations

import pytest

from issuesmith.issue_outcome import issue_outcome


@pytest.mark.parametrize(
    ("issue", "expected"),
    [
        ({"state": "OPEN"}, "open"),
        ({"state": "CLOSED", "state_reason": "not_planned"}, "not_planned"),
        ({"state": "CLOSED", "state_reason": "duplicate"}, "not_planned"),
        ({"state": "CLOSED", "state_reason": "completed"}, "completed"),
        ({"state": "CLOSED"}, "completed"),
        ({"state": "CLOSED", "state_reason": None}, "completed"),
        ({"state": "OPEN", "state_reason": "not_planned"}, "open"),
    ],
)
def test_issue_outcome_maps_state_and_reason(issue, expected):
    assert issue_outcome(issue) == expected
