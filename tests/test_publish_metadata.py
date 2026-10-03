"""tests/test_publish_metadata.py — publish PR body metadata (#2866, revised 2026-09-06)"""

from issuesmith.ops.publish import _build_pr_metadata


def test_same_repo_never_uses_closes():
    """Never use Closes even for same-repo single target (decision 2026-09-06).

    Closing the issue is always M2 finalize's job. GitHub's "Closes" auto-close
    is never used regardless of target_count or same-repo/cross-repo
    (premature close observed in #2852 / #2873).
    """
    title, body = _build_pr_metadata(42, "sumipan/nexus", "sumipan/nexus", target_count=1)
    assert "Refs #42" in body
    assert "Closes" not in body


def test_cross_repo_single_target_never_closes():
    title, body = _build_pr_metadata(99, "sumipan/issuesmith", "sumipan/nexus", target_count=1)
    assert "Refs sumipan/nexus#99" in body
    assert "Closes" not in body


def test_cross_repo_multi_target_never_closes():
    title, body = _build_pr_metadata(42, "sumipan/issuesmith", "sumipan/nexus", target_count=2)
    assert "Refs sumipan/nexus#42" in body
    assert "Closes" not in body


def test_same_repo_multi_target_never_closes():
    title, body = _build_pr_metadata(42, "sumipan/nexus", "sumipan/nexus", target_count=2)
    assert "Refs #42" in body
    assert "Closes" not in body


def test_title_unaffected_by_target_count():
    """Title does not depend on target_count (only the body changes)."""
    t1, _ = _build_pr_metadata(1, "sumipan/nexus", "sumipan/nexus", target_count=1)
    t2, _ = _build_pr_metadata(1, "sumipan/nexus", "sumipan/nexus", target_count=5)
    assert t1 == t2
    assert t1.endswith("Issue #1")


def test_title_and_body_come_from_language_pack():
    title, body = _build_pr_metadata(7, "sumipan/nexus", "sumipan/nexus")
    assert title == "Implement Issue #7"
    assert body == "Auto-generated from the P1/P2 result.\n\nRefs #7"
    title, _ = _build_pr_metadata(7, "sumipan/issuesmith", "sumipan/nexus")
    assert title == "Implement sumipan/nexus#7"


def test_unrestricted_placeholder_disables_allow_paths_filter():
    from issuesmith.ops.publish import _UNRESTRICTED_ALLOW_PATHS, _parse_allow_paths
    from issuesmith.scope_gate import parse_allow_paths_from_ctx

    assert _UNRESTRICTED_ALLOW_PATHS.isascii() is False  # full-width parentheses
    assert _parse_allow_paths(_UNRESTRICTED_ALLOW_PATHS) is None
    assert parse_allow_paths_from_ctx(_UNRESTRICTED_ALLOW_PATHS) == []
    # The legacy placeholder in frozen orders is any full-width-parenthesised word.
    legacy = chr(0xFF08) + "none" + chr(0xFF09)
    assert _parse_allow_paths(legacy) is None
    assert _parse_allow_paths("- src/a.py\n- tests/b.py") == ["src/a.py", "tests/b.py"]
