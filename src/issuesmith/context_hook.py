"""
context_hook.py — ghdag context_hook for issuesmith

ghdag WorkflowDispatcher の context_hook 機能により呼び出される。
GitHub Issue の body から diary 固有のコンテキスト変数を生成し、JSON で stdout に出力する。

呼び出し形式:
    python -m issuesmith.context_hook <issue_number>

出力 (JSON):
    {
        "pipeline_id": "issue-42-a1b2c3d4",
        "base_branch": "main",
        "allow_paths": "- src/**\n- tests/**",
        "branch": "feat/issue-42-a1b2c3d4",
        "worktree_path": "/path/to/nexus/.claude/worktrees/issue-42-a1b2c3d4",
        ...
    }
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from dataclasses import dataclass

import yaml

from issuesmith.config import get_config

_PIPELINE_BRANCH_RE = re.compile(
    r"<!--\s*pipeline-branch:\s*feat/issue-(\d+)-([a-f0-9]+)\s*-->",
    re.IGNORECASE,
)

_cfg = get_config()
SUPPORTED_REPOS: set[str] = set(_cfg.supported_repos)
_REPO_ROOT = str(_cfg.root)
try:
    _WORKTREES_REL = str(_cfg.paths.worktrees_dir.relative_to(_cfg.root))
except ValueError:
    _WORKTREES_REL = str(_cfg.paths.worktrees_dir)
try:
    _EXTERNAL_REL = str(_cfg.paths.external_dir.relative_to(_cfg.root))
except ValueError:
    _EXTERNAL_REL = str(_cfg.paths.external_dir)


@dataclass
class MetadataViolation:
    field: str
    code: str
    message: str


def validate_issue_metadata(metadata: dict) -> list[MetadataViolation]:
    """パース済み YAML メタデータのセマンティック検証。

    parse_issue_metadata() とは責務を分離し、構文パース後に呼ぶ。
    build_context() の既存 ValueError catch 経路を破壊しない。
    """
    violations: list[MetadataViolation] = []

    target_repo = metadata.get("target_repo", None)
    if not target_repo:
        violations.append(MetadataViolation(
            field="target_repo",
            code="missing_required",
            message="target_repo は全件必須です（単一リポ案件も含む）",
        ))
    elif target_repo not in SUPPORTED_REPOS:
        violations.append(MetadataViolation(
            field="target_repo",
            code="unsupported_repo",
            message=f"target_repo '{target_repo}' は未対応です。対応リポジトリ: {sorted(SUPPORTED_REPOS)}",
        ))

    allow_paths_raw = metadata.get("allow_paths", [])
    if isinstance(allow_paths_raw, str):
        allow_paths_raw = [allow_paths_raw]
    for i, path in enumerate(allow_paths_raw):
        if str(path).startswith("/var/tmp/"):
            violations.append(MetadataViolation(
                field=f"allow_paths[{i}]",
                code="invalid_path_format",
                message=f"allow_paths[{i}] が /var/tmp/ で始まっています（install ディレクトリは禁止）",
            ))
        elif "(" in str(path) and ")" in str(path):
            violations.append(MetadataViolation(
                field=f"allow_paths[{i}]",
                code="annotation_in_path",
                message=f"allow_paths[{i}] に括弧付き注記が混入しています: {path!r}",
            ))

    return violations


def parse_issue_metadata(body: str) -> dict:
    """Issue body 冒頭の ```yaml ... ``` ブロックから YAML メタデータを抽出して返す。

    「冒頭」とは Issue body 内で最初に現れるコードフェンス（```xxx ... ```）を指す。
    本文中に登場する `schedule.yaml の例` 等の途中の yaml ブロックを誤って拾わないよう、
    最初のコードブロックが ```yaml で始まる場合のみメタデータとして解釈する。

    Raises:
        ValueError: コードブロックが無い / 最初のブロックが yaml でない / YAML が空 /
            パース結果が dict 以外（list / scalar 等）の場合
    """
    m = re.search(r"^```(\w*)\n(.*?)\n```", body, re.DOTALL | re.MULTILINE)
    if not m:
        raise ValueError("Issue body にコードブロックがありません")
    lang, raw = m.group(1), m.group(2)
    if lang != "yaml":
        raise ValueError(
            f"Issue body 冒頭のコードブロックが yaml ではありません (lang={lang!r})"
        )
    result = yaml.safe_load(raw)
    if result is None:
        raise ValueError("Issue body の YAML メタデータブロックが空です")
    if not isinstance(result, dict):
        raise ValueError(
            f"Issue body の YAML メタデータは dict である必要があります (got {type(result).__name__})"
        )
    return result


# ghdag dispatcher が subprocess で起動する経路では親プロセス（ghdag_runner 等）が
# .env を sourcing していないため、GITHUB_TOKEN 等が見えない。自前でロードしておく。
# find_dotenv で __file__ 起点に親方向探索することで、worktree 経由起動でも
# 親 nexus リポの .env を見つけられる。
# （override=False が既定なので、明示的に export されている値は上書きしない）
try:
    from dotenv import find_dotenv, load_dotenv
    _env_path = find_dotenv(usecwd=False)
    if _env_path:
        load_dotenv(_env_path)
except ImportError:
    pass


def _fetch_issue_body_from_api(issue_number: int) -> str | None:
    """GitHub API で Issue の最新 body を取得する。失敗時は None を返す。"""
    try:
        from ghdag.github_client import GitHubClient

        data = GitHubClient().issue_get(issue_number, fields=["body"])
        body = data.get("body")
        return body.strip() if isinstance(body, str) and body.strip() else None
    except Exception:
        return None


# tests/tools/issuesmith/conftest.py が patch する互換名
_fetch_issue_body_from_gh = _fetch_issue_body_from_api


def _pipeline_id_from_comments(issue_number: int, comments: list[dict]) -> str | None:
    """pipeline-branch コメントから pipeline_id を復元する。最後の一致を採用。"""
    found: str | None = None
    for comment in comments:
        body = comment.get("body", "") if isinstance(comment, dict) else ""
        for match in _PIPELINE_BRANCH_RE.finditer(body):
            num = int(match.group(1))
            shortid = match.group(2)
            if num == issue_number:
                found = f"issue-{num}-{shortid}"
    return found


def _fetch_issue_comments_from_api(issue_number: int, issue_repo: str) -> list[dict]:
    """GitHub API で Issue コメント一覧を取得する。失敗時は空リスト。"""
    try:
        from ghdag.github_client import GitHubClient

        data = GitHubClient(repo=issue_repo).issue_get(issue_number, fields=["comments"])
        comments = data.get("comments")
        return comments if isinstance(comments, list) else []
    except Exception:
        return []


def build_context(
    issue_number: int,
    *,
    body: str | None = None,
    queue_dir: str | None = None,
) -> dict[str, str]:
    """Issue 番号から ghdag テンプレートコンテキストを生成する。

    GitHub API から Issue body を取得し、YAML メタデータを抽出してコンテキストを生成する。

    Args:
        issue_number: GitHub Issue 番号
        body: Issue body 文字列（省略時は GitHub API から取得。テスト用）
        queue_dir: design ファイルを探すディレクトリ（テスト用・指定時は GitHub API を呼ばない）

    Returns:
        ghdag に注入するコンテキスト dict（全値 str）
    """
    if body is None and queue_dir is not None:
        import pathlib
        design_file = pathlib.Path(queue_dir) / f"issue-{issue_number}-design.md"
        if design_file.exists():
            body = design_file.read_text(encoding="utf-8")
        else:
            body = ""

    if body is None:
        body = _fetch_issue_body_from_gh(issue_number)
        if body is None:
            print(
                f"Error: GitHub API で Issue #{issue_number} の body 取得に失敗しました。",
                file=sys.stderr,
            )
            sys.exit(1)

    # YAML メタデータを抽出
    metadata: dict = {}
    if body:
        try:
            metadata = parse_issue_metadata(body)
        except (ValueError, Exception) as exc:
            logging.warning("Issue #%s: YAML メタデータパース失敗: %s", issue_number, exc)
            metadata = {}

    from ghdag.github_client import DEFAULT_REPO

    issue_repo = str(metadata.get("issue_repo", DEFAULT_REPO))

    comments = _fetch_issue_comments_from_api(issue_number, issue_repo)
    restored_pipeline_id = _pipeline_id_from_comments(issue_number, comments)
    if restored_pipeline_id:
        pipeline_id = restored_pipeline_id
    else:
        shortid = str(uuid.uuid4())[:8]
        pipeline_id = f"issue-{issue_number}-{shortid}"

    worktree_path = f"{_REPO_ROOT}/{_WORKTREES_REL}/{pipeline_id}"
    branch = f"feat/{pipeline_id}"
    base_branch = str(metadata.get("base_branch", "main"))

    allow_paths_raw = metadata.get("allow_paths", [])
    if isinstance(allow_paths_raw, str):
        allow_paths_raw = [allow_paths_raw]
    if allow_paths_raw:
        allow_paths = "\n".join(f"- {p}" for p in allow_paths_raw)
    else:
        allow_paths = "（制限なし）"

    source = str(metadata.get("source", ""))

    target_repo = str(metadata.get("target_repo", ""))
    if target_repo and target_repo == issue_repo:
        # target_repo が issue_repo 自身（例: sumipan/nexus）の場合は cross-repo 扱いしない。
        # external clone/worktree 経路に乗ると、M2 受け入れ条件ゲートがマージ後に消える
        # feature worktree を契約検査して偽陰性を出す（#2567）。ネイティブ経路に正規化する。
        target_repo = ""
    if target_repo and target_repo not in SUPPORTED_REPOS:
        raise ValueError(
            f"target_repo '{target_repo}' は未対応です。"
            f"対応リポジトリ: {sorted(SUPPORTED_REPOS)}"
        )
    if target_repo:
        repo_name = target_repo.split("/")[-1]
        target_clone_path = f"{_EXTERNAL_REL}/{repo_name}"
        target_worktree_path = f"{_EXTERNAL_REL}/{repo_name}/worktrees/{pipeline_id}"
        is_cross_repo = "true"
        worktree_path = target_worktree_path
    else:
        repo_name = ""
        target_clone_path = ""
        target_worktree_path = ""
        is_cross_repo = "false"

    diary_allow_paths_raw = metadata.get("diary_allow_paths", [])
    if isinstance(diary_allow_paths_raw, str):
        diary_allow_paths_raw = [diary_allow_paths_raw]

    has_diary_changes = "true" if (target_repo and diary_allow_paths_raw) else "false"

    if has_diary_changes == "true":
        diary_allow_paths = "\n".join(f"- {p}" for p in diary_allow_paths_raw)
        diary_worktree_path = f"{_REPO_ROOT}/{_WORKTREES_REL}/{pipeline_id}-diary"
    else:
        diary_allow_paths = ""
        diary_worktree_path = ""

    # Lint warning: target_repo あり + diary_allow_paths 未設定 + "やらないこと" に diary 記述
    if target_repo and not diary_allow_paths_raw and body:
        nodo_match = re.search(r"## やらないこと(.*?)(?=\n## |\Z)", body, re.DOTALL)
        if nodo_match:
            nodo_text = nodo_match.group(1)
            _DIARY_AVOIDANCE_PATTERNS = [
                "diary 側修正", "diary 側の変更", "diary を更新", "diary側",
            ]
            if any(p in nodo_text for p in _DIARY_AVOIDANCE_PATTERNS):
                print(
                    f"Warning: Issue #{issue_number}: 「やらないこと」に diary 側変更の記述がありますが"
                    " diary_allow_paths が未設定です。"
                    " B1 ブラッシュアップ時に diary_allow_paths の設定を検討してください。",
                    file=sys.stderr,
                )

    return {
        "pipeline_id": pipeline_id,
        "worktree_path": worktree_path,
        "branch": branch,
        "base_branch": base_branch,
        "allow_paths": allow_paths,
        "source": source,
        "issue_repo": issue_repo,
        "target_repo": target_repo,
        "repo_name": repo_name,
        "target_clone_path": target_clone_path,
        "target_worktree_path": target_worktree_path,
        "is_cross_repo": is_cross_repo,
        "has_diary_changes": has_diary_changes,
        "diary_worktree_path": diary_worktree_path,
        "diary_allow_paths": diary_allow_paths,
    }


def main() -> None:
    """CLI エントリポイント: python -m issuesmith.context_hook <issue_number>

    stdout: JSON（ghdag context_hook プロトコル準拠）
    stderr: エラーメッセージ
    exit code: 0=成功, 1=引数エラー / API エラー
    """
    if len(sys.argv) < 2:
        print(
            "Usage: python -m issuesmith.context_hook <issue_number>",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        issue_number = int(sys.argv[1])
    except ValueError:
        print(
            f"Error: issue_number must be an integer, got: {sys.argv[1]!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    context = build_context(issue_number)
    print(json.dumps(context, ensure_ascii=False))


if __name__ == "__main__":
    main()
