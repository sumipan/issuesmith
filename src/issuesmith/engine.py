#!/usr/bin/env python3
"""Switch and run issuesmith LLM roles.

Two independent roles are supported:

* design: Claude Code or Codex CLI
* implementation: Claude Code or Cursor Agent CLI

All LLM steps resolve their role at execution time through the ``run`` command.
Operational selections live under ``.pipeline-state`` and never rewrite the
tracked workflow definition.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import string
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ghdag.llm import call_managed
from ghdag.llm import managed as _llm_managed
from ghdag.llm.capabilities import LLMCapabilities
from ghdag.llm.engines import call as _ghdag_call
from ghdag.metrics import MetricsRecorder, TaskMetrics
from ghdag.metrics.models import FailureClass
from ghdag.quota import QuotaGate
from ruamel.yaml import YAML

from issuesmith.config import get_config

_cfg = get_config()
REPO_ROOT = _cfg.root
WORKFLOW_FILE = _cfg.paths.workflow
STATE_FILE = _cfg.paths.engine_state
LEGACY_STATE_FILE = REPO_ROOT / ".issuesmith-engine"
# jobs/metrics.jsonl は ghdag MetricsRecorder と同一スキーマの JSONL。
METRICS_FILE = _cfg.paths.metrics
METRICS_ENV_VAR = "METRICS_JSONL_PATH"
QUOTA_STATE_PATH = _cfg.paths.quota_state

# call_managed に渡す設計書どおりの capabilities。試行ごとの実効値は
# `_issuesmith_call` が engine 別に上書きする（codex/cursor は
# permission_mode!=default を拒むため）。
_ISSUESMITH_CAPABILITIES = LLMCapabilities(
    permission_mode="bypassPermissions",
    output_format="json",
)
_DEFAULT_CAPABILITIES = LLMCapabilities()

ROLE_ENGINES: dict[str, frozenset[str]] = {
    role: frozenset(role_cfg.allowed) for role, role_cfg in _cfg.engines.items()
}

DEFAULT_MODELS: dict[tuple[str, str], str] = {
    (role, engine): model
    for role, role_cfg in _cfg.engines.items()
    for engine, model in role_cfg.default_model.items()
}

# light tier のデフォルトモデル。heavy tier は常に state の設定モデルを使う。
# state の light_model キーが定義されていればそちらが優先。
# ここに無い (role, engine) は light 指定でも state モデルにフォールバックする。
DEFAULT_LIGHT_MODELS: dict[tuple[str, str], str] = {
    (role, engine): model
    for role, role_cfg in _cfg.engines.items()
    for engine, model in role_cfg.light_model.items()
}

TIERS = frozenset({"light", "heavy"})

IMPLEMENTATION_STEP_IDS = frozenset({"p1", "p3", "m2", "mg1", "sub1"})

# エージェントセッションの暴走防止。観測 wall-time（数百秒）の 3-5 倍を確保。
DEFAULT_TIMEOUTS: dict[str, float] = {
    role: float(role_cfg.timeout_sec) for role, role_cfg in _cfg.engines.items()
}
TIMEOUT_ENV_VAR = "ISSUESMITH_TIMEOUT_SEC"

# ghdag classify_common_failure が RATE_LIMIT を持つまでの nexus 側ワークアラウンド（#2798）。
_RATE_LIMIT_PATTERNS = ("resource_exhausted", "rate limit", "ratelimit", "429")

_RETRY_WAIT_MAX_SECONDS: int = 1800


def _is_rate_limited(text: str) -> bool:
    lower = text.lower()
    return any(p in lower for p in _RATE_LIMIT_PATTERNS)


@dataclass(frozen=True)
class RoleSelection:
    engine: str
    model: str


def _yaml_loader() -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _default_state() -> dict[str, dict[str, str]]:
    return {
        "design": {"engine": "claude", "model": DEFAULT_MODELS[("design", "claude")]},
        "implementation": {
            "engine": "claude",
            "model": DEFAULT_MODELS[("implementation", "claude")],
        },
    }


def _validate_state(data: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(data, dict):
        raise ValueError("engine state must be a mapping")

    normalized: dict[str, dict[str, Any]] = {}
    for role, allowed_engines in ROLE_ENGINES.items():
        value = data.get(role)
        if not isinstance(value, dict):
            raise ValueError(f"missing role configuration: {role}")
        engine = value.get("engine")
        model = value.get("model")
        if engine not in allowed_engines:
            allowed = ", ".join(sorted(allowed_engines))
            raise ValueError(f"{role} engine must be one of: {allowed}")
        if not isinstance(model, str) or not model.strip():
            raise ValueError(f"{role} model must be a non-empty string")
        normalized[role] = {"engine": engine, "model": model.strip()}
        timeout = value.get("timeout_sec")
        if timeout is not None:
            if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
                raise ValueError(f"{role} timeout_sec must be a positive number")
            normalized[role]["timeout_sec"] = timeout
        light_model = value.get("light_model")
        if light_model is not None:
            if not isinstance(light_model, str) or not light_model.strip():
                raise ValueError(f"{role} light_model must be a non-empty string")
            normalized[role]["light_model"] = light_model.strip()
    return normalized


def load_state() -> dict[str, dict[str, Any]]:
    state_path = STATE_FILE if STATE_FILE.exists() else LEGACY_STATE_FILE
    if not state_path.exists():
        return _default_state()
    yaml = _yaml_loader()
    with state_path.open(encoding="utf-8") as stream:
        data = yaml.load(stream)
    # Compatibility with the legacy one-word state file.
    if isinstance(data, str) and data in {"sonnet", "applied"}:
        return _default_state()
    return _validate_state(data)


def resolve(role: str, tier: str | None = None) -> RoleSelection:
    """ロールの engine/model を解決する。

    tier="light" のときのみモデルを降格する: state の light_model →
    DEFAULT_LIGHT_MODELS → 未定義なら state モデルのまま（fail-safe）。
    tier=None / "heavy" は state の設定モデルをそのまま使う。
    """
    if tier is not None and tier not in TIERS:
        raise ValueError(f"tier must be one of: {', '.join(sorted(TIERS))}")
    state = load_state()
    selected = state[role]
    engine = selected["engine"]
    model = selected["model"]
    if tier == "light":
        light_model = selected.get("light_model") or DEFAULT_LIGHT_MODELS.get(
            (role, engine)
        )
        if light_model:
            model = light_model
        else:
            print(
                f"[issuesmith-engine] no light model for role={role} "
                f"engine={engine}; falling back to {model}",
                file=sys.stderr,
            )
    return RoleSelection(engine=engine, model=model)


def _atomic_yaml_write(path: Path, data: Any) -> None:
    yaml = _yaml_loader()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            yaml.dump(data, stream)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _iter_steps(workflow: dict[str, Any]):
    for handler_name, handler in (workflow.get("handlers") or {}).items():
        if not isinstance(handler, dict):
            continue
        for step in handler.get("steps") or []:
            if isinstance(step, dict) and "id" in step:
                yield handler_name, step


def _load_workflow() -> dict[str, Any]:
    yaml = _yaml_loader()
    with WORKFLOW_FILE.open(encoding="utf-8") as stream:
        data = yaml.load(stream)
    if not isinstance(data, dict):
        raise ValueError("issuesmith workflow must be a mapping")
    return data


def switch_role(
    role: str, engine: str, model: str | None, light_model: str | None = None
) -> int:
    if engine not in ROLE_ENGINES[role]:
        allowed = ", ".join(sorted(ROLE_ENGINES[role]))
        raise ValueError(f"{role} engine must be one of: {allowed}")

    selected_model = model or DEFAULT_MODELS[(role, engine)]
    state = load_state()
    previous = state[role]
    entry: dict[str, Any] = {"engine": engine, "model": selected_model}
    if "timeout_sec" in previous:
        entry["timeout_sec"] = previous["timeout_sec"]
    if light_model:
        entry["light_model"] = light_model
    elif previous.get("engine") == engine and "light_model" in previous:
        # モデル名はエンジン固有のため、エンジンが変わったら light_model は破棄する
        entry["light_model"] = previous["light_model"]
    state[role] = entry
    validated = _validate_state(state)

    _atomic_yaml_write(STATE_FILE, validated)
    return 0


def _cursor_models() -> set[str]:
    proc = subprocess.run(
        ["agent", "--list-models"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"agent --list-models failed: {detail}")
    models: set[str] = set()
    for line in proc.stdout.splitlines():
        if " - " in line:
            models.add(line.split(" - ", 1)[0].strip())
    return models


def _allowed_models(engine: str) -> set[str] | None:
    """configs/llm-models.yml の許可リストを返す。読めない場合は None（検証スキップ）。"""
    path = REPO_ROOT / "configs" / "llm-models.yml"
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as stream:
            data = _yaml_loader().load(stream)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    engines = data.get("engines")
    if not isinstance(engines, dict):
        return None
    models = engines.get(engine)
    if not isinstance(models, list):
        return None
    return {str(model) for model in models}


def check_state() -> list[str]:
    errors: list[str] = []
    command_for_engine = {"claude": "claude", "codex": "codex", "cursor": "agent"}
    state = load_state()
    for role in ROLE_ENGINES:
        selection = resolve(role)
        command = command_for_engine[selection.engine]
        if shutil.which(command) is None:
            errors.append(f"{role}: command not found: {command}")
        light_model = state[role].get("light_model")
        if light_model:
            allowed = _allowed_models(selection.engine)
            if allowed is not None and light_model not in allowed:
                errors.append(
                    f"{role}: light_model not in configs/llm-models.yml "
                    f"allowlist for {selection.engine}: {light_model}"
                )

    implementation = resolve("implementation")
    if implementation.engine == "cursor" and shutil.which("agent") is not None:
        try:
            models = _cursor_models()
        except RuntimeError as exc:
            errors.append(f"implementation: {exc}")
        else:
            if implementation.model != "auto" and implementation.model not in models:
                errors.append(f"implementation: unavailable Cursor model: {implementation.model}")

    workflow = _load_workflow()
    found: set[str] = set()
    for handler_name, step in _iter_steps(workflow):
        if step["id"] not in IMPLEMENTATION_STEP_IDS:
            continue
        found.add(str(step["id"]))
        if step.get("engine") != "shell" or step.get("model") != "bash":
            errors.append(
                f"workflow drift: {handler_name}.{step['id']}="
                f"{step.get('engine')}/{step.get('model')} expected shell/bash"
            )
    missing = sorted(IMPLEMENTATION_STEP_IDS - found)
    if missing:
        errors.append(f"implementation steps missing from workflow: {', '.join(missing)}")
    return errors


def _parse_variables(variables: list[str]) -> dict[str, str]:
    parsed_variables: dict[str, str] = {}
    for value in variables:
        if "=" in value:
            key, _, raw = value.partition("=")
            parsed_variables[key] = raw
    return parsed_variables


def _render_template(template_path: str, variables: list[str]) -> str:
    from ghdag.pipeline.order import TemplateVariableError

    path = Path(template_path)
    template = string.Template(path.read_text(encoding="utf-8"))
    parsed_variables = _parse_variables(variables)
    missing = sorted(set(template.get_identifiers()) - set(parsed_variables))
    if missing:
        raise TemplateVariableError(
            f"テンプレート展開エラー ({template_path}): 未定義変数: {missing}"
        )
    return template.substitute(parsed_variables)


def _working_directory(cwd: str | None) -> Path | None:
    if cwd is None:
        return None
    path = Path(cwd)
    if not path.is_absolute():
        path = REPO_ROOT / path
    path = path.resolve()
    if not path.is_dir():
        raise ValueError(f"working directory does not exist: {path}")
    return path


def _metrics_recorder() -> MetricsRecorder:
    override = os.environ.get(METRICS_ENV_VAR)
    return MetricsRecorder(Path(override) if override else METRICS_FILE)


def _resolve_timeout_sec(role: str) -> float:
    state = load_state()
    timeout = state[role].get("timeout_sec")
    if timeout:
        return float(timeout)
    override = os.environ.get(TIMEOUT_ENV_VAR)
    if override:
        try:
            return float(override)
        except ValueError:
            print(
                f"[issuesmith-engine] ignoring invalid {TIMEOUT_ENV_VAR}={override}",
                file=sys.stderr,
            )
    return DEFAULT_TIMEOUTS[role]


def _issuesmith_call(prompt: str, **kwargs):
    """call_managed 内の call() を issuesmith 向けに調整する。

    call_managed は dangerously_skip_permissions を受け取らない。また
    design フォールバックで capabilities が全試行に共有されるが、codex/cursor
    は permission_mode!=default / output_format!=text を拒む。試行ごとの
    engine に合わせて capabilities を差し替え、常に bypass する。
    """
    engine = kwargs.get("engine", "claude")
    kwargs["dangerously_skip_permissions"] = True
    if engine == "claude":
        kwargs["capabilities"] = _ISSUESMITH_CAPABILITIES
    else:
        kwargs["capabilities"] = _DEFAULT_CAPABILITIES
    return _ghdag_call(prompt, **kwargs)


def _record_task_metrics(
    *,
    role: str,
    engine: str,
    model: str,
    template: str | None,
    status: str,
    started_at: float,
    finished_at: float,
    usage,
    failure_class: str | None = None,
) -> None:
    tags: dict[str, str] = {
        "role": role,
        "template": template or "",
    }
    if failure_class is not None:
        tags["failure_class"] = failure_class
    failure_enum: FailureClass | None = None
    if failure_class is not None:
        try:
            failure_enum = FailureClass(failure_class)
        except ValueError:
            failure_enum = None
    _metrics_recorder().record(
        TaskMetrics(
            uuid=str(uuid.uuid4()),
            engine=engine,
            model=model,
            wall_time_sec=round(finished_at - started_at, 3),
            token_count=usage.token_count if usage else None,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            failure_class=failure_enum,
            cost_usd=usage.cost_usd if usage else None,
            cache_read_tokens=usage.cache_read_tokens if usage else None,
            cache_creation_tokens=usage.cache_creation_tokens if usage else None,
            additional_tags=tags,
        )
    )


def _execute(
    role: str,
    content: str,
    *,
    cwd: str | None = None,
    template: str | None = None,
    tier: str | None = None,
    engine: str | None = None,
) -> subprocess.CompletedProcess[str]:
    selection = resolve(role, tier)
    if engine is not None:
        if engine not in ROLE_ENGINES[role]:
            allowed = ", ".join(sorted(ROLE_ENGINES[role]))
            raise ValueError(f"{role} engine must be one of: {allowed}")
        if engine != selection.engine:
            model = DEFAULT_MODELS[(role, engine)]
            if tier == "light":
                model = DEFAULT_LIGHT_MODELS.get((role, engine), model)
            selection = RoleSelection(engine=engine, model=model)

    working_directory = _working_directory(cwd)
    timeout_sec = _resolve_timeout_sec(role)
    fallback_candidates = [
        (alt, DEFAULT_MODELS[(role, alt)])
        for alt in sorted(ROLE_ENGINES[role] - {selection.engine})
    ]

    quota_gate = QuotaGate(state_path=QUOTA_STATE_PATH)
    snapshot = quota_gate.snapshot()
    initial_state = snapshot.engines.get(selection.engine)
    if initial_state and initial_state.status == "paused" and fallback_candidates:
        for alt_engine, alt_model in fallback_candidates:
            alt_state = snapshot.engines.get(alt_engine)
            if not alt_state or alt_state.status != "paused":
                print(
                    f"[issuesmith-engine] {selection.engine} is paused, "
                    f"switching to {alt_engine}",
                    file=sys.stderr,
                )
                selection = RoleSelection(engine=alt_engine, model=alt_model)
                fallback_candidates = []
                break
        else:
            all_engines = [selection.engine] + [e for e, _ in fallback_candidates]
            resume_ats: list[datetime] = []
            for eng in all_engines:
                eng_state = snapshot.engines.get(eng)
                if eng_state and eng_state.status == "paused":
                    if eng_state.resume_at is None:
                        resume_ats = []
                        break
                    resume_ats.append(eng_state.resume_at)
            min_resume = min(resume_ats) if resume_ats else None
            if min_resume is None:
                raise RuntimeError(f"All engines paused for role {role}")
            now = datetime.now(timezone.utc)
            if min_resume > now + timedelta(seconds=_RETRY_WAIT_MAX_SECONDS):
                raise RuntimeError(f"All engines paused for role {role}")
            wait_sec = (min_resume - now).total_seconds()
            if wait_sec > 0:
                time.sleep(wait_sec)
            snapshot = quota_gate.snapshot()
            initial_state = snapshot.engines.get(selection.engine)
            if initial_state and initial_state.status == "paused" and fallback_candidates:
                for alt_engine, alt_model in fallback_candidates:
                    alt_state = snapshot.engines.get(alt_engine)
                    if not alt_state or alt_state.status != "paused":
                        print(
                            f"[issuesmith-engine] {selection.engine} is paused, "
                            f"switching to {alt_engine}",
                            file=sys.stderr,
                        )
                        selection = RoleSelection(engine=alt_engine, model=alt_model)
                        fallback_candidates = []
                        break
                else:
                    raise RuntimeError(f"All engines paused for role {role}")

    print(
        f"[issuesmith-engine] start role={role} engine={selection.engine} "
        f"model={selection.model} tier={tier or 'heavy'} "
        f"cwd={working_directory or Path.cwd()}",
        file=sys.stderr,
    )

    additional_tags = {
        "role": role,
        "template": template or "",
        "tier": tier or "heavy",
    }

    started_at = time.time()
    original_call = _llm_managed.call
    _llm_managed.call = _issuesmith_call  # type: ignore[assignment]
    try:
        result = call_managed(
            content,
            engine=selection.engine,
            model=selection.model,
            timeout=int(timeout_sec),
            cwd=working_directory,
            capabilities=_ISSUESMITH_CAPABILITIES,
            fallback_candidates=fallback_candidates,
            additional_tags=additional_tags,
            quota_gate=quota_gate,
        )

        # F2: ghdag が RATE_LIMIT を分類するまでの nexus 側ワークアラウンド。
        if (
            result.returncode != 0
            and result.failure_class is None
            and _is_rate_limited(result.body)
            and fallback_candidates
        ):
            quota_gate.report(
                engine=result.engine_used,
                status="paused",
                observed_at=datetime.now(timezone.utc),
                reason="rate_limit_detected",
            )
            alt = None
            rate_snapshot = quota_gate.snapshot()
            for alt_engine, alt_model in fallback_candidates:
                alt_state = rate_snapshot.engines.get(alt_engine)
                if not alt_state or alt_state.status != "paused":
                    alt = (alt_engine, alt_model)
                    break
            if alt is not None:
                alt_engine, alt_model = alt
                print(
                    f"[issuesmith-engine] rate limit detected, retried with {alt_engine}",
                    file=sys.stderr,
                )
                result = call_managed(
                    content,
                    engine=alt_engine,
                    model=alt_model,
                    timeout=int(timeout_sec),
                    cwd=working_directory,
                    capabilities=_ISSUESMITH_CAPABILITIES,
                    fallback_candidates=[],
                    additional_tags=additional_tags,
                    quota_gate=quota_gate,
                )
    finally:
        _llm_managed.call = original_call

    finished_at = time.time()
    if result.failure_class == FailureClass.TIMEOUT.value:
        status = "timeout"
    elif result.returncode == 0:
        status = "success"
    else:
        status = "failed"

    _record_task_metrics(
        role=role,
        engine=result.engine_used,
        model=result.model_used,
        template=template,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        usage=result.usage,
        failure_class=result.failure_class,
    )
    return subprocess.CompletedProcess(
        args=[],
        returncode=result.returncode,
        stdout=result.body,
        stderr="",
    )


def _emit(proc: subprocess.CompletedProcess[str]) -> int:
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


def run_role(
    role: str,
    template_path: str,
    variables: list[str],
    cwd: str | None = None,
    tier: str | None = None,
) -> int:
    return _emit(
        _execute(
            role,
            _render_template(template_path, variables),
            cwd=cwd,
            template=Path(template_path).name,
            tier=tier,
        )
    )


def exec_file(
    role: str,
    input_path: str,
    cwd: str | None = None,
    tier: str | None = None,
    engine: str | None = None,
) -> int:
    return _emit(
        _execute(
            role,
            Path(input_path).read_text(encoding="utf-8"),
            cwd=cwd,
            template=Path(input_path).name,
            tier=tier,
            engine=engine,
        )
    )


# PIPELINE_STATUS 契約の単一実装（#2547）。マーカーは「行頭から始まる独立行」のみ
# 有効で、判定はこのモジュールに一元化する。テンプレート側 shell での重複 grep
# 判定は追加しないこと（内外の規則差が #2547 の部分成功事故を生んだ）。
_STATUS_LINE_RE = re.compile(r"^PIPELINE_STATUS: (\S+)\s*$", re.MULTILINE)


def _extract_status_values(stdout: str) -> list[str]:
    """独立行の PIPELINE_STATUS マーカー値を出現順に返す。"""
    return _STATUS_LINE_RE.findall(stdout)


def _has_inline_marker(stdout: str, statuses: list[str]) -> bool:
    """独立行ではない位置にマーカー文字列が埋まっているか（診断用）。"""
    standalone = set(_extract_status_values(stdout))
    return any(
        f"PIPELINE_STATUS: {status}" in stdout and status not in standalone
        for status in statuses
    )


def _mirror_stdout_tail(prefix: str, stdout: str) -> None:
    # ghdag は非ゼロ終了タスクの stdout を result に書かないため、失敗時は
    # stdout 末尾を stderr（永続化される）へミラーして診断材料を残す（#2532）。
    tail = stdout.splitlines()[-50:]
    if tail:
        print(f"[{prefix}] stdout tail ({len(tail)} lines, persisted for diagnostics):", file=sys.stderr)
        for line in tail:
            print(line, file=sys.stderr)


def _run_guarded_order(
    role: str,
    template_path: str,
    variables: list[str],
    success_statuses: list[str],
    failure_status: str,
    cwd: str | None = None,
    tier: str | None = None,
) -> tuple[int, str]:
    """order を実行しマーカー検証する。(returncode, stdout) を返す。

    成功条件: exit 0 かつ独立行のステータスがちょうど 1 件かつ許可値。
    """
    proc = _execute(
        role,
        _render_template(template_path, variables),
        cwd=cwd,
        template=Path(template_path).name,
        tier=tier,
    )
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    statuses = _extract_status_values(proc.stdout)
    if (
        proc.returncode == 0
        and len(statuses) == 1
        and statuses[0] in success_statuses
    ):
        return 0, proc.stdout
    print(f"PIPELINE_STATUS: {failure_status}")
    if proc.returncode != 0:
        print(f"REASON: role process exited with code {proc.returncode}")
    elif len(statuses) != 1:
        detail = f"found {len(statuses)} standalone status lines ({statuses})"
        if _has_inline_marker(proc.stdout, success_statuses):
            detail += "; marker exists only inline — it must be a standalone line"
        print(f"REASON: status contract violated: {detail}")
    else:
        expected = ", ".join(success_statuses)
        print(f"REASON: status '{statuses[0]}' is not an allowed marker ({expected})")
    _mirror_stdout_tail("run-guarded", proc.stdout)
    return 1, proc.stdout


def _run_emit_order(
    role: str,
    template_path: str,
    variables: list[str],
    failure_status: str,
    cwd: str | None = None,
    tier: str | None = None,
) -> tuple[int, str]:
    """engine-emit モードで order を実行する（#2547/#2550）。

    成功条件は exit 0 のみ。order 側の独立行 PIPELINE_STATUS は失敗シグナル
    として扱う（成功マーカーは呼び出し元が発行する）。
    """
    proc = _execute(
        role,
        _render_template(template_path, variables),
        cwd=cwd,
        template=Path(template_path).name,
        tier=tier,
    )
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    statuses = _extract_status_values(proc.stdout)
    if proc.returncode == 0 and not statuses:
        return 0, proc.stdout
    print(f"PIPELINE_STATUS: {failure_status}")
    if proc.returncode != 0:
        reason = f"role process exited with code {proc.returncode}"
    else:
        # emit モードでは LLM の独立行ステータスは失敗シグナル扱い
        # （成功マーカーは engine が発行するため、order 側の status 行は
        # 意図的な失敗報告か契約違反のどちらか）。
        reason = (
            f"order emitted status lines {statuses} — treated as failure "
            "(engine-emit mode issues the success marker itself)"
        )
    print(f"REASON: {reason}")
    print(f"REASON: {reason}", file=sys.stderr)
    _mirror_stdout_tail("engine-emit", proc.stdout)
    return 1, proc.stdout


def run_guarded(
    role: str,
    template_path: str,
    variables: list[str],
    success_statuses: list[str],
    failure_status: str,
    cwd: str | None = None,
    tier: str | None = None,
    emit_status: str | None = None,
) -> int:
    if emit_status:
        rc, _stdout = _run_emit_order(
            role, template_path, variables, failure_status, cwd, tier
        )
        if rc == 0:
            print(f"PIPELINE_STATUS: {emit_status}")
        return rc
    rc, _stdout = _run_guarded_order(
        role, template_path, variables, success_statuses, failure_status, cwd, tier
    )
    return rc


def _run_verify_command(verify_cmd: str) -> tuple[int, str]:
    proc = subprocess.run(
        verify_cmd, shell=True, capture_output=True, text=True, check=False
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def run_verified(
    role: str,
    template_path: str,
    variables: list[str],
    success_statuses: list[str],
    failure_status: str,
    verify_cmd: str,
    recover_template: str,
    max_loops: int = 2,
    cwd: str | None = None,
    tier: str | None = None,
    recover_tier: str = "light",
    skip_verify_statuses: list[str] | None = None,
    emit_status: str | None = None,
) -> int:
    """Verify→Recover→Re-verify 契約付きの LLM ステップ実行（#2541）。

    1. 本体 order を run-guarded 同様に実行（マーカー必須）
    2. verify_cmd（決定論）を実行。exit 0 = PASS
    3. FAIL レポートのみを recover order に渡して修正させ、再 Verify
    4. max_loops 回の recovery で直らなければ failure_status で停止

    recovery の責務は Verify が指摘した成果物不備の修正だけ（機械操作は
    finalizer の仕事）。レポートだけを渡すため recovery は light tier で足りる。
    skip_verify_statuses のマーカーで完了した場合（例: 依存 BLOCK による早期終了）
    は成果物が完成していない前提のため Verify を実行しない。

    emit_status 指定時（#2547）: 成功マーカーは LLM ではなく engine が発行する。
    本体 order の成功条件は exit 0 のみで、order 側の独立行 PIPELINE_STATUS は
    契約違反として扱う（自然言語の書式ゆらぎで制御が壊れるクラスを排除）。
    Verify PASS 後に engine が唯一の `PIPELINE_STATUS: {emit_status}` を出力する。
    """
    if emit_status:
        emit_rc, _stdout = _run_emit_order(
            role, template_path, variables, failure_status, cwd, tier
        )
        if emit_rc != 0:
            return 1
    else:
        rc, stdout = _run_guarded_order(
            role, template_path, variables, success_statuses, failure_status, cwd, tier
        )
        if rc != 0:
            return rc
        for status in skip_verify_statuses or []:
            if status in _extract_status_values(stdout):
                print(f"VERIFY: SKIPPED (status={status})")
                return 0

    attempts = 0
    while True:
        verify_rc, report = _run_verify_command(verify_cmd)
        if verify_rc == 0:
            if emit_status:
                print(f"PIPELINE_STATUS: {emit_status}")
            print(f"VERIFY: PASS (recovery_attempts={attempts})")
            selection = resolve(role, tier)
            now = time.time()
            _record_task_metrics(
                role=role,
                engine=selection.engine,
                model=selection.model,
                template=Path(template_path).name + "+verify",
                status="verify_pass",
                started_at=now,
                finished_at=now,
                usage=None,
            )
            return 0
        sys.stdout.write(report)
        if attempts >= max_loops:
            print(f"PIPELINE_STATUS: {failure_status}")
            print(f"REASON: verify still failing after {attempts} recovery attempts")
            print("[run-verified] final verify report (persisted for diagnostics):", file=sys.stderr)
            sys.stderr.write(report)
            selection = resolve(role, tier)
            now = time.time()
            _record_task_metrics(
                role=role,
                engine=selection.engine,
                model=selection.model,
                template=Path(template_path).name + "+verify",
                status="verify_fail",
                started_at=now,
                finished_at=now,
                usage=None,
            )
            return 1
        attempts += 1
        report_file = tempfile.NamedTemporaryFile(
            mode="w", prefix="issuesmith-verify-", suffix=".md", delete=False
        )
        report_file.write(report)
        report_file.close()
        print(f"VERIFY: FAIL — recovery attempt {attempts}/{max_loops}")
        recover_vars = list(variables) + [f"verify_report_filename={report_file.name}"]
        rproc = _execute(
            role,
            _render_template(recover_template, recover_vars),
            cwd=cwd,
            template=Path(recover_template).name,
            tier=recover_tier,
        )
        sys.stdout.write(rproc.stdout)
        sys.stderr.write(rproc.stderr)
        if rproc.returncode != 0:
            print(f"PIPELINE_STATUS: {failure_status}")
            print(f"REASON: recovery process exited with code {rproc.returncode}")
            return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Switch issuesmith design/implementation LLMs")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("show", help="show effective role assignments")
    subparsers.add_parser("check", help="validate commands, models, and workflow drift")

    switch_parser = subparsers.add_parser("switch", help="switch one role")
    switch_parser.add_argument("role", choices=tuple(ROLE_ENGINES))
    switch_parser.add_argument("engine")
    switch_parser.add_argument("--model")
    switch_parser.add_argument("--light-model")

    resolve_parser = subparsers.add_parser("resolve", help=argparse.SUPPRESS)
    resolve_parser.add_argument("role", choices=tuple(ROLE_ENGINES))
    resolve_parser.add_argument("--field", choices=("engine", "model"))
    resolve_parser.add_argument("--tier", choices=tuple(sorted(TIERS)))

    run_parser = subparsers.add_parser("run", help=argparse.SUPPRESS)
    run_parser.add_argument("role", choices=tuple(ROLE_ENGINES))
    run_parser.add_argument("template_path")
    run_parser.add_argument("--cwd")
    run_parser.add_argument("--tier", choices=tuple(sorted(TIERS)))
    run_parser.add_argument("variables", nargs="*")

    exec_parser = subparsers.add_parser("exec", help=argparse.SUPPRESS)
    exec_parser.add_argument("role", choices=tuple(ROLE_ENGINES))
    exec_parser.add_argument("input_path")
    exec_parser.add_argument("--cwd")
    exec_parser.add_argument("--tier", choices=tuple(sorted(TIERS)))
    exec_parser.add_argument("--engine")

    guarded_parser = subparsers.add_parser("run-guarded", help=argparse.SUPPRESS)
    guarded_parser.add_argument("role", choices=tuple(ROLE_ENGINES))
    guarded_parser.add_argument("template_path")
    guarded_parser.add_argument("--cwd")
    guarded_parser.add_argument("--tier", choices=tuple(sorted(TIERS)))
    guarded_parser.add_argument("--success", action="append", default=[])
    guarded_parser.add_argument("--failure-status", required=True)
    guarded_parser.add_argument("--emit-status")
    guarded_parser.add_argument("variables", nargs="*")

    verified_parser = subparsers.add_parser("run-verified", help=argparse.SUPPRESS)
    verified_parser.add_argument("role", choices=tuple(ROLE_ENGINES))
    verified_parser.add_argument("template_path")
    verified_parser.add_argument("--cwd")
    verified_parser.add_argument("--tier", choices=tuple(sorted(TIERS)))
    verified_parser.add_argument("--success", action="append", default=[])
    verified_parser.add_argument("--failure-status", required=True)
    verified_parser.add_argument("--verify", required=True)
    verified_parser.add_argument("--recover", required=True)
    verified_parser.add_argument("--max-loops", type=int, default=2)
    verified_parser.add_argument("--recover-tier", choices=tuple(sorted(TIERS)), default="light")
    verified_parser.add_argument("--skip-verify-on", action="append", default=[])
    verified_parser.add_argument("--emit-status")
    verified_parser.add_argument("variables", nargs="*")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.action == "show":
            for role in ROLE_ENGINES:
                selection = resolve(role)
                print(f"{role}\t{selection.engine}\t{selection.model}")
            return 0
        if args.action == "switch":
            switch_role(args.role, args.engine, args.model, args.light_model)
            selection = resolve(args.role)
            print(f"{args.role}: engine={selection.engine} model={selection.model}")
            print("ACCEPTED")
            return 0
        if args.action == "resolve":
            selection = resolve(args.role, args.tier)
            if args.field:
                print(getattr(selection, args.field))
            else:
                print(f"{selection.engine}\t{selection.model}")
            return 0
        if args.action == "check":
            errors = check_state()
            if errors:
                for error in errors:
                    print(f"REJECTED:{error}")
                return 1
            print("ACCEPTED")
            return 0
        if args.action == "exec":
            return exec_file(
                args.role, args.input_path, args.cwd, args.tier, args.engine
            )
        if args.action == "run-guarded":
            return run_guarded(
                args.role,
                args.template_path,
                args.variables,
                args.success,
                args.failure_status,
                args.cwd,
                args.tier,
                emit_status=args.emit_status,
            )
        if args.action == "run-verified":
            return run_verified(
                args.role,
                args.template_path,
                args.variables,
                args.success,
                args.failure_status,
                verify_cmd=args.verify,
                recover_template=args.recover,
                max_loops=args.max_loops,
                cwd=args.cwd,
                tier=args.tier,
                recover_tier=args.recover_tier,
                skip_verify_statuses=args.skip_verify_on,
                emit_status=args.emit_status,
            )
        return run_role(
            args.role, args.template_path, args.variables, args.cwd, args.tier
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"REJECTED:{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
