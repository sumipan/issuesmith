"""Tests for issuesmith.template_ids (#3611)."""

from __future__ import annotations

import string

from issuesmith.template_ids import template_identifiers


def test_named_and_braced_in_order_without_duplicates():
    assert template_identifiers(string.Template("$a ${b} $$c $a")) == ["a", "b"]


def test_no_placeholders():
    assert template_identifiers(string.Template("no vars")) == []


def test_invalid_placeholder_is_ignored():
    assert template_identifiers(string.Template("$ ${x} $1 $y")) == ["x", "y"]


def test_custom_template_subclass_pattern():
    class Pct(string.Template):
        delimiter = "%"

    assert template_identifiers(Pct("%foo $bar %%baz ${qux} %{zed}")) == ["foo", "zed"]


