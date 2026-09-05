#!/usr/bin/env python3
"""issuesmith-dispatch.py — shell dispatch 本体のライブ再展開ランナー（#2595 / #2596）.

ghdag は shell order を enqueue 時に string.Template で展開してファイルへ凍結する。
一方 LLM テンプレ（m2-compact.md 等）や scripts/ は実行時にディスクから読まれるため、
DAG 進行中にテンプレ契約を変更すると「凍結された旧 dispatch × ライブの新テンプレ」の
skew が起きる（#2591 / #2585 の M2 停止）。

本ランナーは凍結 order を「変数値を渡すだけの trampoline」にし、dispatch 本体
（workflows/issuesmith/<step>.md）を **実行時** に ghdag と同じ意味論
（string.Template.substitute + 未定義変数チェック）で再展開して実行する。
これにより dispatch 本体・LLM テンプレ・scripts は常に同一ツリーの同一時点から読まれる。

Usage:
    python3 scripts/issuesmith-dispatch.py <step_id> [key=value ...]

Exit code: 展開した dispatch 本体（bash -o pipefail）の終了コードをそのまま返す。
未定義変数 / テンプレ不在は 2。
"""

from __future__ import annotations

import hashlib
import importlib
import os
import string
import subprocess
import sys
import tempfile
from pathlib import Path

from ghdag.github_client import GitHubClient

from issuesmith.config import get_config
from issuesmith.steps.base import StepContext

_cfg = get_config()
REPO_ROOT = _cfg.root
TEMPLATE_DIR = _cfg.paths.template_dir

_STEP_MODULES: dict[str, str] = {
    "m2-role-dispatch": "m2_finalize",
}


def step_id_to_module(step_id: str) -> str:
    return _STEP_MODULES.get(step_id, step_id.replace("-", "_"))


def parse_context(args: list[str]) -> dict[str, str]:
    context: dict[str, str] = {}
    for arg in args:
        if "=" not in arg:
            raise ValueError(f"key=value 形式ではありません: {arg!r}")
        key, value = arg.split("=", 1)
        context[key] = value
    return context


def render(step_id: str, context: dict[str, str], template_dir: Path | None = None) -> tuple[str, str]:
    """テンプレートを ghdag と同じ意味論で展開し、(本文, テンプレ sha256 先頭 12 桁) を返す。"""
    template_path = (template_dir or TEMPLATE_DIR) / f"{step_id}.md"
    if not template_path.exists():
        raise FileNotFoundError(f"テンプレートファイルが見つかりません: {template_path}")
    text = template_path.read_text(encoding="utf-8")
    tmpl = string.Template(text)
    missing = sorted(set(tmpl.get_identifiers()) - set(context))
    if missing:
        raise KeyError(
            f"テンプレート展開エラー ({template_path}): 未定義変数: {missing}, "
            f"利用可能なキー: {sorted(context)}"
        )
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return tmpl.substitute(context), digest


def _context_to_step(context: dict[str, str]) -> StepContext:
    return StepContext(
        issue_number=context.get("issue_number", ""),
        base_branch=context.get("base_branch", ""),
        handler_name=context.get("handler_name", ""),
        is_cross_repo=context.get("is_cross_repo", "false"),
        target_clone_path=context.get("target_clone_path", ""),
        source=context.get("source", ""),
        workflow_name=context.get("workflow_name", ""),
        m1_result_filename=context.get("m1_result_filename", ""),
        m1r_result_filename=context.get("m1r_result_filename", ""),
    )


def _try_python_step(step_id: str, context: dict[str, str]) -> int | None:
    module_name = step_id_to_module(step_id)
    try:
        mod = importlib.import_module(f"issuesmith.steps.{module_name}")
    except ImportError:
        return None
    if not hasattr(mod, "run"):
        return None

    print(
        f"[issuesmith-dispatch] python-step step={step_id} module={module_name}",
        file=sys.stderr,
    )
    result = mod.run(_context_to_step(context))
    if result.recovery:
        GitHubClient().issue_comment(int(context["issue_number"]), result.recovery)
    if result.pipeline_status:
        print(f"PIPELINE_STATUS: {result.pipeline_status}")
    return result.exit_code


def _run_bash_step(step_id: str, context: dict[str, str]) -> int:
    body, digest = render(step_id, context)
    print(f"[issuesmith-dispatch] live-render step={step_id} template_sha={digest}", file=sys.stderr)
    fd, path = tempfile.mkstemp(prefix=f"issuesmith-{step_id}-", suffix=".sh")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        proc = subprocess.run(["bash", "-o", "pipefail", path], cwd=str(REPO_ROOT))
        return proc.returncode
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    step_id, raw_context = argv[0], argv[1:]
    try:
        context = parse_context(raw_context)
    except ValueError as exc:
        print(f"[issuesmith-dispatch] ERROR: {exc}", file=sys.stderr)
        return 2

    rc = _try_python_step(step_id, context)
    if rc is not None:
        return rc

    try:
        return _run_bash_step(step_id, context)
    except (KeyError, FileNotFoundError) as exc:
        print(f"[issuesmith-dispatch] ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
