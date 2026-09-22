"""A deprecation message must name an existing replacement (sumipan/nexus#3566)."""
from __future__ import annotations

import re

from issuesmith import cli
from tests.conventions._util import SRC, py_files

_DEPRECATION = re.compile(r"deprecated; use '(?:python3 -m )?issuesmith ([a-z][a-z0-9-]*)")


def _deprecation_targets() -> list[tuple[str, str]]:
    found = []
    for path in py_files(SRC):
        for m in _DEPRECATION.finditer(path.read_text(encoding="utf-8")):
            found.append((path.name, m.group(1)))
    return found


def missing_deprecation_targets(found: list[tuple[str, str]], registered: set[str]) -> list[tuple[str, str]]:
    return [(f, cmd) for f, cmd in found if cmd not in registered]


def test_deprecation_messages_point_at_registered_commands():
    found = _deprecation_targets()
    assert found, "expected at least the recover/redispatch deprecations"
    missing = missing_deprecation_targets(found, set(cli._HANDLERS))
    assert not missing, f"deprecation advice names unregistered commands: {missing}"


def test_detects_advice_pointing_at_dropped_command():
    """Reproduction of sumipan/nexus#3566: recover says 'use resume' while resume is unregistered."""
    missing = missing_deprecation_targets(_deprecation_targets(), set(cli._HANDLERS) - {"resume"})
    assert ("cli.py", "resume") in missing


def test_future_warnings_have_replacement_advice():
    for path in py_files(SRC):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"warnings\.warn\(\s*\n?\s*(\"|')(.*?)\1", text, re.S):
            message = m.group(2)
            assert "use " in message or "replaced by" in message, f"{path.name}: warning without replacement advice: {message!r}"
