"""issuesmith instance configuration (issuesmith.yaml).

Resolves nexus-specific values (repo name, paths, engines, timezone) from a
config file so the package can be reused outside this repository.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

_CONFIG_ENV = "ISSUESMITH_CONFIG"
_CONFIG_FILENAME = "issuesmith.yaml"

# src/issuesmith/config.py → parents[2] == sumipan/issuesmith repo root
_PACKAGE_FILE = Path(__file__).resolve()


def _package_fallback_yaml() -> Path:
    return _PACKAGE_FILE.parents[2] / _CONFIG_FILENAME


_DEFAULT_SUPPORTED_REPOS: frozenset[str] = frozenset(
    {
        "sumipan/nexus",
        "sumipan/mltgnt",
        "sumipan/mltgnt-vscode-extension",
        "sumipan/ghdag",
        "sumipan/slack-project",
        "sumipan/diary",
        "sumipan/nexus-companion",
        "sumipan/okr-core",
        "sumipan/issuesmith",
    }
)

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
            "codex": "gpt-5.4-mini",
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


@dataclass(frozen=True)
class RoleConfig:
    allowed: frozenset[str]
    default_model: Mapping[str, str]
    timeout_sec: float
    light_model: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class IssuesmithConfig:
    repo: str
    label_namespace: str
    timezone: str
    supported_repos: frozenset[str]
    root: Path
    paths: PathsConfig
    engines: Mapping[str, RoleConfig]


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
    return PathsConfig(**{k: _abs(root, v) for k, v in src.items()})


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


def _build_config(data: Mapping[str, Any], *, root: Path) -> IssuesmithConfig:
    repo = str(data.get("repo") or "sumipan/nexus")
    label_namespace = str(data.get("label_namespace") or "issuesmith")
    timezone = str(data.get("timezone") or "Asia/Tokyo")
    supported_raw = data.get("supported_repos")
    if supported_raw is None:
        supported = _DEFAULT_SUPPORTED_REPOS
    else:
        supported = frozenset(str(x) for x in supported_raw)
    paths_raw = data.get("paths") if isinstance(data.get("paths"), dict) else None
    engines_raw = data.get("engines") if isinstance(data.get("engines"), dict) else None
    return IssuesmithConfig(
        repo=repo,
        label_namespace=label_namespace,
        timezone=timezone,
        supported_repos=supported,
        root=root.resolve(),
        paths=_build_paths(paths_raw, root.resolve()),
        engines=_build_engines(engines_raw),
    )
