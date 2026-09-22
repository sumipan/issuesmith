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

from ghdag.core.vocabulary import DONE_DEFERRED
from ghdag.forge import get_forge
from ghdag.quota import QuotaGate

from issuesmith.andon import Andon as _FullAndon
from issuesmith.andon import raise_andon as _raise_andon
from issuesmith.config import StepConfig, get_config
from issuesmith.engine import RetrySignal
from issuesmith.steps.base import Andon, StepContext, StepResult, Verdict

_cfg = get_config()
REPO_ROOT = _cfg.root
TEMPLATE_DIR = _cfg.paths.template_dir

# None = use get_config().steps. Tests may set a dict (incl. {}) to override.
_STEP_MODULES: dict[str, str] | None = None


def resolve_step_config(step_id: str) -> StepConfig:
    """Resolve step_id → StepConfig (config.steps, then hyphen→underscore fallback)."""
    if _STEP_MODULES is not None:
        if step_id in _STEP_MODULES:
            short = _STEP_MODULES[step_id]
            cfg = get_config().steps.get(step_id)
            template = cfg.template if cfg is not None else None
            return StepConfig(module=f"issuesmith.steps.{short}", template=template)
        return StepConfig(module=f"issuesmith.steps.{step_id.replace('-', '_')}")

    steps = get_config().steps
    if step_id in steps:
        return steps[step_id]
    return StepConfig(module=f"issuesmith.steps.{step_id.replace('-', '_')}")


def step_id_to_module(step_id: str) -> str:
    """Return importable module path for step_id (compat / short-name helpers)."""
    return resolve_step_config(step_id).module


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
        worktree_path=context.get("worktree_path", ""),
        target_worktree_path=context.get("target_worktree_path", ""),
        branch=context.get("branch", ""),
        target_repo=context.get("target_repo", ""),
        allow_paths=context.get("allow_paths", ""),
        diary_worktree_path=context.get("diary_worktree_path", ""),
        has_diary_changes=context.get("has_diary_changes", ""),
        pipeline_id=context.get("pipeline_id", ""),
        diary_allow_paths=context.get("diary_allow_paths", ""),
        issue_repo=context.get("issue_repo", ""),
        p1_result_filename=context.get("p1_result_filename", ""),
        p2_result_filename=context.get("p2_result_filename", ""),
        p3_result_filename=context.get("p3_result_filename", ""),
        execution_constraints=context.get("execution_constraints", ""),
    )


def _call_step_run(mod: object, ctx: StepContext, step: StepConfig):
    run_fn = getattr(mod, "run")
    try:
        return run_fn(ctx, step)
    except TypeError:
        return run_fn(ctx)


def _try_python_step(step_id: str, context: dict[str, str]) -> int | None:
    step = resolve_step_config(step_id)
    try:
        mod = importlib.import_module(step.module)
    except ImportError:
        return None
    if not hasattr(mod, "run"):
        return None

    print(
        f"[issuesmith-dispatch] python-step step={step_id} module={step.module}",
        file=sys.stderr,
    )
    result = _call_step_run(mod, _context_to_step(context), step)
    return map_step_result(result, step_id=step_id, context=context)


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


# Markers that map to a phase-done/phase-running label transition.
_MARKER_PHASE: dict[str, str] = {
    "MERGE_DONE": "merge",
    "IMPL_DONE": "develop",
    "REPORT_DONE": "draft",
}


def _project_marker_labels(marker: str, context: dict[str, str]) -> None:
    """Apply phase-done label and remove phase-running label for terminal markers."""
    phase = _MARKER_PHASE.get(marker)
    if phase is None:
        return
    raw_issue = context.get("issue_number", "")
    if not raw_issue:
        return
    try:
        issue_num = int(raw_issue)
    except ValueError:
        return
    if not issue_num:
        return
    ns = get_config().label_namespace
    try:
        get_forge().issue_update(
            issue_num,
            labels_add=[f"{ns}:{phase}-done"],
            labels_remove=[f"{ns}:{phase}-running"],
        )
    except Exception as exc:
        print(
            f"[issuesmith-dispatch] WARNING: label projection failed for {marker}: {exc}",
            file=sys.stderr,
        )


def map_step_result(
    result: StepResult,
    *,
    step_id: str,
    context: dict[str, str],
    verdicts: list[Verdict] | None = None,
) -> int:
    """Map a StepResult to an exit code, printing PIPELINE_STATUS markers as side effects.

    done  → exit 0 + print PIPELINE_STATUS for each marker + project phase labels
    retry → raise RetrySignal (caught by main(), exits 0 after deferring)
    andon → call raise_andon + exit 1

    If result.irreversible and any verdict in verdicts is not passed, the
    result is overridden to andon(broken) regardless of the original status.
    """
    irreversible = getattr(result, "irreversible", False)
    status = getattr(result, "status", "done")
    markers = getattr(result, "markers", [])
    retry = getattr(result, "retry", None)
    andon = getattr(result, "andon", None)
    exit_code = getattr(result, "exit_code", None)
    pipeline_status = getattr(result, "pipeline_status", None)
    recovery = getattr(result, "recovery", None)

    # Gate check for irreversible steps
    if irreversible and verdicts and not all(v.passed for v in verdicts):
        andon = Andon(kind="broken")
        status = "andon"

    if status == "retry":
        assert retry is not None, "StepResult(status='retry') must set retry="
        raise retry

    if status == "andon":
        if andon is not None:
            issue_num = int(context.get("issue_number") or "0")
            workflow = context.get("workflow_name", "unknown")
            summary = getattr(andon, "summary", "") or f"step {step_id} raised andon {andon.kind}"
            full_andon = _FullAndon(
                id=f"{workflow}:{issue_num}:{step_id}:0",
                kind=andon.kind,
                issue=issue_num,
                step=step_id,
                summary=summary,
            )
            _raise_andon(get_forge(), full_andon)
        return 1

    # status == "done": old-style compat or new markers path
    if exit_code is not None:
        # Old API: honour exit_code / pipeline_status directly
        if recovery:
            issue_num = int(context.get("issue_number") or "0")
            get_forge().issue_comment(issue_num, recovery)
        if pipeline_status:
            print(f"PIPELINE_STATUS: {pipeline_status}")
            _project_marker_labels(pipeline_status, context)
        return exit_code

    for marker in markers:
        print(f"PIPELINE_STATUS: {marker}")
        _project_marker_labels(marker, context)
    return 0


def _waiting_label() -> str:
    return f"{get_config().label_namespace}:waiting"


def _forge_remove_waiting(issue_number: int) -> None:
    """Remove <ns>:waiting label (no-op if absent; called at start of each step run)."""
    try:
        get_forge().remove_label(issue_number, _waiting_label())
    except Exception:
        pass


def _forge_add_waiting(issue_number: int) -> None:
    try:
        get_forge().issue_update(issue_number, labels_add=[_waiting_label()])
    except Exception as exc:
        print(
            f"[issuesmith-dispatch] WARNING: failed to add waiting label: {exc}",
            file=sys.stderr,
        )


def _handle_retry_signal(
    sig: RetrySignal,
    step_id: str,
    issue_number: int | None,
    *,
    quota_gate: QuotaGate | None = None,
    task_uuid: str | None = None,
) -> None:
    """Defer the running task instead of failing it (sumipan/nexus#3515).

    1. Register the task with ``QuotaGate.defer`` so ``release_ready`` re-queues it once one
       of the role's engines is available again. The task uuid comes from ``GHDAG_TASK_UUID``
       (ghdag >= 0.68.0 exports it to launched tasks).
    2. Apply the ``<ns>:waiting`` label.
    3. Emit ``PIPELINE_STATUS: DEFERRED`` so ghdag marks the task DONE_DEFERRED: not a success
       (downstream depends do not start) and not a failure (no failure hook / circuit breaker).
    """
    from issuesmith.engine import ROLE_ENGINES

    uuid = (task_uuid if task_uuid is not None else os.environ.get("GHDAG_TASK_UUID", "")).strip()
    role = getattr(sig, "role", "") or ""
    role_engines = sorted(ROLE_ENGINES.get(role, frozenset())) if role else []
    engine = role_engines[0] if role_engines else "unknown"
    gate = quota_gate or QuotaGate(state_path=get_config().paths.quota_state)
    if uuid:
        gate.defer(
            uuid,
            engine=engine,
            after=sig.after,
            role_engines=role_engines or None,
            reason=f"{step_id}: {sig.reason.value}",
        )
    else:
        print(
            "[issuesmith-dispatch] WARNING: GHDAG_TASK_UUID is not set (ghdag >= 0.68.0 exports "
            "it); the task is marked DEFERRED but nothing will release it automatically",
            file=sys.stderr,
        )
    if issue_number is not None:
        _forge_add_waiting(issue_number)
    print(
        f"[issuesmith-dispatch] deferred step={step_id} uuid={uuid or '-'} "
        f"reason={sig.reason.value} after={sig.after} role={role or '-'} engines={role_engines}",
        file=sys.stderr,
    )
    print(f"PIPELINE_STATUS: {DONE_DEFERRED}")


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

    issue_number: int | None = None
    raw_issue = context.get("issue_number", "")
    if raw_issue:
        try:
            issue_number = int(raw_issue)
        except ValueError:
            pass

    # Remove waiting label at the start of every run (clears it after a resume).
    if issue_number is not None:
        _forge_remove_waiting(issue_number)

    try:
        rc = _try_python_step(step_id, context)
        if rc is not None:
            return rc

        try:
            return _run_bash_step(step_id, context)
        except (KeyError, FileNotFoundError) as exc:
            print(f"[issuesmith-dispatch] ERROR: {exc}", file=sys.stderr)
            broken = StepResult(status="andon", andon=Andon(kind="broken", summary=str(exc)))
            return map_step_result(broken, step_id=step_id, context=context)
    except RetrySignal as sig:
        _handle_retry_signal(sig, step_id, issue_number)
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
