"""Tests for issuesmith.template_ids (#3611)."""

from __future__ import annotations

import string
import sys

import pytest

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


@pytest.mark.skipif(sys.version_info < (3, 11), reason="get_identifiers is 3.11+")
@pytest.mark.parametrize(
    "text",
    ["$a ${b} $$c $a", "no vars", "${x}$y$x ${y}", "$ $1 ${ok}", "$_under ${a1}"],
)
def test_matches_stdlib_get_identifiers(text):
    tmpl = string.Template(text)
    assert template_identifiers(tmpl) == tmpl.get_identifiers()
