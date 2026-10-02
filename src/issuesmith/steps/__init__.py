"""Deprecated compat package — step modules are shims only (#4276).

Each ``issuesmith.steps.<name>`` module re-exports its canonical location
(``issuesmith.milestone``, ``issuesmith.scope_gate``, ``issuesmith.ops.*``, …).
Removal of this package is planned in a final upstream Issue after nexus
migrates imports (#4232).
"""
