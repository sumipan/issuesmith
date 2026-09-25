"""Python 3.10 compatible ``string.Template`` identifier extraction (#3611)."""

from __future__ import annotations

import string


def template_identifiers(template: string.Template) -> list[str]:
    """Return named/braced identifiers in order of first appearance, deduplicated.

    Escaped (``$$``) and invalid placeholders are ignored. Matches
    ``Template.get_identifiers()`` (3.11+) without depending on it.
    """
    ids: list[str] = []
    for mo in template.pattern.finditer(template.template):
        named = mo.group("named") or mo.group("braced")
        if named is not None and named not in ids:
            ids.append(named)
    return ids
