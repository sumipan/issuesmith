"""issuesmith instance configuration (issuesmith.yaml).

Resolves nexus-specific values (repo name, paths, engines, timezone) from a
config file so the package can be reused outside this repository.
"""

from __future__ import annotations

import os
import shlex
import warnings
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Mapping

import yaml

from issuesmith.language import EN, LanguagePack, load_language_pack


class ConfigError(ValueError):
    """Raised when issuesmith configuration is invalid."""

_CONFIG_ENV = "ISSUESMITH_CONFIG"
_CONFIG_FILENAME = "issuesmith.yaml"

# src/issuesmith/config.py → parents[2] == sumipan/issuesmith repo root
_PACKAGE_FILE = Path(__file__).resolve()


def _package_fallback_yaml() -> Path:
    return _PACKAGE_FILE.parents[2] / _CONFIG_FILENAME


# Host-specific repositories go in issuesmith.yaml ``supported_repos:``.
# The package default is empty (every cross-repo check is rejected until set).
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
            # gpt-5.4-mini returns 400 on codex with ChatGPT account auth and was
            # dropped from the nexus allowlist (2026-09-09, B1 light tier stopped twice).
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
    # For the budget brake. When unset, _build_paths falls back to quota_state.
    # Defaults to None for manually built test configs (None means quota_state).
    brake_state: Path | None = None
    lanes: Path | None = None


_AUTO_ANSWER_MATCH_KEYS = frozenset({"step", "kind", "rule_id"})


@dataclass(frozen=True)
class AutoAnswerRule:
    name: str
    match: Mapping[str, str]
    action: str
    max_per_issue: int = 1


@dataclass(frozen=True)
class AndonConfig:
    auto_answer: tuple[AutoAnswerRule, ...] = ()


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
    excludes: tuple[str, ...] = ()
    writes_files: bool = True
    advance_when: tuple[str, ...] = ()
    # Steps of the phase in run order; empty means ``(entry_step,)`` (#4807).
    steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class StepConfig:
    module: str = ""
    template: str | None = None
    requires: tuple[str, ...] = ()
    input_kind: Literal["issue", "worktree", "artifact"] = "issue"
    requires_declared: bool = False
    accepts: tuple[str, ...] = ()
    andon_when: tuple[str, ...] = ()


_DEFAULT_STEPS: dict[str, StepConfig] = {
    "p1": StepConfig(andon_when=("external_leak.target_unknown",)),
}

_DEFAULT_HANDLER_BY_PHASE: dict[str, str] = {
    "draft": "brushup",
    "sub": "subissue",
    "develop": "impl",
    "merge": "merge",
}

_DEFAULT_PHASES: tuple[PhaseConfig, ...] = (
    PhaseConfig(
        name="draft",
        role="design",
        entry_step="b1",
        handler="brushup",
        writes_files=False,
        advance_when=("deps_terminal",),
    ),
    PhaseConfig(
        name="sub",
        role="implementation",
        entry_step="sub-ready",
        handler="subissue",
        writes_files=False,
        advance_when=("deps_terminal",),
    ),
    PhaseConfig(
        name="develop",
        role="implementation",
        entry_step="cp2",
        handler="impl",
        advance_when=("deps_terminal", "pins_landed"),
    ),
    PhaseConfig(
        name="merge",
        role="implementation",
        entry_step="m2",
        handler="merge",
        advance_when=("deps_terminal", "closing_pr_exists"),
    ),
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
    ``data_file_tests`` makes the file name of a modified data / config file a required key
    so tests pinning its contents join allow_paths (nexus #3949).
    """

    enabled: bool = True
    search_dirs: tuple[str, ...] = ("tests", "src")
    data_file_tests: bool = True


@dataclass(frozen=True)
class ScopeSizeConfig:
    """B1 Issue size gate (nexus #3665): limits read from the Issue's change table."""

    enabled: bool = True
    max_files: int = 8
    max_concerns: int = 2
    delete_with_new: bool = False
    exclude_prefixes: tuple[str, ...] = (
        "tests/", "docs/", "README.md", "CHANGELOG.md", "pyproject.toml",
    )
    # Vocabulary of the host's Issue bodies, derived from the language pack
    # (``Config.language``). The legacy ``scope_size.*`` keys still override it for
    # one release. Kind words are matched case-insensitively.
    delete_words: tuple[str, ...] = EN.delete_words
    new_words: tuple[str, ...] = EN.new_words
    sub_plan_header: str = EN.sub_plan_header
    no_deps_word: str = EN.no_deps_word


@dataclass(frozen=True)
class MetricsConfig:
    """Rework metrics options (``issuesmith.yaml`` ``metrics:`` section, #4431)."""

    done_step: str = "m2"
    repair_templates: tuple[str, ...] = ()
    cause_targets: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TestsConfig:
    """Pytest gate options (``issuesmith.yaml`` ``tests:`` section)."""

    flaky_reruns: int = 2


@dataclass(frozen=True)
class DerivedAllowConfig:
    """Derived allow_paths for newly failing tests in the requires loop (#3756).

    ``enabled: false`` restores the strict allow_paths-only behaviour.
    ``ledger_globs``: ratchet ledgers next to a newly failing test that repair may
    shrink (#4791). An empty tuple disables ledger derivation.
    """

    enabled: bool = True
    ledger_globs: tuple[str, ...] = ("tests/conventions/known_*.txt",)


@dataclass(frozen=True)
class ExternalLeakConfig:
    """Options of the ``external_leak`` worktree gate.

    ``cjk_free_external_targets``: when true, added lines of a branch whose Issue
    targets another repository (``target_repo`` differs from ``repo``) must not
    contain CJK characters (literal or ``\\uXXXX`` escaped). Hosts that keep their
    public repositories English-only enable this so P1's repair loop fixes the lines
    instead of a later publish check stopping the pipeline.
    """

    cjk_free_external_targets: bool = False


_DEFAULT_TERMINAL_LABELS: tuple[str, ...] = ("issuesmith:merge-done", "bump:done")

_DEFAULT_TERMINAL_WITHOUT_MERGE: tuple[str, ...] = (
    "rejected",
    "superseded",
    "sub-ready",
    "sub-done",
)



@dataclass(frozen=True)
class MainHealthConfig:
    """Periodic base-branch test run (issuesmith.yaml observe.main_health:, #3664)."""

    worktree: Path
    command: tuple[str, ...]
    base_branch: str = "main"
    timeout_seconds: int = 1800


@dataclass(frozen=True)
class ObserveConfig:
    """Configuration for the observe layer (issuesmith.yaml observe: section)."""

    stall_minutes: int = 120
    task_timeout_minutes: int = 90
    systemic_min_issues: int = 2
    systemic_window_minutes: int = 60
    forge_max_consecutive_errors: int = 3
    max_api_calls: int = 8
    main_health: MainHealthConfig | None = None


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
    sections: Mapping[str, str] = field(default_factory=lambda: dict(EN.sections))
    sub_design_subsections: tuple[str, ...] = EN.sub_design_subsections
    steps: Mapping[str, StepConfig] = field(default_factory=lambda: dict(_DEFAULT_STEPS))
    forbidden_pr_paths: tuple[str, ...] = _DEFAULT_FORBIDDEN_PR_PATHS
    scope_gate: ScopeGateConfig = field(default_factory=ScopeGateConfig)
    scope_coupling: ScopeCouplingConfig = field(default_factory=ScopeCouplingConfig)
    scope_size: ScopeSizeConfig = field(default_factory=ScopeSizeConfig)
    tests: TestsConfig = field(default_factory=TestsConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    derived_allow: DerivedAllowConfig = field(default_factory=DerivedAllowConfig)
    external_leak: ExternalLeakConfig = field(default_factory=ExternalLeakConfig)
    terminal_labels: tuple[str, ...] = _DEFAULT_TERMINAL_LABELS
    terminal_without_merge: tuple[str, ...] = ()
    observe: ObserveConfig = field(default_factory=ObserveConfig)
    api_brake: ApiBreakConfig = field(default_factory=ApiBreakConfig)
    language: LanguagePack = EN
    label_write_guard: Literal["warn", "enforce"] = "warn"
    installs: Mapping[str, Path] = field(default_factory=dict)
    andon: AndonConfig = field(default_factory=AndonConfig)

    def phase(self, name: str) -> PhaseConfig:
        for ph in self.phases:
            if ph.name == name:
                return ph
        raise KeyError(name)

    def phase_for_handler(self, handler: str) -> str | None:
        for ph in self.phases:
            if ph.handler == handler:
                return ph.name
        return None

    def design_phase(self) -> PhaseConfig | None:
        design_phases = [ph for ph in self.phases if ph.role == "design"]
        if len(design_phases) > 1:
            raise ConfigError("multiple design phases declared")
        return design_phases[0] if design_phases else None

    def _expand_label(self, label: str) -> str:
        if ":" in label:
            return label
        return f"{self.label_namespace}:{label}"

    def phase_labels(self, name: str) -> tuple[str, str, str]:
        ph = self.phase(name)
        ns = self.label_namespace
        return (
            f"{ns}:{ph.name}-ready",
            f"{ns}:{ph.name}-running",
            f"{ns}:{ph.name}-done",
        )

    def required_labels(self, name: str) -> frozenset[str]:
        ph = self.phase(name)
        return frozenset(self._expand_label(x) for x in ph.preconditions)

    def excluded_labels(self, name: str) -> frozenset[str]:
        ph = self.phase(name)
        return frozenset(self._expand_label(x) for x in ph.excludes)


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
    # brake_state has no fixed default: when unset it falls back to quota_state of the
    # same config (existing users keep a single gate).
    if raw and raw.get("brake_state") is not None:
        resolved["brake_state"] = _abs(root, str(raw["brake_state"]))
    else:
        resolved["brake_state"] = resolved["quota_state"]
    lanes: Path | None = None
    if raw and raw.get("lanes") is not None:
        lanes = _abs(root, str(raw["lanes"]))
    return PathsConfig(**resolved, lanes=lanes)


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


def _label_list(raw: Any, *, field: str, index: int) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"phases[{index}].{field} must be a list of strings")
    return tuple(str(x) for x in raw if x)


def _build_phases(raw: Any) -> tuple[PhaseConfig, ...]:
    if raw is None:
        return _DEFAULT_PHASES
    if not isinstance(raw, list):
        raise ValueError("phases must be a list of mappings")
    import issuesmith.pins  # noqa: F401  (registers pins_landed)
    from issuesmith.preconditions import PRECONDITION_REGISTRY

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
        handler = str(item.get("handler") or "").strip()
        if not handler:
            handler = _DEFAULT_HANDLER_BY_PHASE.get(str(name), "")
        if not handler:
            raise ConfigError(f"phases[{i}].handler is required")
        preconditions = _label_list(item.get("preconditions"), field="preconditions", index=i)
        excludes = _label_list(item.get("excludes"), field="excludes", index=i)
        writes_files = bool(item.get("writes_files", True))
        advance_when = _label_list(item.get("advance_when"), field="advance_when", index=i)
        for pred in advance_when:
            if pred not in PRECONDITION_REGISTRY:
                raise ConfigError(
                    f"phases[{i}].advance_when references unknown predicate {pred!r}"
                )
        raw_steps = item.get("steps")
        if raw_steps is None:
            steps: tuple[str, ...] = ()
        elif isinstance(raw_steps, list):
            steps = tuple(str(x) for x in raw_steps if x)
        else:
            raise ValueError(f"phases[{i}].steps must be a list of step ids")
        phases.append(
            PhaseConfig(
                name=str(name),
                role=str(role),
                entry_step=str(entry_step),
                handler=handler,
                preconditions=preconditions,
                excludes=excludes,
                writes_files=writes_files,
                advance_when=advance_when,
                steps=steps,
            )
        )
    if not phases:
        raise ValueError("phases must not be empty")
    owner: dict[str, str] = {}
    for phase in phases:
        for step in phase.steps or (phase.entry_step,):
            if step in owner and owner[step] != phase.name:
                raise ConfigError(
                    f"step {step!r} is declared by phases {owner[step]!r} and {phase.name!r}"
                )
            owner[step] = phase.name
    return tuple(phases)


def _build_label_write_guard(raw: Any) -> Literal["warn", "enforce"]:
    if raw is None:
        return "warn"
    if raw == "warn" or raw == "enforce":
        return raw
    raise ConfigError(f"label_write_guard must be 'warn' or 'enforce', got {raw!r}")


def _build_sections(raw: Mapping[str, Any] | None, base: Mapping[str, str]) -> dict[str, str]:
    sections = dict(base)
    if raw:
        for key, value in raw.items():
            if value is None:
                continue
            sections[str(key)] = str(value)
    return sections


def _build_sub_design_subsections(raw: Any, base: tuple[str, ...]) -> tuple[str, ...]:
    if raw is None:
        return base
    if not isinstance(raw, list):
        raise ValueError("sub_design_subsections must be a list of strings")
    return tuple(str(x) for x in raw)


# Legacy vocabulary keys superseded by ``language_pack`` (read for one release).
_LEGACY_SCOPE_SIZE_VOCAB: tuple[str, ...] = (
    "delete_words", "new_words", "sub_plan_header", "no_deps_word",
)


def _legacy_vocab_keys(data: Mapping[str, Any]) -> list[str]:
    keys = [k for k in ("sections", "sub_design_subsections") if data.get(k) is not None]
    scope_size_raw = data.get("scope_size")
    if isinstance(scope_size_raw, Mapping):
        keys += [f"scope_size.{k}" for k in _LEGACY_SCOPE_SIZE_VOCAB if k in scope_size_raw]
    return keys


def _validate_legacy_scope_size_vocab(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate legacy ``scope_size`` vocabulary keys; return the normalized values present."""
    out: dict[str, Any] = {}
    for key in ("delete_words", "new_words"):
        if key not in raw:
            continue
        value = raw[key]
        if (
            not isinstance(value, (list, tuple))
            or not value
            or not all(isinstance(x, str) and x.strip() for x in value)
        ):
            raise ConfigError(f"scope_size.{key} must be a non-empty list of strings")
        out[key] = tuple(x.strip().lower() for x in value)
    for key in ("sub_plan_header", "no_deps_word"):
        if key not in raw:
            continue
        value = raw[key]
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"scope_size.{key} must be a non-empty string")
        out[key] = value.strip()
    return out


def _header_columns(header: str) -> tuple[str, ...]:
    """``| a | b |`` -> ``("a", "b")``."""
    return tuple(cell.strip() for cell in header.strip().strip("|").split("|"))


def _build_language(data: Mapping[str, Any], *, root: Path) -> LanguagePack:
    """Resolve ``Config.language``: ``language_pack`` file, else EN plus legacy keys.

    Legacy keys (``sections``, ``sub_design_subsections`` and the ``scope_size``
    vocabulary) emit a ``DeprecationWarning``. With ``language_pack`` set they are
    ignored; without it they override the matching :data:`EN` fields.
    """
    pack_raw = data.get("language_pack")
    legacy = _legacy_vocab_keys(data)
    scope_size_raw = data.get("scope_size")
    legacy_scope = (
        _validate_legacy_scope_size_vocab(scope_size_raw)
        if isinstance(scope_size_raw, Mapping)
        else {}
    )
    if pack_raw is not None:
        if not isinstance(pack_raw, str) or not pack_raw.strip():
            raise ConfigError("language_pack must be a path string")
        pack = load_language_pack(_abs(root, pack_raw.strip()))
        if legacy:
            warnings.warn(
                f"issuesmith.yaml: {', '.join(legacy)} ignored because language_pack is set;"
                " remove them (they will be dropped in the next release)",
                DeprecationWarning,
                stacklevel=2,
            )
        return pack
    if not legacy:
        return EN
    warnings.warn(
        f"issuesmith.yaml: {', '.join(legacy)} are deprecated; move them to a"
        " language_pack file (they will be dropped in the next release)",
        DeprecationWarning,
        stacklevel=2,
    )
    sections_raw = data.get("sections") if isinstance(data.get("sections"), dict) else None
    overrides: dict[str, Any] = {
        "sections": _build_sections(sections_raw, EN.sections),
        "sub_design_subsections": _build_sub_design_subsections(
            data.get("sub_design_subsections"), EN.sub_design_subsections
        ),
    }
    for key in ("delete_words", "new_words", "no_deps_word"):
        if key in legacy_scope:
            overrides[key] = legacy_scope[key]
    if "sub_plan_header" in legacy_scope:
        overrides["sub_plan_columns"] = _header_columns(legacy_scope["sub_plan_header"])
    return replace(EN, **overrides)


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
        accepts: tuple[str, ...] = ()
        if "accepts" in conf:
            accepts_raw = conf["accepts"]
            if not isinstance(accepts_raw, list):
                raise ConfigError(f"steps.{step_id}.accepts must be a list")
            accepts = tuple(str(x) for x in accepts_raw)
        andon_when: tuple[str, ...] = ()
        if "andon_when" in conf:
            andon_raw = conf["andon_when"]
            if not isinstance(andon_raw, list):
                raise ConfigError(f"steps.{step_id}.andon_when must be a list")
            andon_when = tuple(str(x) for x in andon_raw)

        steps[str(step_id)] = StepConfig(
            module=module,
            template=template,
            requires=requires,
            input_kind=input_kind,  # type: ignore[arg-type]
            requires_declared=requires_declared,
            accepts=accepts,
            andon_when=andon_when,
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
        # Kept even when phases are declared; emptying the default is #4881.
        return _DEFAULT_TERMINAL_LABELS
    if not isinstance(raw, list):
        raise ValueError("terminal_labels must be a list of strings")
    return tuple(str(x) for x in raw)


def _build_terminal_without_merge(raw: Any, *, using_default_phases: bool = False) -> tuple[str, ...]:
    if raw is None:
        return _DEFAULT_TERMINAL_WITHOUT_MERGE if using_default_phases else ()
    if not isinstance(raw, list):
        raise ValueError("terminal_without_merge must be a list of strings")
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
    dft_raw = raw.get("data_file_tests")
    data_file_tests = True if dft_raw is None else bool(dft_raw)
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
    return ScopeCouplingConfig(
        enabled=enabled, search_dirs=search_dirs, data_file_tests=data_file_tests
    )


def _build_scope_size(
    raw: Mapping[str, Any] | None, language: LanguagePack = EN
) -> ScopeSizeConfig:
    vocab: dict[str, Any] = {
        "delete_words": tuple(w.strip().lower() for w in language.delete_words),
        "new_words": tuple(w.strip().lower() for w in language.new_words),
        "sub_plan_header": language.sub_plan_header,
        "no_deps_word": language.no_deps_word,
    }
    defaults = ScopeSizeConfig(**vocab)
    if not raw:
        return defaults
    limits: dict[str, int] = {}
    for key in ("max_files", "max_concerns"):
        value = raw.get(key, getattr(defaults, key))
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ConfigError(f"scope_size.{key} must be an integer >= 1 (got {value!r})")
        limits[key] = value
    exclude_prefixes = defaults.exclude_prefixes
    if "exclude_prefixes" in raw:
        ex_raw = raw["exclude_prefixes"]
        if not isinstance(ex_raw, list) or not all(isinstance(x, str) for x in ex_raw):
            raise ConfigError("scope_size.exclude_prefixes must be a list of strings")
        exclude_prefixes = tuple(ex_raw)
    return ScopeSizeConfig(
        enabled=bool(raw.get("enabled", defaults.enabled)),
        max_files=limits["max_files"],
        max_concerns=limits["max_concerns"],
        delete_with_new=bool(raw.get("delete_with_new", defaults.delete_with_new)),
        exclude_prefixes=exclude_prefixes,
        **vocab,
    )


def _build_metrics(raw: Mapping[str, Any] | None) -> MetricsConfig:
    defaults = MetricsConfig()
    if not raw:
        return defaults
    allowed = {"done_step", "repair_templates", "cause_targets"}
    unknown = sorted(str(k) for k in raw if k not in allowed)
    if unknown:
        raise ConfigError(
            f"metrics supports only {sorted(allowed)}; unknown keys: {unknown}"
        )
    done_step = str(raw.get("done_step", defaults.done_step)).strip() or defaults.done_step
    if "repair_templates" in raw:
        templates_raw = raw["repair_templates"]
        if not isinstance(templates_raw, list) or not all(isinstance(x, str) for x in templates_raw):
            raise ConfigError("metrics.repair_templates must be a list of strings")
        repair_templates = tuple(str(x) for x in templates_raw)
    else:
        repair_templates = defaults.repair_templates
    targets_raw = raw.get("cause_targets", {})
    if not isinstance(targets_raw, Mapping):
        raise ConfigError("metrics.cause_targets must be a mapping")
    cause_targets = {str(k): str(v) for k, v in targets_raw.items()}
    return MetricsConfig(
        done_step=done_step,
        repair_templates=repair_templates,
        cause_targets=cause_targets,
    )


def _build_tests(raw: Mapping[str, Any] | None) -> TestsConfig:
    defaults = TestsConfig()
    if not raw:
        return defaults
    unknown = sorted(str(k) for k in raw if k != "flaky_reruns")
    if unknown:
        raise ConfigError(
            f"tests supports only 'flaky_reruns'; unknown keys: {unknown}"
        )
    reruns_raw = raw.get("flaky_reruns", defaults.flaky_reruns)
    if isinstance(reruns_raw, bool) or not isinstance(reruns_raw, int) or reruns_raw < 0:
        raise ConfigError("tests.flaky_reruns must be an integer >= 0")
    return TestsConfig(flaky_reruns=reruns_raw)


def _build_derived_allow(raw: Mapping[str, Any] | None) -> DerivedAllowConfig:
    if not raw:
        return DerivedAllowConfig()
    unknown = sorted(str(k) for k in raw if k not in ("enabled", "ledger_globs"))
    if unknown:
        raise ConfigError(
            f"derived_allow supports only 'enabled' and 'ledger_globs'; unknown keys: {unknown}"
        )
    enabled_raw = raw.get("enabled")
    enabled = True if enabled_raw is None else bool(enabled_raw)
    if "ledger_globs" not in raw:
        return DerivedAllowConfig(enabled=enabled)
    globs_raw = raw["ledger_globs"]
    if not isinstance(globs_raw, list) or not all(isinstance(g, str) for g in globs_raw):
        raise ConfigError("derived_allow.ledger_globs must be a list of strings")
    return DerivedAllowConfig(enabled=enabled, ledger_globs=tuple(globs_raw))


def _build_external_leak(raw: Mapping[str, Any] | None) -> ExternalLeakConfig:
    if not raw:
        return ExternalLeakConfig()
    unknown = sorted(str(k) for k in raw if k != "cjk_free_external_targets")
    if unknown:
        raise ConfigError(
            f"external_leak supports only 'cjk_free_external_targets'; unknown keys: {unknown}"
        )
    return ExternalLeakConfig(cjk_free_external_targets=bool(raw.get("cjk_free_external_targets")))


def _build_main_health(raw: Any, root: Path) -> MainHealthConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("observe.main_health must be a mapping")
    worktree_raw = str(raw.get("worktree") or "").strip()
    if not worktree_raw:
        raise ValueError("observe.main_health.worktree is required")
    command_raw = raw.get("command")
    if isinstance(command_raw, str):
        command = tuple(shlex.split(command_raw))
    elif isinstance(command_raw, (list, tuple)):
        command = tuple(str(x) for x in command_raw)
    else:
        command = ()
    if not command:
        raise ValueError("observe.main_health.command is required")
    worktree = Path(worktree_raw).expanduser()
    if not worktree.is_absolute():
        worktree = root / worktree
    return MainHealthConfig(
        worktree=worktree,
        command=command,
        base_branch=str(raw.get("base_branch") or "main"),
        timeout_seconds=int(raw.get("timeout_seconds", 1800)),
    )


def _build_observe(
    raw: Mapping[str, Any] | None, root: Path | None = None,
) -> ObserveConfig:
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
        main_health=_build_main_health(raw.get("main_health"), root or Path.cwd()),
    )


def _build_installs(raw: Any, root: Path) -> dict[str, Path]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError("installs must be a mapping")
    out: dict[str, Path] = {}
    for key, value in raw.items():
        if not isinstance(value, str):
            raise ConfigError(f"installs[{key!r}] must be a string path")
        out[str(key)] = _abs(root, value)
    return out


def _build_andon(raw: Mapping[str, Any] | None) -> AndonConfig:
    if not raw:
        return AndonConfig()
    auto_raw = raw.get("auto_answer")
    if auto_raw is None:
        return AndonConfig()
    if not isinstance(auto_raw, list):
        raise ConfigError("andon.auto_answer must be a list")
    rules: list[AutoAnswerRule] = []
    seen_names: set[str] = set()
    for idx, item in enumerate(auto_raw):
        if not isinstance(item, dict):
            raise ConfigError(f"andon.auto_answer[{idx}] must be a mapping")
        name = str(item.get("name") or "").strip()
        if not name:
            raise ConfigError(f"andon.auto_answer[{idx}].name is required")
        if name in seen_names:
            raise ConfigError(f"andon.auto_answer: duplicate name {name!r}")
        seen_names.add(name)
        match_raw = item.get("match")
        if not isinstance(match_raw, dict) or not match_raw:
            raise ConfigError(f"andon.auto_answer[{name!r}].match must be a non-empty mapping")
        match: dict[str, str] = {}
        for key, value in match_raw.items():
            key_s = str(key)
            if key_s not in _AUTO_ANSWER_MATCH_KEYS:
                raise ConfigError(
                    f"andon.auto_answer[{name!r}].match: unknown key {key_s!r}"
                )
            match[key_s] = str(value)
        action = str(item.get("action") or "").strip()
        if not action:
            raise ConfigError(f"andon.auto_answer[{name!r}].action is required")
        max_per_issue = int(item.get("max_per_issue", 1))
        if max_per_issue < 1:
            raise ConfigError(f"andon.auto_answer[{name!r}].max_per_issue must be >= 1")
        rules.append(
            AutoAnswerRule(
                name=name,
                match=match,
                action=action,
                max_per_issue=max_per_issue,
            )
        )
    return AndonConfig(auto_answer=tuple(rules))


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
            "issuesmith.yaml must set repo: owner/name"
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
    steps_raw = data.get("steps") if isinstance(data.get("steps"), dict) else None
    scope_gate_raw = (
        data.get("scope_gate") if isinstance(data.get("scope_gate"), dict) else None
    )
    scope_coupling_raw = (
        data.get("scope_coupling") if isinstance(data.get("scope_coupling"), dict) else None
    )
    scope_size_raw = (
        data.get("scope_size") if isinstance(data.get("scope_size"), dict) else None
    )
    tests_raw = data.get("tests") if isinstance(data.get("tests"), dict) else None
    metrics_raw = data.get("metrics") if isinstance(data.get("metrics"), dict) else None
    derived_allow_raw = (
        data.get("derived_allow") if isinstance(data.get("derived_allow"), dict) else None
    )
    external_leak_raw = (
        data.get("external_leak") if isinstance(data.get("external_leak"), dict) else None
    )
    observe_raw = data.get("observe") if isinstance(data.get("observe"), dict) else None
    api_brake_raw = (
        data.get("api_brake") if isinstance(data.get("api_brake"), dict) else None
    )
    andon_raw = data.get("andon") if isinstance(data.get("andon"), dict) else None
    language = _build_language(data, root=root.resolve())
    using_default_phases = data.get("phases") is None
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
        sections=dict(language.sections),
        sub_design_subsections=language.sub_design_subsections,
        steps=_build_steps(steps_raw),
        forbidden_pr_paths=_build_forbidden_pr_paths(data.get("forbidden_pr_paths")),
        scope_gate=_build_scope_gate(scope_gate_raw),
        scope_coupling=_build_scope_coupling(scope_coupling_raw),
        scope_size=_build_scope_size(scope_size_raw, language),
        tests=_build_tests(tests_raw),
        metrics=_build_metrics(metrics_raw),
        derived_allow=_build_derived_allow(derived_allow_raw),
        external_leak=_build_external_leak(external_leak_raw),
        terminal_labels=_build_terminal_labels(data.get("terminal_labels")),
        terminal_without_merge=_build_terminal_without_merge(
            data.get("terminal_without_merge"),
            using_default_phases=using_default_phases,
        ),
        observe=_build_observe(observe_raw, root.resolve()),
        api_brake=_build_api_brake(api_brake_raw),
        language=language,
        label_write_guard=_build_label_write_guard(data.get("label_write_guard")),
        installs=_build_installs(data.get("installs"), root.resolve()),
        andon=_build_andon(andon_raw),
    )
