"""tests/test_publish_metadata.py — publish PR 本文メタデータ（#2866, 2026-09-06 改訂）"""

from issuesmith.ops.publish import _build_pr_metadata


def test_same_repo_never_uses_closes():
    """同一リポ・単一ターゲットでも Closes は使わない（2026-09-06 決定）。

    issue を閉じるのは常に M2 finalize の役目。GitHub の "Closes" auto-close は
    target_count・same-repo/cross-repo を問わず一切使わない
    （#2852・#2873 で premature close が実測されたため）。
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
    """タイトルは target_count に依存しない（本文のみが変わる）。"""
    t1, _ = _build_pr_metadata(1, "sumipan/nexus", "sumipan/nexus", target_count=1)
    t2, _ = _build_pr_metadata(1, "sumipan/nexus", "sumipan/nexus", target_count=5)
    assert t1 == t2 == "実装: Issue #1"
