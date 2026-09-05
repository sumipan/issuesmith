#!/usr/bin/env python3
"""issuesmith パイプラインを動かす前の **実行時環境** ヘルスチェック。

テンプレ render テスト（static）と区別される **runtime check**。
以下が成立していなければ exit 1。テンプレ・yml の変更とは独立に、SHR / dag-runner /
ghdag_runner で実際に ghdag が動く環境が壊れていないかを検証する。

検査項目:

  1. pyproject.toml に ghdag 直接依存が存在しないこと（推移的依存であることの確認）
  2. `import ghdag` が成功し `get_adapter('shell')` が正しく解決すること

ghdag は mltgnt 経由の推移的依存として取得する（#1148）。
直接依存を持つと diary と mltgnt で異なるバージョンをピンする二重管理が生じる。

過去事故: pip resolve が stale な egg-info を読んで ghdag を v0.15.0 に
ダウングレードし続け、ShellAdapter が消えて SHR の全 dispatch が失敗。
テンプレ render テストでは絶対に検出できなかった。

CLAUDE.md ルール: workflows/issuesmith/* / pyproject.toml /
scripts/diary_hooks.py / scripts/dag-runner.py を変更する PR は、マージ前に
本スクリプトを **実 Python 環境で** 実行して exit 0 を確認する。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

from issuesmith.config import get_config

REPO_ROOT = get_config().root
PYPROJECT = REPO_ROOT / "pyproject.toml"

_GHDAG_PIN_RE = re.compile(
    r"ghdag\s*@\s*git\+https://github\.com/sumipan/ghdag\.git@v?(\d+\.\d+\.\d+)"
)


def _check_pyproject_no_direct_pin() -> tuple[bool, str]:
    """pyproject.toml に ghdag 直接依存がないことを確認する（推移的依存であるべき）。"""
    text = PYPROJECT.read_text(encoding="utf-8")
    m = _GHDAG_PIN_RE.search(text)
    if m:
        return False, (
            f"pyproject.toml に ghdag 直接依存 pin が存在します: v{m.group(1)}\n"
            "ghdag は mltgnt 経由の推移的依存として取得してください。直接 pin を削除してください。"
        )
    return True, "pyproject.toml: ghdag 直接依存なし（推移的依存として取得）"


def _check_installed_ghdag() -> tuple[bool, str]:
    try:
        import ghdag  # noqa: F401
    except ImportError as e:
        return False, f"ghdag を import できない: {e}"

    try:
        from importlib.metadata import version as _pkg_version
        actual = _pkg_version("ghdag")
    except Exception as e:
        return False, f"importlib.metadata.version('ghdag') 失敗: {e}"

    return True, f"installed ghdag: v{actual}（mltgnt 経由の推移的依存）"


def _check_shell_adapter() -> tuple[bool, str]:
    try:
        from ghdag.workflow import engine as workflow_engine
    except ImportError as e:
        return False, f"ghdag.workflow.engine の import 失敗: {e}"

    get_adapter = getattr(workflow_engine, "get_adapter", None)
    if get_adapter is None:
        return False, "ghdag.workflow.engine.get_adapter が見つからない"
    adapter_not_found_error = getattr(
        workflow_engine,
        "AdapterNotFoundError",
        ValueError,
    )

    try:
        adapter = get_adapter("shell")
    except (adapter_not_found_error, ValueError) as e:
        return False, (
            f"get_adapter('shell') が失敗: {e}\n"
            "  対処: ghdag を v0.34.0 以降に再インストール"
        )
    return True, f"shell adapter OK: {type(adapter).__name__}"


def _parse_skill_frontmatter(text: str) -> dict | None:
    """SKILL.md 先頭の --- で囲まれた frontmatter をパースする。不正なら None。"""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    try:
        data = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _check_agent_skill_manifests() -> tuple[bool, str]:
    """Codex が session 初期化時に探索する agent skills の manifest を検証する。

    1 つでも不正な SKILL.md（frontmatter 欠落・name/description 空）があると、
    対象 Issue と無関係でも design role の Codex session 全体が起動失敗する（#2523）。
    """
    skills_dir = Path(
        os.environ.get("AGENT_SKILLS_DIR", str(Path.home() / ".agents" / "skills"))
    )
    if not skills_dir.is_dir():
        return True, f"agent skills: ディレクトリなし（スキップ）: {skills_dir}"

    bad: list[str] = []
    for manifest in sorted(skills_dir.glob("*/SKILL.md")):
        try:
            text = manifest.read_text(encoding="utf-8")
        except OSError as exc:
            bad.append(f"{manifest}: 読み込み失敗: {exc}")
            continue
        fm = _parse_skill_frontmatter(text)
        if fm is None:
            bad.append(f"{manifest}: frontmatter が欠落または不正 YAML")
            continue
        name = fm.get("name")
        description = fm.get("description")
        if not (isinstance(name, str) and name.strip()):
            bad.append(f"{manifest}: name が空")
        if not (isinstance(description, str) and description.strip()):
            bad.append(f"{manifest}: description が空")
    if bad:
        return False, "agent skills manifest 不正（Codex design role が起動失敗する）: " + "; ".join(bad)
    return True, f"agent skills: {skills_dir} の全 SKILL.md manifest OK"


def _repo_to_pkg_name(repo: str) -> str:
    return repo.rsplit("/", 1)[-1]


def _check_post_merge_stable_install(item: dict) -> tuple[bool, str]:
    repo = item.get("repo", "")
    path = item.get("path", "")
    pkg = _repo_to_pkg_name(str(repo))
    try:
        result = subprocess.run(
            ["pip", "show", pkg],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return False, f"pip show {pkg} failed: {exc}"
    if result.returncode != 0:
        recovery = f'pip install -e "{path}/[dev]" --no-deps'
        return False, f"stable_install: {pkg} not installed — {recovery}"
    editable = ""
    for line in (result.stdout or "").splitlines():
        if line.startswith("Editable project location:"):
            editable = line.split(":", 1)[1].strip()
            break
    if editable == str(path):
        return True, f"stable_install: {pkg} editable at {path}"
    recovery = f'pip install -e "{path}/[dev]" --no-deps'
    return (
        False,
        f"stable_install: editable location {editable!r} != {path!r} — {recovery}",
    )


def _check_post_merge_tag(item: dict) -> tuple[bool, str]:
    repo = str(item.get("repo", ""))
    tag = str(item.get("tag", ""))
    url = f"https://github.com/{repo}.git"
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", url, tag],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return False, f"git ls-remote failed: {exc}"
    lines = [line for line in (result.stdout or "").strip().splitlines() if line]
    if lines:
        return True, f"tag: {tag} exists on {repo}"
    path = item.get("path", f"/var/tmp/{_repo_to_pkg_name(repo)}")
    recovery = f"cd {path} && git tag {tag} && git push origin {tag}"
    return False, f"tag {tag} not found on {repo} — {recovery}"


def _check_post_merge_restart(item: dict) -> tuple[bool, str]:
    processes = item.get("processes", [])
    if not isinstance(processes, list) or not processes:
        return False, "restart: processes must be a non-empty list"
    try:
        result = subprocess.run(
            ["overmind", "status"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        recovery = f"overmind restart {' '.join(str(p) for p in processes)}"
        return False, f"overmind status failed: {exc} — {recovery}"
    output = result.stdout or ""
    missing = [p for p in processes if p not in output or "running" not in output]
    if missing and result.returncode != 0:
        recovery = f"overmind restart {' '.join(str(p) for p in processes)}"
        return False, f"restart: processes not running: {', '.join(missing)} — {recovery}"
    for proc in processes:
        proc_str = str(proc)
        if proc_str not in output:
            recovery = f"overmind restart {' '.join(str(p) for p in processes)}"
            return False, f"restart: {proc_str} not found in overmind status — {recovery}"
    return True, f"restart: {', '.join(str(p) for p in processes)} running"


def check_post_merge(items: list[dict]) -> list[tuple[bool, str]]:
    """post_merge 契約項目の実行時検証。各項目の (ok, message) を返す。"""
    handlers = {
        "stable_install": _check_post_merge_stable_install,
        "tag": _check_post_merge_tag,
        "restart": _check_post_merge_restart,
    }
    results: list[tuple[bool, str]] = []
    for item in items:
        kind = item.get("kind")
        handler = handlers.get(kind)
        if handler is None:
            results.append((False, f"unknown post_merge kind: {kind}"))
            continue
        results.append(handler(item))
    return results


def main() -> int:
    print("=== issuesmith preflight ===")
    failures: list[str] = []

    for fn in (
        _check_pyproject_no_direct_pin,
        _check_installed_ghdag,
        _check_shell_adapter,
        _check_agent_skill_manifests,
    ):
        ok, msg = fn()
        print(("OK " if ok else "FAIL ") + msg)
        if not ok:
            failures.append(msg)

    print()
    if failures:
        print(f"=== PREFLIGHT FAILED ({len(failures)} 件) ===")
        return 1
    print("=== PREFLIGHT PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
