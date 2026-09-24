"""AC-8: andon.answer widen:<files> updates allow_paths and calls resume."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_andon_id(step: str = "p1") -> str:
    return f"issuesmith:42:{step}:0"


def _make_client_with_andon(body: str = "", andon_id: str | None = None) -> MagicMock:
    """Build a mock forge client that returns an issue with one andon comment."""
    from issuesmith.andon import Andon, to_comment

    if andon_id is None:
        andon_id = _make_andon_id()

    andon = Andon(
        id=andon_id,
        kind="decision",
        issue=42,
        step="p1",
        summary="scope out of allow",
        options=["widen:src/a.py", "split", "reject"],
    )
    comment_body = to_comment(andon)

    client = MagicMock()
    client.list_issues.return_value = [{"number": 42}]
    client.get_issue_comments.return_value = [{"body": comment_body}]
    client.issue_get.return_value = {"body": body}
    return client


# ---------------------------------------------------------------------------
# AC-8: widen action updates allow_paths in issue body and calls resume
# ---------------------------------------------------------------------------


def test_answer_widen_updates_allow_paths_in_body():
    """widen:src/a.py adds src/a.py to allow_paths in body yaml block."""
    from issuesmith.andon import answer

    body = "```yaml\nallow_paths:\n  - src/main.py\n```\n## Background\n\ntext\n"
    andon_id = _make_andon_id()
    client = _make_client_with_andon(body=body, andon_id=andon_id)

    updated_bodies = []

    def fake_issue_update(num, **kwargs):
        body_arg = kwargs.get("body")
        if body_arg:
            updated_bodies.append(body_arg)

    client.issue_update.side_effect = fake_issue_update

    with patch("issuesmith.andon.resume") as mock_resume:
        with patch("issuesmith.andon._default_metrics_path", return_value=MagicMock()):
            with patch("issuesmith.andon._write_metrics"):
                answer(client, andon_id, "widen:src/a.py")

    # Body was updated with new allow_paths
    assert updated_bodies, "issue_update should have been called with body"
    new_body = updated_bodies[-1]
    assert "src/a.py" in new_body
    assert "src/main.py" in new_body  # existing path preserved
    # resume should have been called
    mock_resume.assert_called_once_with(42, from_step="p1")


def test_answer_widen_deduplicates_paths():
    """widen with already-present file does not duplicate it."""
    from issuesmith.andon import answer

    body = "```yaml\nallow_paths:\n  - src/a.py\n```\n\ntext\n"
    andon_id = _make_andon_id()
    client = _make_client_with_andon(body=body, andon_id=andon_id)

    updated_bodies = []

    def fake_issue_update(num, **kwargs):
        body_arg = kwargs.get("body")
        if body_arg:
            updated_bodies.append(body_arg)

    client.issue_update.side_effect = fake_issue_update

    with patch("issuesmith.andon.resume") as mock_resume:
        with patch("issuesmith.andon._default_metrics_path", return_value=MagicMock()):
            with patch("issuesmith.andon._write_metrics"):
                answer(client, andon_id, "widen:src/a.py")

    if updated_bodies:
        import yaml

        from issuesmith.body_editor import _LEADING_YAML_BLOCK_RE
        m = _LEADING_YAML_BLOCK_RE.search(updated_bodies[-1])
        if m:
            data = yaml.safe_load(m.group(1))
            count = data.get("allow_paths", []).count("src/a.py")
            assert count == 1, "src/a.py should appear exactly once"


def test_answer_widen_no_yaml_block_no_resume():
    """widen on body without yaml block: comment only, no resume."""
    from issuesmith.andon import answer

    body = "## Background\n\nNo yaml block here.\n"
    andon_id = _make_andon_id()
    client = _make_client_with_andon(body=body, andon_id=andon_id)

    with patch("issuesmith.andon.resume") as mock_resume:
        with patch("issuesmith.andon._default_metrics_path", return_value=MagicMock()):
            with patch("issuesmith.andon._write_metrics"):
                answer(client, andon_id, "widen:src/a.py")

    mock_resume.assert_not_called()


def test_answer_split_does_not_resume():
    """split action: record only, no resume."""
    from issuesmith.andon import answer

    andon_id = _make_andon_id()
    client = _make_client_with_andon(andon_id=andon_id)

    with patch("issuesmith.andon.resume") as mock_resume:
        with patch("issuesmith.andon._default_metrics_path", return_value=MagicMock()):
            with patch("issuesmith.andon._write_metrics"):
                answer(client, andon_id, "split")

    mock_resume.assert_not_called()


def test_answer_reject_does_not_resume():
    """reject action: record only, no resume."""
    from issuesmith.andon import answer

    andon_id = _make_andon_id()
    client = _make_client_with_andon(andon_id=andon_id)

    with patch("issuesmith.andon.resume") as mock_resume:
        with patch("issuesmith.andon._default_metrics_path", return_value=MagicMock()):
            with patch("issuesmith.andon._write_metrics"):
                answer(client, andon_id, "reject")

    mock_resume.assert_not_called()


def test_answer_widen_multiple_files():
    """widen:a.py,b.py adds both files to allow_paths."""
    from issuesmith.andon import answer

    body = "```yaml\nallow_paths:\n  - src/main.py\n```\n\ntext\n"
    andon_id = _make_andon_id()
    client = _make_client_with_andon(body=body, andon_id=andon_id)

    updated_bodies = []

    def fake_issue_update(num, **kwargs):
        body_arg = kwargs.get("body")
        if body_arg:
            updated_bodies.append(body_arg)

    client.issue_update.side_effect = fake_issue_update

    with patch("issuesmith.andon.resume") as mock_resume:
        with patch("issuesmith.andon._default_metrics_path", return_value=MagicMock()):
            with patch("issuesmith.andon._write_metrics"):
                answer(client, andon_id, "widen:src/a.py,src/b.py")

    assert updated_bodies
    new_body = updated_bodies[-1]
    assert "src/a.py" in new_body
    assert "src/b.py" in new_body
    mock_resume.assert_called_once()
