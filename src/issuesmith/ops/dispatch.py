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

# Maximum number of LLM repair cycles before raising andon(decision).
_MAX_REPAIRS: int = 3
# Step ID used for the repair template (nexus-side workflow template).
_REPAIR_STEP_ID: str = "repair"


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
    missing = sorted(set(tmpl.get_identifiers()) - set(context))  # type: ignore[attr-defined]  # TODO(#3611)
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
        repair_violations=context.get("repair_violations", ""),
        repair_step_origin=context.get("repair_step_origin", ""),
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
    step_cfg: StepConfig | None = None,
    _repair_count: int = 0,
) -> int:
    """Map a StepResult to an exit code, printing PIPELINE_STATUS markers as side effects.

    done  → evaluate requires, then exit 0 + print PIPELINE_STATUS + project phase labels
    retry → raise RetrySignal (caught by main(), exits 0 after deferring)
    andon → call raise_andon + exit 1

    If result.irreversible and any verdict in verdicts is not passed, the
    result is overridden to andon(broken) regardless of the original status.

    step_cfg: if None, resolved lazily from step_id (for testing, pass explicitly).
    _repair_count: initial repair count (used in tests to simulate max_repairs state).
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

    # Evaluate StepConfig.requires before emitting markers (#3626).
    # Repair step: skip requires evaluation (re-evaluation is done by the caller loop).
    cfg = step_cfg if step_cfg is not None else resolve_step_config(step_id)
    if cfg.requires and step_id != _REPAIR_STEP_ID:
        rc = run_requires_loop(
            cfg, step_id, context, repair_count=_repair_count
        )
        if rc is not None:
            return rc

    for marker in markers:
        print(f"PIPELINE_STATUS: {marker}")
        _project_marker_labels(marker, context)
    return 0


# ---------------------------------------------------------------------------
# Requires evaluation and repair loop (#3626)
# ---------------------------------------------------------------------------


def _derived_allow_paths(context: dict[str, str]) -> list[str]:
    """Return derived allow_paths (#3756) stored newline-separated in context."""
    raw = context.get("derived_allow_paths", "")
    return [p.strip() for p in raw.splitlines() if p.strip()]


def _merge_derived_allow_paths(context: dict[str, str], gates: dict[str, object]) -> None:
    """Union TestsGate.derived_allow_paths into context in place (kept for the generation)."""
    from issuesmith.gates.worktree import TestsGate

    merged = set(_derived_allow_paths(context))
    for gate in gates.values():
        if isinstance(gate, TestsGate):
            merged.update(gate.derived_allow_paths)
    if merged:
        context["derived_allow_paths"] = "\n".join(sorted(merged))


def _build_requires_gates(
    requires: tuple[str, ...],
    context: dict[str, str],
) -> dict[str, object]:
    """Build gate instances for requires evaluation from context via GATE_REGISTRY."""
    from issuesmith.gates import GATE_REGISTRY, GateBuildContext, GateBuildError

    worktree_path_str = (
        context.get("worktree_path") or context.get("target_worktree_path") or ""
    )
    base_branch = context.get("base_branch", "main")
    allow_paths_raw = context.get("allow_paths", "")
    allow_paths = [
        p.lstrip("- ").strip()
        for p in allow_paths_raw.splitlines()
        if p.strip()
    ]
    worktree_path = Path(worktree_path_str) if worktree_path_str else None
    build_ctx = GateBuildContext(
        worktree_path=worktree_path,
        allow_paths=allow_paths,
        base_branch=base_branch,
        derived_allow_paths=tuple(_derived_allow_paths(context)),
    )

    gates: dict[str, object] = {}
    for gate_id in requires:
        entry = GATE_REGISTRY.get(gate_id)
        if entry is None:
            raise GateBuildError(f"gate {gate_id!r} not found in GATE_REGISTRY")
        try:
            gates[gate_id] = entry.build(build_ctx)
        except GateBuildError:
            raise
        except Exception as exc:
            raise GateBuildError(
                f"gate {gate_id!r} could not be built: {exc}"
            ) from exc

    return gates


def _get_issue_body(context: dict[str, str]) -> str:
    """Return issue body for requires evaluation (from context cache or forge)."""
    body = context.get("issue_body", "")
    if body:
        return body
    raw_issue = context.get("issue_number", "")
    if not raw_issue:
        return ""
    try:
        issue_num = int(raw_issue)
    except ValueError:
        return ""
    try:
        data = get_forge().issue_get(issue_num, fields=["body"])
        return str(data.get("body") or "")
    except Exception:
        return ""


def _fetch_fresh_issue_body(context: dict[str, str]) -> str:
    """Always fetch issue body from forge (no context cache); falls back to context on failure."""
    raw_issue = context.get("issue_number", "")
    if not raw_issue:
        return context.get("issue_body", "")
    try:
        issue_num = int(raw_issue)
    except ValueError:
        return context.get("issue_body", "")
    try:
        data = get_forge().issue_get(issue_num, fields=["body"])
        body = str(data.get("body") or "")
        return body if body else context.get("issue_body", "")
    except Exception:
        return context.get("issue_body", "")


def fetch_issue_inputs(context: dict[str, str]) -> tuple[str, list[str]]:
    """Return (fresh issue body, labels) for gate evaluation outside dispatch (#3663)."""
    return _fetch_fresh_issue_body(context), _get_issue_labels(context)


def _get_issue_labels(context: dict[str, str]) -> list[str]:
    """Return issue labels for requires evaluation (from context cache or forge)."""
    raw = context.get("issue_labels", "")
    if raw:
        return [lbl.strip() for lbl in raw.split(",") if lbl.strip()]
    raw_issue = context.get("issue_number", "")
    if not raw_issue:
        return []
    try:
        issue_num = int(raw_issue)
    except ValueError:
        return []
    try:
        data = get_forge().issue_get(issue_num, fields=["labels"])
        labels_raw = data.get("labels") or []
        return [
            (lbl["name"] if isinstance(lbl, dict) else str(lbl))
            for lbl in labels_raw
        ]
    except Exception:
        return []


def _get_preexisting_rule_ids(
    step_cfg: StepConfig,
    context: dict[str, str],
    gates: dict[str, object],
) -> frozenset[str]:
    """Return rule_ids that are also present on the base branch (preexisting).

    Minimal implementation: evaluate gates against base-branch body if available.
    Returns frozenset() when the check cannot be performed.
    """
    return frozenset()


def _get_previous_commits(context: dict[str, str]) -> str:
    """Return git log --oneline for commits ahead of origin/<base>."""
    worktree_path_str = (
        context.get("worktree_path") or context.get("target_worktree_path") or ""
    )
    base_branch = context.get("base_branch", "main")
    if not worktree_path_str:
        return ""
    try:
        import subprocess as _sp
        proc = _sp.run(
            ["git", "log", "--oneline", f"origin/{base_branch}..HEAD"],
            capture_output=True, text=True, check=False,
            cwd=worktree_path_str,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except Exception:
        return ""


def _run_repair_step(
    violations: list,
    step_id: str,
    context: dict[str, str],
) -> int | None:
    """Launch the repair step for non-auto-fixable violations.

    Returns None to trigger re-evaluation, or a non-zero exit code to stop.
    May raise RetrySignal — callers must NOT catch it (engine retries don't
    count toward repair_count).
    """
    repair_ctx = dict(context)
    violation_lines = "\n".join(f"- {v.rule_id}: {v.message}" for v in violations)
    derived = _derived_allow_paths(context)
    if derived:
        scope_line = (
            "Do not touch files outside allow_paths and derived_allow_paths.\n"
            "derived_allow_paths:\n" + "".join(f"- {p}\n" for p in derived)
        )
    else:
        scope_line = "Do not touch files outside allow_paths.\n"
    repair_ctx["repair_violations"] = (
        "Fix only the violations below with the smallest possible diff. "
        + scope_line + violation_lines
    )
    repair_ctx["repair_step_origin"] = step_id
    repair_ctx["previous_commits"] = _get_previous_commits(context)

    rc = _try_python_step(_REPAIR_STEP_ID, repair_ctx)
    if rc is None:
        # Bash fallback: mark repair active so a template that calls
        # `dispatch repair` again stops at the main() guard instead of recursing.
        old_active = os.environ.get("ISSUESMITH_REPAIR_ACTIVE")
        os.environ["ISSUESMITH_REPAIR_ACTIVE"] = "1"
        try:
            rc = _run_bash_step(_REPAIR_STEP_ID, repair_ctx)
        except (KeyError, FileNotFoundError):
            return None  # no template → treat as success, re-evaluate
        finally:
            if old_active is None:
                os.environ.pop("ISSUESMITH_REPAIR_ACTIVE", None)
            else:
                os.environ["ISSUESMITH_REPAIR_ACTIVE"] = old_active
    return rc if rc != 0 else None


def _safe_record_metrics(event: str, step_id: str, issue_num: int) -> None:
    try:
        from issuesmith.repair import record_metrics

        record_metrics(get_config().paths.metrics, event, step_id, issue_num)
    except Exception:
        pass


def _violation_gate_id(rule_id: str, gates: dict[str, object]) -> str | None:
    """Map a violation rule_id to its gate_id by prefix matching."""
    for gate_id in gates:
        if rule_id == gate_id or rule_id.startswith(gate_id + "."):
            return gate_id
    return None


def _is_violation_repairable(rule_id: str, gates: dict[str, object]) -> bool:
    """Return True if the gate that produced this violation allows LLM repair."""
    from issuesmith.gates import GATE_REGISTRY

    gate_id = _violation_gate_id(rule_id, gates)
    if gate_id is None:
        return True
    entry = GATE_REGISTRY.get(gate_id)
    if entry is None:
        return True
    return getattr(entry, "repairable", True)


def _build_andon_options(blocking: list) -> list[str]:
    """Build andon decision options from blocking violations.

    pr_scope.out_of_allow → widen:<file1>,<file2> (sorted, deduplicated)
    Always append split and reject.
    """
    pr_scope_files = sorted({
        v.location
        for v in blocking
        if v.rule_id == "pr_scope.out_of_allow" and v.location
    })
    options: list[str] = []
    if pr_scope_files:
        options.append(f"widen:{','.join(pr_scope_files)}")
    options.extend(["split", "reject"])
    return options


def run_requires_loop(
    step_cfg: StepConfig,
    step_id: str,
    context: dict[str, str],
    *,
    repair_count: int = 0,
) -> int | None:
    """Evaluate requires gates; auto-fix, repair, or raise andon as needed.

    Returns None when all gates pass, or an exit code to stop.
    Rebuilds gates and fetches fresh issue body on each evaluation.
    May raise RetrySignal — callers must propagate it (not counted as repair).
    """
    from issuesmith.gates.base import ContractInput
    from issuesmith.repair import apply_auto_fixes, evaluate_requires

    if not step_cfg.requires:
        return None

    # Always fetch fresh issue body (not from context cache)
    body = _fetch_fresh_issue_body(context)

    # Rebuild allow_paths from fresh body (fall back to context value)
    fresh_allow_paths: str | None = None
    if body:
        try:
            from issuesmith.context_hook import parse_issue_metadata
            metadata = parse_issue_metadata(body)
            ap_raw = metadata.get("allow_paths", [])
            if isinstance(ap_raw, list) and ap_raw:
                fresh_allow_paths = "\n".join(f"- {p}" for p in ap_raw)
        except Exception:
            pass

    eval_context = dict(context)
    if fresh_allow_paths is not None:
        eval_context["allow_paths"] = fresh_allow_paths

    from issuesmith.gates import GateBuildError
    try:
        gates = _build_requires_gates(step_cfg.requires, eval_context)
    except GateBuildError as exc:
        summary = f"gate could not be built in step {step_id}: {exc}"
        full_andon = _FullAndon(
            id=f"{eval_context.get('workflow_name', 'unknown')}:{eval_context.get('issue_number', 0)}:{step_id}:0",
            kind="broken",
            issue=int(eval_context.get("issue_number") or "0"),
            step=step_id,
            summary=summary,
        )
        _raise_andon(get_forge(), full_andon)
        return 1

    labels = _get_issue_labels(context)
    inp = ContractInput(body=body, labels=labels)

    preexisting_ids = _get_preexisting_rule_ids(step_cfg, eval_context, gates)
    result = evaluate_requires(
        gates, body, labels, preexisting_rule_ids=preexisting_ids
    )
    # Shared with recursive calls and engine._run_guarded_with_requires (#3756).
    _merge_derived_allow_paths(context, gates)

    issue_num = int(eval_context.get("issue_number") or "0")
    workflow = eval_context.get("workflow_name", "unknown")

    # Gate exception → andon(broken), no repair attempted.
    if result.gate_error is not None:
        summary = (
            f"requires gate raised exception in step {step_id}: {result.gate_error}"
        )
        full_andon = _FullAndon(
            id=f"{workflow}:{issue_num}:{step_id}:0",
            kind="broken",
            issue=issue_num,
            step=step_id,
            summary=summary,
        )
        _raise_andon(get_forge(), full_andon)
        return 1

    # Preexisting violations: visualize only, do not block.
    if result.preexisting:
        _record_preexisting_violations(result.preexisting, step_id, eval_context, issue_num)

    if not result.blocking:
        _safe_record_metrics("requires_check", step_id, issue_num)
        return None

    # Try deterministic auto-fixes for auto_fixable violations.
    has_auto_fixable = any(v.auto_fixable for v in result.blocking)
    if has_auto_fixable:
        result, inp = apply_auto_fixes(result, gates, inp)
        if not result.blocking:
            return run_requires_loop(
                step_cfg, step_id, context,
                repair_count=repair_count,
            )

    if not result.blocking:
        _safe_record_metrics("requires_check", step_id, issue_num)
        return None

    # Non-repairable violations → andon(decision) without repair
    non_repairable = [
        v for v in result.blocking
        if not _is_violation_repairable(v.rule_id, gates)
    ]
    if non_repairable:
        options = _build_andon_options(result.blocking)
        summary = (
            f"non-repairable gate violation in step {step_id}: "
            + "; ".join(v.message for v in non_repairable)
        )
        full_andon = _FullAndon(
            id=f"{workflow}:{issue_num}:{step_id}:0",
            kind="decision",
            issue=issue_num,
            step=step_id,
            summary=summary,
            options=options,
        )
        _raise_andon(get_forge(), full_andon)
        return 1

    # Non-auto-fixable repairable violations remain → repair step or andon.
    if repair_count >= _MAX_REPAIRS:
        options = _build_andon_options(result.blocking)
        summary = (
            f"requires evaluation failed after {repair_count} repair(s)"
            f" in step {step_id}: "
            + "; ".join(v.message for v in result.blocking)
        )
        full_andon = _FullAndon(
            id=f"{workflow}:{issue_num}:{step_id}:0",
            kind="decision",
            issue=issue_num,
            step=step_id,
            summary=summary,
            options=options,
        )
        _raise_andon(get_forge(), full_andon)
        return 1

    _safe_record_metrics("requires_repair", step_id, issue_num)

    # Launch repair step — may raise RetrySignal (not counted as repair).
    rc = _run_repair_step(result.blocking, step_id, context)
    if rc is not None:
        return rc  # repair step failed

    # Re-evaluate after successful repair (increment repair_count).
    return run_requires_loop(
        step_cfg, step_id, context,
        repair_count=repair_count + 1,
    )


def _record_preexisting_violations(
    violations: list,
    step_id: str,
    context: dict[str, str],
    issue_num: int,
) -> None:
    """Post preexisting violations to issue comment and record metrics."""
    _safe_record_metrics("requires_preexisting", step_id, issue_num)
    if not issue_num:
        return
    try:
        msg = (
            "## requires: preexisting violations (non-blocking)\n\n"
            + "\n".join(f"- `{v.rule_id}`: {v.message}" for v in violations)
        )
        get_forge().issue_comment(issue_num, msg)
    except Exception:
        pass


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
    if quota_gate is None:
        # The brake (budget pause) lives in its own state file; without it release_ready sees
        # every engine as available and re-queues the task on the next tick (defer / run loop).
        paths = get_config().paths
        quota_gate = QuotaGate(state_path=paths.quota_state, brake_state_path=paths.brake_state)
    gate = quota_gate
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


def handle_retry_signal(
    sig: "RetrySignal",
    step_id: str,
    issue_number: "int | None",
    *,
    quota_gate: "QuotaGate | None" = None,
    task_uuid: "str | None" = None,
) -> None:
    """Public wrapper for _handle_retry_signal (sumipan/nexus#3663)."""
    _handle_retry_signal(sig, step_id, issue_number, quota_gate=quota_gate, task_uuid=task_uuid)


def main(argv: list[str]) -> int:
    from issuesmith.config import ConfigError  # noqa: PLC0415
    from issuesmith.gates import validate_step_requires  # noqa: PLC0415

    try:
        validate_step_requires(get_config().steps)
    except ConfigError as exc:
        print(f"[issuesmith-dispatch] ERROR: invalid requires in issuesmith.yaml: {exc}", file=sys.stderr)
        return 2
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

    # Module-less step (LLM-only step) guard: must be run via engine run-guarded --requires-step.
    steps = get_config().steps
    if step_id in steps and not steps[step_id].module:
        summary = (
            f"step {step_id} has no module; run it via engine run-guarded "
            f"--requires-step {step_id}"
        )
        broken = StepResult(status="andon", andon=Andon(kind="broken", summary=summary))
        return map_step_result(broken, step_id=step_id, context=context)

    # Repair recursion guard: if ISSUESMITH_REPAIR_ACTIVE is set and we are asked to
    # run the repair step again, stop immediately with andon(broken).
    if step_id == _REPAIR_STEP_ID and os.environ.get("ISSUESMITH_REPAIR_ACTIVE"):
        summary = "repair step re-entered itself (ISSUESMITH_REPAIR_ACTIVE is set)"
        broken = StepResult(status="andon", andon=Andon(kind="broken", summary=summary))
        return map_step_result(broken, step_id=step_id, context=context)

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
