"""Tests for FailureClass reverse lookup in engine (#3611)."""

from __future__ import annotations

import pytest
from ghdag.metrics.models import FailureClass

from issuesmith.engine import _failure_class_from_value


@pytest.mark.parametrize("member", list(FailureClass))
def test_known_value_returns_member(member):
    assert _failure_class_from_value(member.value) is member


def test_unknown_value_returns_none():
    assert _failure_class_from_value("no-such-class") is None
