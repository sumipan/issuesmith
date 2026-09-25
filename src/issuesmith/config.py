"""issuesmith instance configuration (issuesmith.yaml).

Resolves nexus-specific values (repo name, paths, engines, timezone) from a
config file so the package can be reused outside this repository.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

import yaml


class ConfigError(ValueError):
    """Raised when issuesmith configuration is invalid."""

_CONFIG_ENV = "ISSUESMITH_CONFIG"
_CONFIG_FILENAME = "issuesmith.yaml"

# src/issuesmith/config.py → parents[2] == sumipan/issuesmith repo root
_PACKAGE_FILE = Path(__file__).resolve()


def _package_fallback_yaml() -> Path:
    return _PACKAGE_FILE.parents[2] / _CONFIG_FILENAME


# nexus 固有のリポジトリ一覧は issuesmith.yaml の supported_repos: に書く。
# パッケージ既定は空（未設定のままでは cross-repo 検証がすべて拒否される）。
_DEFAULT_SUPPORTED_REPOS: frozenset[str] = frozenset()

_DEFAULT_REL_PATHS: dict[str, str] = {
    "queue": "jobs/issuesmith-queue.jsonl",
    "queue_state": "logs/issuesmith-queue-state.json",
    "queue_lock": "logs/issuesmith-queue.lock",
    "triage_log": "jobs/issuesmith-triage.jsonl",
    "seed": "configs/night-queue.yaml",
    "night_state": "logs/night-queue-state.json",
    "exec_jsonl": "jobs/exec.jsonl",
    "done_dir": "jobs/done",
    "quota_state": "jobs/quota-gate.json",
    "metrics": "jobs/metrics.jsonl",
    "worktrees_dir": ".claude/worktrees",
    "external_dir": ".claude/external",
    "workflow": "workflows/issuesmith.yml",
    "template_dir": "workflows/issuesmith",
    "engine_state": ".pipeline-state/issuesmith-engine.yml",
}

_DEFAULT_ENGINES: dict[str, dict[str, Any]] = {
    "design": {
        "allowed": ["claude", "codex"],
        "default_model": {
            "claude": "claude-opus-4-6",
            "codex": "gpt-5.6-sol",
        },
        "light_model": {
            "claude": "claude-sonnet-4-6",
            # gpt-5.4-mini は ChatGPT アカウント認証の codex で 400 になり
            # nexus の allowlist から外れた（2026-09-09、B1 light tier が 2 度停止）。
            "codex": "gpt-5.5",
        },
        "timeout_sec": 1800,
    },
    "implementation": {
        "allowed": ["claude", "cursor"],
        "default_model": {
            "claude": "claude-sonnet-4-6",
            "cursor": "auto",
        },
        "timeout_sec": 3600,
    },
}


@dataclass(frozen=True)
class PathsConfig:
    queue: Path
    queue_state: Path
    queue_lock: Path
    triage_log: Path
    seed: Path
    night_state: Path
    exec_jsonl: Path
    done_dir: Path
    quota_state: Path
    metrics: Path
    worktrees_dir: Path
    external_dir: Path
    workflow: Path
    template_dir: Path
    engine_state: Path
    # budget-brake 用。未指定時は _build_paths が quota_state へフォールバックする。
    # 手動構築のテスト互換のため default None（None は quota_state と同義）。
    brake_state: Path | None = None


@dataclass(frozen=True)
class RoleConfig:
    allowed: frozenset[str]
    default_model: Mapping[str, str]
    timeout_sec: float
    light_model: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ConcurrencyConfig:
    default: int
    per_engine: Mapping[str, int]
    strict_order: bool = False

    def limit(self, engine: str) -> int:
        return self.per_engine.get(engine, self.default)


@dataclass(frozen=True)
class MilestoneChainConfig:
    enabled: bool = False
    child_priority: str = "normal"
    auto_develop: bool = True
    auto_close_parent: bool = True


@dataclass(frozen=True)
class TriageConfig:
    enabled: bool = True
    engine: str = "claude"
    model: str = "claude-sonnet-4-6"
    timeout: int = 60
    body_chars: int = 500
    circuit_breaker_threshold: int = 3
    circuit_breaker_reset_seconds: int = 1800


@dataclass(frozen=True)
class PhaseConfig:
    name: str
    role: str
    entry_step: str
    handler: str = ""
    preconditions: tuple[str, ...] = ()


@dataclass(frozen=True)
class StepConfig:
    module: str = ""
    template: str | None = None
    requires: tuple[str, ...] = ()
    input_kind: Literal["issue", "worktree", "artifact"] = "issue"
    requires_declared: bool = False


_DEFAULT_PHASES: tuple[PhaseConfig, ...] = (
    PhaseConfig(name="draft", role="design", entry_step="b1"),
    PhaseConfig(name="sub", role="implementation", entry_step="sub-ready"),
    PhaseConfig(name="develop", role="implementation", entry_step="cp2"),
    PhaseConfig(name="merge", role="implementation", entry_step="m2"),
)

_DEFAULT_STEPS: dict[str, StepConfig] = {
    "m2-role-dispatch": StepConfig(
        module="issuesmith.steps.m2_finalize",
        template="m2-compact.md",
    ),
}

_DEFAULT_SECTIONS: dict[str, str] = {
    "acceptance_criteria": "受け入れ条件",
    "migration": "マイグレーション手順",
    "migration_state_survey": "実行時状態の調査",
    "sub_plan": "サブイシュー分割計画",
    "design": "設計",
    "background": "背景・目的",
    "dependencies": "依存（先行）",
    "impact_survey": "影響範囲調査",
    "milestone": "マイルストーン",
    "changed_files": "変更対象ファイル",
}

_DEFAULT_SUB_DESIGN_SUBSECTIONS: tuple[str, ...] = (
    "スコープ",
    "設計方針",
    "変更対象ファイル",
    "受け入れ条件",
)

# PR diff scope gate defaults (#3178). Mirrored in issuesmith.yaml.
_DEFAULT_FORBIDDEN_PR_PATHS: tuple[str, ...] = (
    "jobs/**",
    "logs/**",
    ".sessions/**",
    "*.jsonl",
    "*.pid",
    "*.lock",
)


@dataclass(frozen=True)
class ScopeGateConfig:
    """P0 allow_paths size gate (#3349)."""

    enabled: bool = True
    max_files: int = 80
    max_lines: int = 20_000
    hard_max_files: int = 200


@dataclass(frozen=True)
class ScopeCouplingConfig:
    """CP1/B1 scope coupling gate (#3520).

    ``enabled: false`` turns the gate off entirely (nexus #3527: the gate
    contradicts scope_breadth on core-module changes and is disabled until redesigned).
    """

    enabled: bool = True
    search_dirs: tuple[str, ...] = ("tests", "src")


_DEFAULT_TERMINAL_LABELS: tuple[str, ...] = ("issuesmith:merge-done", "bump:done")



@dataclass(frozen=True)
class ObserveConfig:
    """Configuration for the observe layer (issuesmith.yaml observe: section)."""

    stall_minutes: int = 120
    task_timeout_minutes: int = 90
    systemic_min_issues: int = 2
    systemic_window_minutes: int = 60
    forge_max_consecutive_errors: int = 3
    max_api_calls: int = 8


@dataclass(frozen=True)
class ApiBreakConfig:
    """GitHub API rate-limit brake (issuesmith.yaml api_brake: section, #3769)."""

    enabled: bool = False
    min_remaining: int = 800  # 16% of 5000/h: stop before the shared PAT budget runs out


@dataclass(frozen=True)
class IssuesmithConfig:
    repo: str
    label_namespace: str
    timezone: str
    supported_repos: frozenset[str]
    root: Path
    paths: PathsConfig
    engines: Mapping[str, RoleConfig]
    concurrency: ConcurrencyConfig
    milestone_chain: MilestoneChainConfig = field(default_factory=MilestoneChainConfig)
    triage: TriageConfig = field(default_factory=TriageConfig)
    phases: tuple[PhaseConfig, ...] = _DEFAULT_PHASES
    sections: Mapping[str, str] = field(default_factory=lambda: dict(_DEFAULT_SECTIONS))
    sub_design_subsections: tuple[str, ...] = _DEFAULT_SUB_DESIGN_SUBSECTIONS
    steps: Mapping[str, StepConfig] = field(default_factory=lambda: dict(_DEFAULT_STEPS))
    forbidden_pr_paths: tuple[str, ...] = _DEFAULT_FORBIDDEN_PR_PATHS
    scope_gate: ScopeGateConfig = field(default_factory=ScopeGateConfig)
    scope_coupling: ScopeCouplingConfig = field(default_factory=ScopeCouplingConfig)
    terminal_labels: tuple[str, ...] = _DEFAULT_TERMINAL_LABELS
    observe: ObserveConfig = field(default_factory=ObserveConfig)
    api_brake: ApiBreakConfig = field(default_factory=ApiBreakConfig)


_cached: IssuesmithConfig | None = None


def reset_config_cache() -> None:
    """Clear the get_config() cache (for tests)."""
    global _cached
    _cached = None


def get_config() -> IssuesmithConfig:
    """Return cached IssuesmithConfig (loads once per process unless reset)."""
    global _cached
    if _cached is None:
        _cached = load_config()
    return _cached


def load_config(path: Path | None = None) -> IssuesmithConfig:
    """Load IssuesmithConfig.

    Resolution order for the config file:
      1. ``path`` argument
      2. env ``ISSUESMITH_CONFIG``
      3. ``issuesmith.yaml`` found by walking up from cwd
      4. ``Path(__file__).resolve().parents[2] / "issuesmith.yaml"`` (package repo root)
      5. builtin defaults (legacy nexus values)
    """
    resolved = _resolve_config_path(path)
    if resolved is None:
        root = _PACKAGE_FILE.parents[2]
        return _build_config({}, root=root)
    data = _read_yaml(resolved)
    return _build_config(data, root=resolved.parent.resolve())


def _resolve_config_path(path: Path | None) -> Path | None:
    if path is not None:
        p = Path(path)
        return p if p.is_file() else None

    env = os.environ.get(_CONFIG_ENV, "").strip()
    if env:
        p = Path(env)
        return p if p.is_file() else None

    found = _find_upward(Path.cwd(), _CONFIG_FILENAME)
    if found is not None:
        return found

    fallback = _package_fallback_yaml()
    if fallback.is_file():
        return fallback
    return None


def _find_upward(start: Path, filename: str) -> Path | None:
    cur = start.resolve()
    for candidate in [cur, *cur.parents]:
        p = candidate / filename
        if p.is_file():
            return p
    return None


def _read_yaml(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"issuesmith config must be a mapping: {path}")
    return raw


def _abs(root: Path, value: str | Path) -> Path:
    p = Path(value)
    if not p.is_absolute():
        p = root / p
    return p.resolve()


def _build_paths(raw: Mapping[str, Any] | None, root: Path) -> PathsConfig:
    src = dict(_DEFAULT_REL_PATHS)
    if raw:
        for key in _DEFAULT_REL_PATHS:
            if key in raw and raw[key] is not None:
                src[key] = str(raw[key])
    resolved = {k: _abs(root, v) for k, v in src.items()}
    # brake_state は固定既定を持たず、未指定時は同一設定の quota_state にフォールバック
    # （既存利用者は単一 gate のまま動く）。
    if raw and raw.get("brake_state") is not None:
        resolved["brake_state"] = _abs(root, str(raw["brake_state"]))
    else:
        resolved["brake_state"] = resolved["quota_state"]
    return PathsConfig(**resolved)


def _build_role(raw: Mapping[str, Any]) -> RoleConfig:
    allowed = frozenset(str(x) for x in (raw.get("allowed") or ()))
    default_model = {
        str(k): str(v) for k, v in dict(raw.get("default_model") or {}).items()
    }
    light_raw = raw.get("light_model") or {}
    light_model = {str(k): str(v) for k, v in dict(light_raw).items()}
    timeout = float(raw.get("timeout_sec") or 0)
    return RoleConfig(
        allowed=allowed,
        default_model=default_model,
        light_model=light_model,
        timeout_sec=timeout,
    )


def _build_engines(raw: Mapping[str, Any] | None) -> dict[str, RoleConfig]:
    base = {k: dict(v) for k, v in _DEFAULT_ENGINES.items()}
    if raw:
        for role, conf in raw.items():
            if not isinstance(conf, dict):
                continue
            merged = dict(base.get(str(role), {}))
            merged.update(conf)
            base[str(role)] = merged
    return {role: _build_role(conf) for role, conf in base.items()}


def _build_concurrency(raw: Mapping[str, Any] | None) -> ConcurrencyConfig:
    if not raw:
        return ConcurrencyConfig(default=1, per_engine={})
    default = int(raw.get("default") or 1)
    per_raw = raw.get("per_engine") or {}
    per_engine = {str(k): int(v) for k, v in dict(per_raw).items()}
    strict_order = bool(raw.get("strict_order", False))
    return ConcurrencyConfig(
        default=default, per_engine=per_engine, strict_order=strict_order
    )


def _build_milestone_chain(raw: Mapping[str, Any] | None) -> MilestoneChainConfig:
    if not raw:
        return MilestoneChainConfig()
    return MilestoneChainConfig(
        enabled=bool(raw.get("enabled", False)),
        child_priority=str(raw.get("child_priority") or "normal"),
        auto_develop=bool(raw.get("auto_develop", True)),
        auto_close_parent=bool(raw.get("auto_close_parent", True)),
    )


def _build_triage(raw: Mapping[str, Any] | None) -> TriageConfig:
    if not raw:
        return TriageConfig()
    defaults = TriageConfig()
    return TriageConfig(
        enabled=bool(raw.get("enabled", defaults.enabled)),
        engine=str(raw.get("engine") or defaults.engine),
        model=str(raw.get("model") or defaults.model),
        timeout=int(raw.get("timeout", defaults.timeout)),
        body_chars=int(raw.get("body_chars", defaults.body_chars)),
        circuit_breaker_threshold=int(
            raw.get("circuit_breaker_threshold", defaults.circuit_breaker_threshold)
        ),
        circuit_breaker_reset_seconds=int(
            raw.get(
                "circuit_breaker_reset_seconds",
                defaults.circuit_breaker_reset_seconds,
            )
        ),
    )


def _build_phases(raw: Any) -> tuple[PhaseConfig, ...]:
    if raw is None:
        return _DEFAULT_PHASES
    if not isinstance(raw, list):
        raise ValueError("phases must be a list of mappings")
    phases: list[PhaseConfig] = []
    for i, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"phases[{i}] must be a mapping")
        name = item.get("name")
        role = item.get("role")
        entry_step = item.get("entry_step")
        if not name or not role or not entry_step:
            raise ValueError(
                f"phases[{i}] requires non-empty name, role, and entry_step"
            )
        handler = str(item.get("handler") or "")
        raw_preconds = item.get("preconditions")
        if isinstance(raw_preconds, list):
            preconditions: tuple[str, ...] = tuple(str(x) for x in raw_preconds if x)
        else:
            preconditions = ()
        phases.append(
            PhaseConfig(
                name=str(name),
                role=str(role),
                entry_step=str(entry_step),
                handler=handler,
                preconditions=preconditions,
            )
        )
    if not phases:
        raise ValueError("phases must not be empty")
    return tuple(phases)


def _build_sections(raw: Mapping[str, Any] | None) -> dict[str, str]:
    sections = dict(_DEFAULT_SECTIONS)
    if raw:
        for key, value in raw.items():
            if value is None:
                continue
            sections[str(key)] = str(value)
    return sections


def _build_sub_design_subsections(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return _DEFAULT_SUB_DESIGN_SUBSECTIONS
    if not isinstance(raw, list):
        raise ValueError("sub_design_subsections must be a list of strings")
    return tuple(str(x) for x in raw)


def _build_steps(raw: Mapping[str, Any] | None) -> dict[str, StepConfig]:
    steps = dict(_DEFAULT_STEPS)
    if not raw:
        return steps
    _valid_input_kinds = {"issue", "worktree", "artifact"}
    for step_id, conf in raw.items():
        if not isinstance(conf, Mapping):
            raise ValueError(f"steps.{step_id} must be a mapping")
        module_raw = conf.get("module")
        # module is optional: absent/empty means LLM-only step (run via engine run-guarded)
        module = str(module_raw).strip() if module_raw else ""
        template_raw = conf.get("template")
        template = None if template_raw is None else str(template_raw)

        # requires / input_kind are optional (backward compat). Only the shape is checked here:
        # gate ids and gate/step input_kind compatibility are validated by
        # ``issuesmith.gates.validate_step_requires`` (doctor / dispatch / config show).
        # Loading the config must not import the gate registry: the registry pulls in every
        # gate rule, some of which call ``get_config()`` at import time, which re-enters this
        # loader while ``issuesmith.gates`` is half-initialised (sumipan/nexus#3687).
        requires: tuple[str, ...] = ()
        requires_declared: bool = False
        input_kind: str = "issue"
        if "requires" in conf:
            requires_raw = conf["requires"]
            if not isinstance(requires_raw, list):
                raise ConfigError(f"steps.{step_id}.requires must be a list")
            requires = tuple(str(g) for g in requires_raw)
            requires_declared = True
        if "input_kind" in conf:
            input_kind = str(conf["input_kind"])
            if input_kind not in _valid_input_kinds:
                raise ConfigError(
                    f"steps.{step_id}.input_kind must be one of"
                    f" {sorted(_valid_input_kinds)}, got {input_kind!r}"
                )

        steps[str(step_id)] = StepConfig(
            module=module,
            template=template,
            requires=requires,
            input_kind=input_kind,  # type: ignore[arg-type]
            requires_declared=requires_declared,
        )
    return steps


def _build_forbidden_pr_paths(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return _DEFAULT_FORBIDDEN_PR_PATHS
    if not isinstance(raw, list):
        raise ValueError("forbidden_pr_paths must be a list of strings")
    return tuple(str(x) for x in raw)


def _build_scope_gate(raw: Mapping[str, Any] | None) -> ScopeGateConfig:
    defaults = ScopeGateConfig()
    if not raw:
        return defaults
    return ScopeGateConfig(
        enabled=bool(raw.get("enabled", defaults.enabled)),
        max_files=int(raw.get("max_files", defaults.max_files)),
        max_lines=int(raw.get("max_lines", defaults.max_lines)),
        hard_max_files=int(raw.get("hard_max_files", defaults.hard_max_files)),
    )


def _build_terminal_labels(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return _DEFAULT_TERMINAL_LABELS
    if not isinstance(raw, list):
        raise ValueError("terminal_labels must be a list of strings")
    return tuple(str(x) for x in raw)


def _build_scope_coupling(raw: Mapping[str, Any] | None) -> ScopeCouplingConfig:
    if not raw:
        return ScopeCouplingConfig()
    if "ignore_symbols" in raw:
        raise ConfigError(
            "scope_coupling.ignore_symbols is no longer supported; "
            "remove it from issuesmith.yaml (requires-chain validation replaced it)"
        )
    enabled_raw = raw.get("enabled")
    enabled = True if enabled_raw is None else bool(enabled_raw)
    search_dirs: tuple[str, ...] = ("tests", "src")
    if "search_dirs" in raw:
        sd_raw = raw["search_dirs"]
        if not isinstance(sd_raw, list) or not sd_raw:
            raise ConfigError(
                "scope_coupling.search_dirs must be a non-empty list of directory names"
            )
        stripped = [str(x).strip() for x in sd_raw]
        if any(not s for s in stripped):
            raise ConfigError(
                "scope_coupling.search_dirs must be a non-empty list of directory names"
            )
        seen: dict[str, None] = {}
        for s in stripped:
            seen[s] = None
        search_dirs = tuple(seen.keys())
    return ScopeCouplingConfig(enabled=enabled, search_dirs=search_dirs)


def _build_observe(raw: Mapping[str, Any] | None) -> ObserveConfig:
    defaults = ObserveConfig()
    if not raw:
        return defaults
    return ObserveConfig(
        stall_minutes=int(raw.get("stall_minutes", defaults.stall_minutes)),
        task_timeout_minutes=int(raw.get("task_timeout_minutes", defaults.task_timeout_minutes)),
        systemic_min_issues=int(raw.get("systemic_min_issues", defaults.systemic_min_issues)),
        systemic_window_minutes=int(raw.get("systemic_window_minutes", defaults.systemic_window_minutes)),
        forge_max_consecutive_errors=int(
            raw.get("forge_max_consecutive_errors", defaults.forge_max_consecutive_errors)
        ),
        max_api_calls=int(raw.get("max_api_calls", defaults.max_api_calls)),
    )


def _build_api_brake(raw: Mapping[str, Any] | None) -> ApiBreakConfig:
    defaults = ApiBreakConfig()
    if not raw:
        return defaults
    return ApiBreakConfig(
        enabled=bool(raw.get("enabled", defaults.enabled)),
        min_remaining=int(raw.get("min_remaining", defaults.min_remaining)),
    )


def _build_config(data: Mapping[str, Any], *, root: Path) -> IssuesmithConfig:
    repo_raw = data.get("repo")
    if not repo_raw or not str(repo_raw).strip():
        raise ValueError(
            "issuesmith.yaml に repo: owner/name を設定してください"
        )
    repo = str(repo_raw).strip()
    label_namespace = str(data.get("label_namespace") or "issuesmith")
    timezone = str(data.get("timezone") or "Asia/Tokyo")
    supported_raw = data.get("supported_repos")
    if supported_raw is None:
        supported = _DEFAULT_SUPPORTED_REPOS
    else:
        supported = frozenset(str(x) for x in supported_raw)
    paths_raw = data.get("paths") if isinstance(data.get("paths"), dict) else None
    engines_raw = data.get("engines") if isinstance(data.get("engines"), dict) else None
    concurrency_raw = data.get("concurrency") if isinstance(data.get("concurrency"), dict) else None
    milestone_raw = data.get("milestone_chain") if isinstance(data.get("milestone_chain"), dict) else None
    triage_raw = data.get("triage") if isinstance(data.get("triage"), dict) else None
    sections_raw = data.get("sections") if isinstance(data.get("sections"), dict) else None
    steps_raw = data.get("steps") if isinstance(data.get("steps"), dict) else None
    scope_gate_raw = (
        data.get("scope_gate") if isinstance(data.get("scope_gate"), dict) else None
    )
    scope_coupling_raw = (
        data.get("scope_coupling") if isinstance(data.get("scope_coupling"), dict) else None
    )
    observe_raw = data.get("observe") if isinstance(data.get("observe"), dict) else None
    api_brake_raw = (
        data.get("api_brake") if isinstance(data.get("api_brake"), dict) else None
    )
    return IssuesmithConfig(
        repo=repo,
        label_namespace=label_namespace,
        timezone=timezone,
        supported_repos=supported,
        root=root.resolve(),
        paths=_build_paths(paths_raw, root.resolve()),
        engines=_build_engines(engines_raw),
        concurrency=_build_concurrency(concurrency_raw),
        milestone_chain=_build_milestone_chain(milestone_raw),
        triage=_build_triage(triage_raw),
        phases=_build_phases(data.get("phases")),
        sections=_build_sections(sections_raw),
        sub_design_subsections=_build_sub_design_subsections(
            data.get("sub_design_subsections")
        ),
        steps=_build_steps(steps_raw),
        forbidden_pr_paths=_build_forbidden_pr_paths(data.get("forbidden_pr_paths")),
        scope_gate=_build_scope_gate(scope_gate_raw),
        scope_coupling=_build_scope_coupling(scope_coupling_raw),
        terminal_labels=_build_terminal_labels(data.get("terminal_labels")),
        observe=_build_observe(observe_raw),
        api_brake=_build_api_brake(api_brake_raw),
    )
