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
import os
import string
import subprocess
import sys
import tempfile
from pathlib import Path

from issuesmith.config import get_config

_cfg = get_config()
REPO_ROOT = _cfg.root
TEMPLATE_DIR = _cfg.paths.template_dir


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


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    step_id, raw_context = argv[0], argv[1:]
    try:
        context = parse_context(raw_context)
        body, digest = render(step_id, context)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        print(f"[issuesmith-dispatch] ERROR: {exc}", file=sys.stderr)
        return 2

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


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
