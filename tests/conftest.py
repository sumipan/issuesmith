"""
issuesmith テスト共通設定。
"""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def no_gh_fetch(request):
    """デフォルトで _fetch_issue_body_from_gh を None 返しにモック化する。

    gh CLI に依存しないよう、ローカル design.md を使うテストが意図せず
    実際の GitHub Issue を取得しないように抑制する。
    gh fetch 動作を明示的にテストしたい場合は patch で上書きすること。
    """
    if "gh_fetch" in request.keywords:
        yield
        return
    with (
        patch("issuesmith.context_hook._fetch_issue_body_from_gh", return_value=None),
        patch("issuesmith.context_hook._fetch_issue_comments_from_api", return_value=[]),
    ):
        yield
