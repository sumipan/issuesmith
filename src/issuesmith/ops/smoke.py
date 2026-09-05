#!/usr/bin/env python3
"""issuesmith パイプラインの実 Issue スモーク。

dispatcher と同じ手順で:
  1. 指定された実 Issue 番号から build_context() で context を作る
  2. workflows/issuesmith.yml の全 handler の全 step を実 context で展開
  3. shell step は bash -n で構文検証
  4. KeyError / bash 構文エラーが 1 件でもあれば exit 1

`workflows/issuesmith/*.md` や `workflows/issuesmith.yml` を変更した PR は
**マージ前に必ず本スクリプトを実行して exit 0 を確認すること**。

Usage:
    python3 scripts/issuesmith-smoke.py 901 903 906
    # 引数なしで実行すると open な reset / develop-ready / merge-ready ラベル付き Issue を自動収集
"""
from __future__ import annotations

import re
import string
import subprocess
import sys

import yaml
from ghdag.github_client import GitHubClient

from issuesmith.config import get_config
from issuesmith.context_hook import build_context

_cfg = get_config()
REPO_ROOT = _cfg.root
WORKFLOW_YAML = _cfg.paths.workflow
TEMPLATE_DIR = _cfg.paths.template_dir


def _fetch_issue_body(issue_number: int) -> str:
    return GitHubClient().issue_get(issue_number, ["body"]).get("body", "")


def _open_issuesmith_issues() -> list[int]:
    """issuesmith:* ラベルが付いた open Issue を最大 5 件返す（fallback サンプル）。"""
    client = GitHubClient()
    seen: set[int] = set()
    numbers: list[int] = []
    for label in ("issuesmith:reset", "issuesmith:develop-ready", "issuesmith:merge-ready"):
        if len(numbers) >= 5:
            break
        for issue in client.list_issues(label, "open"):
            n = int(issue["number"])
            if n not in seen:
                seen.add(n)
                numbers.append(n)
                if len(numbers) >= 5:
                    break
    return numbers


def _smoke_one_issue(issue_number: int, yml: dict) -> list[str]:
    errors: list[str] = []
    body = _fetch_issue_body(issue_number)

    for handler_name, handler in yml["handlers"].items():
        if not isinstance(handler, dict) or "steps" not in handler:
            continue

        prev_ids: set[str] = set()
        for step in handler["steps"]:
            step_id = step["id"]
            template_name = step["template"]
            depends = step.get("depends", [])

            # 順序整合性
            for d in depends:
                if d not in prev_ids:
                    errors.append(
                        f"#{issue_number} {handler_name}.{step_id}: depends '{d}' が"
                        f"前段に存在しない（yml の steps 順序が壊れている）"
                    )
            prev_ids.add(step_id)

            # 実 context を組み立て
            ctx = build_context(issue_number, body=body)
            ctx.update({
                "issue_number": str(issue_number),
                "workflow_name": "issuesmith",
                "handler_name": handler_name,
                "ts": "20260516000000",
                "order_uuid": "00000000-0000-0000-0000-000000000000",
                "result_uuid": "00000000-0000-0000-0000-000000000001",
                "pipeline_id": f"issue-{issue_number}-smoke",
                "result_filename": f"20260516000000-{step_id}-result.md",
            })
            for d in depends:
                ctx[f"{d}_result_filename"] = f"20260516000000-{d}-result.md"

            tpath = TEMPLATE_DIR / f"{template_name}.md"
            if not tpath.exists():
                errors.append(f"#{issue_number} {handler_name}.{step_id}: テンプレ未存在: {tpath}")
                continue

            try:
                rendered = string.Template(tpath.read_text(encoding="utf-8")).substitute(ctx)
            except KeyError as e:
                errors.append(
                    f"#{issue_number} {handler_name}.{step_id} ({template_name}.md):"
                    f" KeyError ${{{e.args[0]}}} — depends={depends}"
                )
                continue

            if step.get("engine") == "shell":
                proc = subprocess.run(
                    ["bash", "-n"], input=rendered, text=True, capture_output=True
                )
                if proc.returncode != 0:
                    errors.append(
                        f"#{issue_number} {handler_name}.{step_id} ({template_name}.md):"
                        f" bash -n failed: {proc.stderr.strip()}"
                    )
                    continue
                tag = "shell"
            else:
                tag = "llm"
            print(f"  OK  #{issue_number} {handler_name}.{step_id:<5} ({tag}, {len(rendered)} bytes)")

    return errors


_RAW_ADD_LABEL_PATTERN = re.compile(
    r"gh\s+issue\s+edit\b.*?--add-label\s+['\"]?issuesmith:"
)

_STATE_MACHINE_CLI = (
    "python -m ghdag.workflow.state_machine transition --workflow workflows/issuesmith.yml"
)

_DEPRECATED_SM_MODULE = "ghdag.workflow." + "label_" + "state_" + "machine"


def _line_uses_deprecated_state_machine_cli(line: str) -> bool:
    return _DEPRECATED_SM_MODULE in line


def _check_no_deprecated_state_machine_references() -> list[str]:
    """workflows/ と scripts/ に旧ラベル遷移 CLI 参照が残っていないか検査。"""
    errors: list[str] = []
    for base in (REPO_ROOT / "workflows", REPO_ROOT / "scripts"):
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in {".md", ".py", ".yml", ".yaml"}:
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if _line_uses_deprecated_state_machine_cli(line):
                    rel = path.relative_to(REPO_ROOT)
                    errors.append(f"{rel}:{lineno}: 旧ラベル遷移 CLI 参照: {line.strip()}")
    return errors


def _check_issuesmith_yml_state_machine() -> list[str]:
    """issuesmith.yml に state machine 宣言が揃っているか検査。"""
    errors: list[str] = []
    yml = yaml.safe_load(WORKFLOW_YAML.read_text(encoding="utf-8"))
    if yml.get("label_namespace") != "issuesmith":
        errors.append("issuesmith.yml: label_namespace: issuesmith が未定義")
    if yml.get("reset_label") != "issuesmith:reset":
        errors.append('issuesmith.yml: reset_label: "issuesmith:reset" が未定義')
    transitions = yml.get("transitions") or {}
    required_edges = {
        ("issuesmith:draft-running", "issuesmith:draft-done"),
        ("issuesmith:draft-running", "issuesmith:develop-ready"),
        ("issuesmith:draft-done", "issuesmith:develop-ready"),
        ("issuesmith:develop-running", "issuesmith:develop-done"),
        ("issuesmith:develop-running", "issuesmith:merge-ready"),
        ("issuesmith:develop-running", "issuesmith:draft-done"),
        ("issuesmith:develop-done", "issuesmith:merge-ready"),
        ("issuesmith:merge-running", "issuesmith:merge-done"),
        ("issuesmith:merge-running", "issuesmith:migrate-ready"),
        ("issuesmith:migrate-running", "issuesmith:merge-ready"),
        ("issuesmith:migrate-running", "issuesmith:reset"),
        ("issuesmith:sub-running", "issuesmith:sub-done"),
    }
    for src, dst in required_edges:
        if dst not in (transitions.get(src) or []):
            errors.append(f"issuesmith.yml: transitions に {src} → {dst} が無い")
    return errors


def _check_templates_use_state_machine_cli() -> list[str]:
    """テンプレートのラベル遷移 CLI が state_machine + --workflow 形式か検査。"""
    errors: list[str] = []
    transition_re = re.compile(
        r"python\s+-m\s+ghdag\.workflow\.state_machine\s+transition"
    )
    for tpath in sorted(TEMPLATE_DIR.glob("*.md")):
        for lineno, line in enumerate(tpath.read_text(encoding="utf-8").splitlines(), 1):
            if not transition_re.search(line):
                continue
            if "state_machine transition --workflow workflows/issuesmith.yml" not in line:
                errors.append(
                    f"{tpath.name}:{lineno}: state_machine CLI が --workflow 形式ではない:"
                    f" {line.strip()}"
                )
    return errors


def _check_raw_add_label_in_templates() -> list[str]:
    """テンプレート内に raw `gh issue edit --add-label issuesmith:*` が残っていないか検査。

    --remove-label のみのクリーンアップは許容。--add-label を含む場合のみエラー。
    """
    errors: list[str] = []
    for tpath in sorted(TEMPLATE_DIR.glob("*.md")):
        for lineno, line in enumerate(tpath.read_text(encoding="utf-8").splitlines(), 1):
            if _RAW_ADD_LABEL_PATTERN.search(line):
                errors.append(
                    f"{tpath.name}:{lineno}: raw `gh issue edit --add-label issuesmith:*` を検出。"
                    f" `{_STATE_MACHINE_CLI}` に置換してください。"
                    f" 該当行: {line.strip()}"
                )
    return errors


def main(argv: list[str]) -> int:
    if argv:
        issues = [int(a) for a in argv]
    else:
        issues = _open_issuesmith_issues()
        if not issues:
            print("ERROR: 検査対象の Issue が無い。引数で Issue 番号を渡すか、"
                  "issuesmith:reset/develop-ready/merge-ready ラベル付きの open Issue を用意",
                  file=sys.stderr)
            return 2

    all_errors: list[str] = []

    print("\n=== テンプレート静的チェック: 旧ラベル遷移 CLI 参照 ===")
    lsm_errors = _check_no_deprecated_state_machine_references()
    if lsm_errors:
        all_errors.extend(lsm_errors)
        for e in lsm_errors:
            print(f"  FAIL: {e}")
    else:
        print("  OK  workflows/ scripts/ に旧ラベル遷移 CLI 参照なし")

    print("\n=== issuesmith.yml state machine 宣言 ===")
    yml_sm_errors = _check_issuesmith_yml_state_machine()
    if yml_sm_errors:
        all_errors.extend(yml_sm_errors)
        for e in yml_sm_errors:
            print(f"  FAIL: {e}")
    else:
        print("  OK  label_namespace / reset_label / transitions")

    print("\n=== テンプレート静的チェック: state_machine CLI 形式 ===")
    cli_errors = _check_templates_use_state_machine_cli()
    if cli_errors:
        all_errors.extend(cli_errors)
        for e in cli_errors:
            print(f"  FAIL: {e}")
    else:
        print("  OK  全テンプレートが state_machine --workflow 形式")

    print("\n=== テンプレート静的チェック: raw add-label 検出 ===")
    static_errors = _check_raw_add_label_in_templates()
    if static_errors:
        all_errors.extend(static_errors)
        for e in static_errors:
            print(f"  WARN: {e}")
    else:
        print("  OK  raw `gh issue edit --add-label issuesmith:*` なし")

    yml = yaml.safe_load(WORKFLOW_YAML.read_text(encoding="utf-8"))
    for n in issues:
        print(f"\n=== Issue #{n} ===")
        all_errors.extend(_smoke_one_issue(n, yml))

    print()
    if all_errors:
        print("=== E2E SMOKE FAILED ===")
        for e in all_errors:
            print(f"  FAIL: {e}")
        print(f"\n合計 {len(all_errors)} 件の問題を検出。修正してから再実行すること。")
        return 1

    print(f"=== E2E SMOKE PASSED ({len(issues)} issues × 全 step) ===")
    print("実 Issue body で dispatcher の全 step が KeyError / bash 構文エラーなしで render される。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
