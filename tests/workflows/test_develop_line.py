"""Reference develop line acceptance tests."""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml
from ghdag.forge import get_forge

from issuesmith.andon import list_open
from issuesmith.config import RepairConfig, ReviewConfig, StepConfig, reset_config_cache
from issuesmith.contract import StepContext
from issuesmith.language import EN
from issuesmith.lines import LINES, extract_line
from issuesmith.lines.develop import prepare as develop_prepare
from issuesmith.lines.develop.hooks import DevelopLineHooks

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE_FILES = _REPO_ROOT / "src" / "issuesmith" / "lines" / "develop" / "files"
_LINE_DEV_DIR = _REPO_ROOT / "src" / "issuesmith" / "lines" / "develop"

_ALLOWED_ISSMITH_IMPORTS = frozenset(
    {
        "issuesmith.contract",
        "issuesmith.verbs",
        "issuesmith.gates",
        "issuesmith.config",
        "issuesmith.andon",
        "issuesmith.forge",
    }
)

_FAKE_AGENT = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    set -euo pipefail
    PROMPT=""
    if [[ ! -t 0 ]]; then
      PROMPT="$(cat)"
    fi
    while [[ $# -gt 0 ]]; do
      case "$1" in
        -p|--print|--prompt)
          if [[ $# -ge 2 && "$2" != -* ]]; then
            PROMPT="$2"
            shift 2
          else
            shift
          fi
          ;;
        --model|--output-format|--sandbox)
          shift 2
          ;;
        --stream-partial-output|--force)
          shift
          ;;
        --resume)
          shift 2
          ;;
        *)
          shift
          ;;
      esac
    done
    MODE_FILE="$(dirname "$0")/fake-engine-mode"
    MODE="good"
    if [[ -f "$MODE_FILE" ]]; then
      MODE="$(cat "$MODE_FILE")"
    fi
    if [[ "$PROMPT" == *"Review order"* ]] || [[ "$PROMPT" == *"REVIEW_PASS"* ]]; then
      case "$MODE" in
        review_advance_clean)
          ORIGIN="$(git remote get-url origin)"
          PUSH_DIR="$(mktemp -d)"
          if git clone "$ORIGIN" "$PUSH_DIR" 2>/dev/null; then
            git -C "$PUSH_DIR" config user.email "t@example.com"
            git -C "$PUSH_DIR" config user.name "T"
            mkdir -p "$PUSH_DIR/src"
            printf 'OTHER = 1\\n' >"$PUSH_DIR/src/other.py"
            git -C "$PUSH_DIR" add src/other.py
            git -C "$PUSH_DIR" commit -m "advance main cleanly" || true
            git -C "$PUSH_DIR" push origin main || true
            rm -rf "$PUSH_DIR"
          fi
          ;;
        review_advance_conflict)
          ORIGIN="$(git remote get-url origin)"
          PUSH_DIR="$(mktemp -d)"
          if git clone "$ORIGIN" "$PUSH_DIR" 2>/dev/null; then
            git -C "$PUSH_DIR" config user.email "t@example.com"
            git -C "$PUSH_DIR" config user.name "T"
            printf 'VALUE = 3\\n' >"$PUSH_DIR/src/feature.py"
            git -C "$PUSH_DIR" add src/feature.py
            git -C "$PUSH_DIR" commit -m "advance main with conflict" || true
            git -C "$PUSH_DIR" push origin main || true
            rm -rf "$PUSH_DIR"
          fi
          ;;
      esac
      printf '%s\\n' 'PIPELINE_STATUS: REVIEW_PASS'
      exit 0
    fi
    TARGET="src/feature.py"
    case "$MODE" in
      bad_lint)
        printf 'import os\\n\\nVALUE = 2\\n' >>"$TARGET"
        git add src/feature.py
        git commit -m "fake engine change" || true
        ;;
      good|review_advance_clean|review_advance_conflict)
        printf 'VALUE = 2\\n' >"$TARGET"
        printf '%s\\n' \
          'from src import feature' \
          '' \
          'def test_value() -> None:' \
          '    assert feature.VALUE == 2' \
          >tests/test_feature.py
        git add src/feature.py tests/test_feature.py
        git commit -m "fake engine change" || true
        ;;
      autofix_lint)
        printf 'VALUE = 2   \\n' >"$TARGET"
        printf '%s\\n' \\
          'from src import feature' \\
          '' \\
          'def test_value() -> None:' \\
          '    assert feature.VALUE == 2' \\
          >tests/test_feature.py
        git add src/feature.py tests/test_feature.py
        git commit -m "fake engine change" || true
        ;;
      autofix_tracked)
        printf 'VALUE = 2   \\n' >"$TARGET"
        printf '%s\\n' \\
          'from src import feature' \\
          '' \\
          'def test_value() -> None:' \\
          '    assert feature.VALUE == 2' \\
          >tests/test_feature.py
        git add src/feature.py tests/test_feature.py
        git commit -m "fake engine change" || true
        printf 'VALUE = 2\\n' >"$TARGET"
        ;;
      out_of_scope)
        printf 'VALUE = 2\\n' >"$TARGET"
        printf '%s\\n' \
          'from src import feature' \
          '' \
          'def test_value() -> None:' \
          '    assert feature.VALUE == 2' \
          >tests/test_feature.py
        mkdir -p docs
        printf 'note\\n' >docs/note.md
        git add src/feature.py tests/test_feature.py docs/note.md
        git commit -m "fake engine change" || true
        ;;
      base_conflict)
        git rebase --abort 2>/dev/null || true
        git merge --abort 2>/dev/null || true
        ORIGIN="$(git remote get-url origin)"
        PUSH_DIR="$(mktemp -d)"
        if git clone "$ORIGIN" "$PUSH_DIR" 2>/dev/null; then
          git -C "$PUSH_DIR" config user.email "t@example.com"
          git -C "$PUSH_DIR" config user.name "T"
          printf 'VALUE = 3\\n' >"$PUSH_DIR/src/feature.py"
          git -C "$PUSH_DIR" add src/feature.py
          git -C "$PUSH_DIR" commit -m "advance main" || true
          git -C "$PUSH_DIR" push origin main || true
          rm -rf "$PUSH_DIR"
        fi
        printf 'VALUE = 2\\n' >"$TARGET"
        printf '%s\\n' \
          'from src import feature' \
          '' \
          'def test_value() -> None:' \
          '    assert feature.VALUE == 2' \
          >tests/test_feature.py
        git add src/feature.py tests/test_feature.py
        git commit -m "fake engine change" || true
        ;;
      *)
        echo "unknown mode: $MODE" >&2
        exit 1
        ;;
    esac
    exit 0
    """
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _make_host_repo(tmp_path: Path, slug: str) -> Path:
    seed = tmp_path / "seed"
    origin = tmp_path / "origin.git"
    host = tmp_path / "host"
    subprocess.run(
        ["git", "init", "-b", "main", str(seed)],
        capture_output=True,
        check=True,
    )
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "config", "user.name", "T")
    (seed / "src").mkdir()
    (seed / "src" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    (seed / "tests").mkdir()
    (seed / "tests" / "test_feature.py").write_text(
        "from src import feature\n\n\ndef test_value():\n    assert feature.VALUE == 1\n",
        encoding="utf-8",
    )
    (seed / "pyproject.toml").write_text(
        "[project]\nname = 'ref-host'\nversion = '0.0.0'\n\n"
        "[tool.pytest.ini_options]\ntestpaths = ['tests']\n\n"
        "[tool.ruff]\nline-length = 100\n\n"
        '[tool.ruff.lint]\nextend-select = ["W291"]\n',
        encoding="utf-8",
    )
    (seed / ".gitignore").write_text("__pycache__/\n*.py[cod]\n", encoding="utf-8")
    (seed / "jobs").mkdir()
    (seed / "logs").mkdir()
    extract_line("develop", seed)
    cfg_path = seed / "issuesmith.yaml"
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    data["repo"] = slug
    data["supported_repos"] = [slug]
    for role in ("implementation", "review"):
        eng = data.get("engines", {}).get(role)
        if isinstance(eng, dict):
            eng.pop("allowed", None)
            eng["heavy"] = [["cursor", "auto"]]
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "init")
    subprocess.run(
        ["git", "clone", "--bare", str(seed), str(origin)],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "clone", str(origin), str(host)],
        capture_output=True,
        check=True,
    )
    _git(host, "config", "user.email", "t@example.com")
    _git(host, "config", "user.name", "T")
    (host / "bin").mkdir()
    fake_cli = host / "bin" / "cursor-agent"
    fake_cli.write_text(_FAKE_AGENT, encoding="utf-8")
    fake_cli.chmod(0o755)
    (host / "jobs").mkdir(exist_ok=True)
    (host / "logs").mkdir(exist_ok=True)
    (host / ".pipeline-state").mkdir(exist_ok=True)
    return host


def _init_feature_seed(repo: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)],
        capture_output=True,
        check=True,
    )
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "src").mkdir()
    (repo / "src" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_feature.py").write_text(
        "from src import feature\n\n\ndef test_value():\n    assert feature.VALUE == 1\n",
        encoding="utf-8",
    )
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'ref-host'\nversion = '0.0.0'\n\n"
        "[tool.pytest.ini_options]\ntestpaths = ['tests']\n\n"
        "[tool.ruff]\nline-length = 100\n\n"
        '[tool.ruff.lint]\nextend-select = ["W291"]\n',
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text("__pycache__/\n*.py[cod]\n", encoding="utf-8")
    (repo / "jobs").mkdir()
    (repo / "logs").mkdir()


def _make_target_forge_root(tmp_path: Path) -> tuple[Path, Path]:
    """Target checkout at forge_root plus a bare origin for clone/push."""
    seed = tmp_path / "target_seed"
    origin = tmp_path / "target.git"
    forge_root = tmp_path / "target_forge"
    _init_feature_seed(seed)
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "init")
    subprocess.run(
        ["git", "clone", "--bare", str(seed), str(origin)],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "clone", str(origin), str(forge_root)],
        capture_output=True,
        check=True,
    )
    _git(forge_root, "config", "user.email", "t@example.com")
    _git(forge_root, "config", "user.name", "T")
    return forge_root, origin


def _make_detached_develop_host(
    tmp_path: Path,
    *,
    host_slug: str,
    supported_repos: list[str],
) -> Path:
    host = tmp_path / "host_detached"
    subprocess.run(
        ["git", "init", "-b", "main", str(host)],
        capture_output=True,
        check=True,
    )
    _git(host, "config", "user.email", "t@example.com")
    _git(host, "config", "user.name", "T")
    (host / "host-readme.md").write_text("detached host\n", encoding="utf-8")
    _git(host, "add", "host-readme.md")
    _git(host, "commit", "-m", "host init")
    extract_line("develop", host)
    cfg_path = host / "issuesmith.yaml"
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    data["repo"] = host_slug
    data["supported_repos"] = list(supported_repos)
    data["host_is_repo_checkout"] = False
    for role in ("implementation", "review"):
        eng = data.get("engines", {}).get(role)
        if isinstance(eng, dict):
            eng.pop("allowed", None)
            eng["heavy"] = [["cursor", "auto"]]
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    _git(host, "add", "issuesmith.yaml")
    _git(host, "commit", "-m", "wiring")
    (host / "bin").mkdir(exist_ok=True)
    fake_cli = host / "bin" / "cursor-agent"
    fake_cli.write_text(_FAKE_AGENT, encoding="utf-8")
    fake_cli.chmod(0o755)
    (host / "jobs").mkdir(exist_ok=True)
    (host / "logs").mkdir(exist_ok=True)
    (host / ".pipeline-state").mkdir(exist_ok=True)
    return host


def _clone_target(external_dir: Path, target_slug: str, origin: Path) -> Path:
    repo_name = target_slug.split("/")[-1]
    clone_dir = external_dir / repo_name
    external_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", str(origin), str(clone_dir)],
        capture_output=True,
        check=True,
    )
    _git(clone_dir, "config", "user.email", "t@example.com")
    _git(clone_dir, "config", "user.name", "T")
    return clone_dir


def _cross_repo_issue_body(target_slug: str) -> str:
    return textwrap.dedent(
        f"""\
        ```yaml
        target_repo: {target_slug}
        base_branch: main
        allow_paths:
          - src/**
          - tests/**
        ```

        ## Task
        Bump VALUE in src/feature.py.
        """
    )


def _issue_body(*, acceptance_yaml: str | None = None) -> str:
    base = textwrap.dedent(
        """\
        ```yaml
        target_repo: example/reference-host
        base_branch: main
        allow_paths:
          - src/**
          - tests/**
        ```

        ## Task
        Bump VALUE in src/feature.py.
        """
    )
    if acceptance_yaml is None:
        return base
    ac = (
        "## Acceptance Criteria\n\n"
        "```yaml\n"
        f"{acceptance_yaml.strip()}\n"
        "```\n"
    )
    return base + "\n" + ac


def _broken_acceptance_issue_body() -> str:
    return textwrap.dedent(
        """\
        ```yaml
        target_repo: example/reference-host
        base_branch: main
        allow_paths:
          - src/**
          - tests/**
        ```

        ## Acceptance Criteria

        ```yaml
        paths_must_exist: [
        ```
        """
    )


def _readme_gate_rows(readme_text: str) -> dict[str, str]:
    """Map step id to the gate-id cell from the reference-line gates table."""
    rows: dict[str, str] = {}
    in_table = False
    for line in readme_text.splitlines():
        if line.strip() == "### Reference line gates by step":
            in_table = True
            continue
        if in_table and line.startswith("### "):
            break
        if not in_table or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[0] in ("Step", "---", "------"):
            continue
        step = cells[0].strip("`")
        rows[step] = cells[1]
    return rows


def _config_step_requires(config_path: Path) -> dict[str, list[str]]:
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    for step_id, step in (data.get("steps") or {}).items():
        requires = step.get("requires") or []
        if requires:
            out[step_id] = list(requires)
    return out


_RUNNER_TIMEOUT_SEC = 60.0
_RUNNER_STOP_GRACE_SEC = 10.0


@pytest.fixture
def runner_procs(monkeypatch: pytest.MonkeyPatch):
    """Record ghdag runners and assert they exit after each test (refs #7257)."""
    recorded: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def recording_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        argv = list(args[0]) if args else list(kwargs.get("args") or [])
        if "ghdag" in argv and "run" in argv:
            recorded.append(proc)
        return proc

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    yield recorded
    for proc in recorded:
        assert proc.poll() is not None, "ghdag run process still running after test"


@pytest.fixture
def host_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner_procs):
    slug = "example/reference-host"
    host = _make_host_repo(tmp_path, slug)
    forge_root = tmp_path / "forge"
    forge_root.mkdir()
    (tmp_path / "queue").mkdir()
    bin_dir = host / "bin"
    env = {
        **os.environ,
        "ISSUESMITH_CONFIG": str(host / "issuesmith.yaml"),
        "PYTHONPATH": f"{_REPO_ROOT / 'src'}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        "GHDAG_FORGE": "local",
        "GHDAG_FORGE_ROOT": str(forge_root),
        "ISSUESMITH_QUEUE_DIR": str(tmp_path / "queue"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "GHDAG_TASK_ENV_ALLOW": "*",
    }
    for key in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_REPOSITORIES", "GITHUB_REPOSITORY"):
        env.pop(key, None)
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(host)
    reset_config_cache()
    client = get_forge()
    yield host, client, env, slug
    reset_config_cache()


def _cross_repo_env(
    tmp_path: Path,
    *,
    host_slug: str,
    supported_repos: list[str],
    clone_slugs: list[str],
    origin: Path,
    forge_root: Path,
) -> tuple[Path, Path, dict[str, str], str]:
    external_dir = (tmp_path / "external").resolve()
    host = _make_detached_develop_host(
        tmp_path,
        host_slug=host_slug,
        supported_repos=supported_repos,
    )
    cfg_path = host / "issuesmith.yaml"
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    data.setdefault("paths", {})["external_dir"] = str(external_dir)
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    for slug in clone_slugs:
        _clone_target(external_dir, slug, origin)
    (tmp_path / "queue").mkdir()
    bin_dir = host / "bin"
    env = {
        **os.environ,
        "ISSUESMITH_CONFIG": str(host / "issuesmith.yaml"),
        "PYTHONPATH": f"{_REPO_ROOT / 'src'}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        "GHDAG_FORGE": "local",
        "GHDAG_FORGE_ROOT": str(forge_root),
        "ISSUESMITH_QUEUE_DIR": str(tmp_path / "queue"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "GHDAG_TASK_ENV_ALLOW": "*",
    }
    for key in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_REPOSITORIES", "GITHUB_REPOSITORY"):
        env.pop(key, None)
    return host, external_dir, env, host_slug


@pytest.fixture
def cross_repo_same_target_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner_procs):
    """Host is detached; Issues and target work share one supported repo slug."""
    target_slug = "example/reference-host"
    forge_root, origin = _make_target_forge_root(tmp_path)
    host, external_dir, env, host_slug = _cross_repo_env(
        tmp_path,
        host_slug=target_slug,
        supported_repos=[target_slug],
        clone_slugs=[target_slug],
        origin=origin,
        forge_root=forge_root,
    )
    clone_dir = external_dir / target_slug.split("/")[-1]
    env["GHDAG_FORGE_ROOT"] = str(clone_dir)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    for key in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_REPOSITORIES", "GITHUB_REPOSITORY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(host)
    reset_config_cache()
    client = get_forge()
    yield host, client, env, host_slug, target_slug, clone_dir, external_dir
    reset_config_cache()


@pytest.fixture
def cross_repo_other_target_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner_procs):
    """Issues on one slug; implementation targets another supported repo."""
    host_slug = "example/issuesmith-host"
    target_slug = "example/reference-host"
    forge_root, origin = _make_target_forge_root(tmp_path)
    host, external_dir, env, _ = _cross_repo_env(
        tmp_path,
        host_slug=host_slug,
        supported_repos=[host_slug, target_slug],
        clone_slugs=[target_slug],
        origin=origin,
        forge_root=forge_root,
    )
    clone_dir = external_dir / target_slug.split("/")[-1]
    env["GHDAG_FORGE_ROOT"] = str(clone_dir)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    for key in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_REPOSITORIES", "GITHUB_REPOSITORY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(host)
    reset_config_cache()
    client = get_forge()
    yield host, client, env, host_slug, target_slug, clone_dir, external_dir
    reset_config_cache()


@pytest.fixture
def no_pr_target_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner_procs):
    """Detached host with target_remotes pointing at a bare repo (no pre-clone)."""
    target_slug = "example/reference-host"
    _forge_root, origin = _make_target_forge_root(tmp_path)
    bare_path = str(origin.resolve())
    external_dir = (tmp_path / "external").resolve()
    host = _make_detached_develop_host(
        tmp_path,
        host_slug=target_slug,
        supported_repos=[target_slug],
    )
    cfg_path = host / "issuesmith.yaml"
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    data.setdefault("paths", {})["external_dir"] = str(external_dir)
    data["target_remotes"] = {target_slug: bare_path}
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    (tmp_path / "queue").mkdir()
    bin_dir = host / "bin"
    env = {
        **os.environ,
        "ISSUESMITH_CONFIG": str(host / "issuesmith.yaml"),
        "PYTHONPATH": f"{_REPO_ROOT / 'src'}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        "GHDAG_FORGE": "local",
        "GHDAG_FORGE_ROOT": str(origin),
        "ISSUESMITH_QUEUE_DIR": str(tmp_path / "queue"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "GHDAG_TASK_ENV_ALLOW": "*",
    }
    for key in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_REPOSITORIES", "GITHUB_REPOSITORY"):
        env.pop(key, None)
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(host)
    reset_config_cache()
    client = get_forge()
    yield host, client, env, target_slug, origin, external_dir, bare_path
    reset_config_cache()


def _issue_comments(client, number: int) -> list[str]:
    comments = client.get_issue_comments(number)
    return [str(c.get("body") or "") for c in comments if isinstance(c, dict)]


def _set_fake_engine_mode(host: Path, mode: str) -> None:
    (host / "bin" / "fake-engine-mode").write_text(mode, encoding="utf-8")


def _labels(client, number: int) -> set[str]:
    raw = client.issue_get(number, fields=["labels", "state"])["labels"]
    return {lb["name"] for lb in raw if isinstance(lb, dict)}


def _watch_once(env: dict[str, str], host: Path) -> int:
    proc = subprocess.run(
        [
            "python3",
            "-m",
            "ghdag",
            "watch",
            "workflows",
            "--once",
            "--exec-jsonl",
            str(host / "jobs" / "exec.jsonl"),
        ],
        cwd=host,
        env=env,
        capture_output=True,
        text=True,
    )
    return proc.returncode


_STOP_HOOKS = textwrap.dedent(
    """\
    import os
    import signal
    from pathlib import Path

    from ghdag.dag.hooks import DefaultHooks

    _UUIDS = set(os.environ["DEVELOP_LINE_TEST_UUIDS"].split(","))
    _DONE_DIR = Path(os.environ["DEVELOP_LINE_TEST_DONE_DIR"])


    class StopWhenDoneHooks(DefaultHooks):
        def __init__(self):
            self._terminal = set()

        def _mark(self, uuid):
            self._terminal.add(uuid)
            if all(u in self._terminal or (_DONE_DIR / u).exists() for u in _UUIDS):
                os.kill(os.getpid(), signal.SIGINT)

        def on_task_success(self, uuid, task, metrics):
            self._mark(uuid)

        def on_task_failure(self, uuid, task, returncode, stderr_text, metrics):
            self._mark(uuid)

        def on_task_rejected(self, uuid, task, retry_depth, is_final, metrics):
            if is_final:
                self._mark(uuid)

        def on_task_dep_failed(self, uuid, task, failed_dep):
            self._mark(uuid)

        def on_task_empty_result(self, uuid, task, stderr_text, metrics):
            self._mark(uuid)

        def on_task_cancelled(self, uuid, task):
            self._mark(uuid)


    HOOKS_CLASS = StopWhenDoneHooks
    """
)


def _stop_runner(proc: subprocess.Popen[bytes]) -> None:
    """Stop a ghdag runner started by this module; no-op if it already exited.

    SIGTERM lets the runner terminate its task process groups before exiting;
    SIGKILL is the fallback after _RUNNER_STOP_GRACE_SEC.
    """
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=_RUNNER_STOP_GRACE_SEC)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _run_dag_once(
    env: dict[str, str],
    host: Path,
    *,
    timeout_sec: float = _RUNNER_TIMEOUT_SEC,
) -> None:
    """Run ``ghdag run`` until every queued task is terminal.

    Fails the test (pytest.fail) if the runner is still running after
    timeout_sec; the runner is stopped on every exit path.
    """
    exec_path = host / "jobs" / "exec.jsonl"
    if not exec_path.exists():
        return
    uuids: list[str] = []
    for line in exec_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        uuids.append(json.loads(line)["uuid"])
    done_dir = host / "jobs" / "done"
    if all((done_dir / uuid).exists() for uuid in uuids):
        return
    hooks_dir = host / "bin" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "develop_line_stop_hooks.py").write_text(_STOP_HOOKS, encoding="utf-8")
    run_env = {
        **env,
        "PYTHONPATH": f"{hooks_dir}{os.pathsep}{env.get('PYTHONPATH', '')}",
        "DEVELOP_LINE_TEST_UUIDS": ",".join(uuids),
        "DEVELOP_LINE_TEST_DONE_DIR": str(done_dir),
    }
    proc = subprocess.Popen(
        [
            "python3",
            "-m",
            "ghdag",
            "run",
            str(exec_path),
            "--interval-sec",
            "0.2",
            "--hooks",
            "develop_line_stop_hooks",
        ],
        cwd=host,
        env=run_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        try:
            proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            unfinished = [u for u in uuids if not (done_dir / u).exists()]
            _stop_runner(proc)
            pytest.fail(
                f"ghdag run on {exec_path} did not finish within {timeout_sec}s; unfinished tasks: {unfinished!r}"
            )
    finally:
        _stop_runner(proc)


def _run_until(
    client,
    number: int,
    env: dict[str, str],
    host: Path,
    *,
    expect_done: bool = True,
    max_rounds: int = 40,
) -> str:
    for _ in range(max_rounds):
        labels = _labels(client, number)
        state = client.issue_get(number, fields=["state"])["state"]
        if "issuesmith:develop-done" in labels:
            return "done"
        if list_open(client):
            return "andon"
        if any(lb.endswith(":andon-decision") or lb == "issuesmith:andon-decision" for lb in labels):
            return "andon"
        if expect_done and state == "CLOSED" and "issuesmith:develop-done" in labels:
            return "done"
        _watch_once(env, host)
        _run_dag_once(env, host)
    return "timeout"


def test_run_dag_once_stops_runner_that_never_finishes(tmp_path: Path, runner_procs) -> None:
    """refs #7257 AC1: timeout stops runner and task when the queue never finishes."""
    host = tmp_path
    jobs = host / "jobs"
    done = jobs / "done"
    done.mkdir(parents=True)
    pid_file = jobs / "never-ends.pid"
    task_script = jobs / "never_ends_sleep.py"
    task_script.write_text(
        textwrap.dedent(
            f"""\
            import os
            import signal
            from pathlib import Path

            Path({str(pid_file)!r}).write_text(str(os.getpid()))
            signal.pause()
            """
        ),
        encoding="utf-8",
    )
    order = jobs / "20261118120000-shell-order-never-ends.md"
    order.write_text(f"exec python3 {task_script}\n", encoding="utf-8")
    exec_path = jobs / "exec.jsonl"
    exec_path.write_text(
        json.dumps(
            {
                "uuid": "never-ends",
                "command": f"bash -o pipefail {order}",
                "idempotency_key": "develop-line-test:never-ends",
                "result_path": "jobs/never-ends-result.md",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PYTHONPATH": f"{_REPO_ROOT / 'src'}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
    }
    with pytest.raises(pytest.fail.Exception) as excinfo:
        _run_dag_once(env, host, timeout_sec=2.0)
    msg = str(excinfo.value)
    assert str(exec_path) in msg
    assert "never-ends" in msg
    assert runner_procs
    for proc in runner_procs:
        assert proc.poll() is not None
    pid = int(pid_file.read_text(encoding="utf-8").strip())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_issue_flows_when_host_is_not_target_checkout(cross_repo_same_target_env) -> None:
    host, client, env, host_slug, target_slug, target_checkout, external_dir = (
        cross_repo_same_target_env
    )
    number = client.issue_create(
        "cross same target",
        _cross_repo_issue_body(host_slug),
    )
    client.issue_update(number, labels_add=["issuesmith:develop-ready"])
    outcome = _run_until(client, number, env, host, expect_done=True)
    assert outcome == "done"
    labels = _labels(client, number)
    assert "issuesmith:develop-done" in labels
    state = client.issue_get(number, fields=["state"])["state"]
    assert state == "CLOSED"
    show = _git(target_checkout, "show", "main:src/feature.py")
    assert show.returncode == 0, show.stderr
    assert "VALUE = 2" in show.stdout
    host_wt = _git(host, "worktree", "list", "--porcelain")
    assert host_wt.returncode == 0, host_wt.stderr
    host_wt_paths = [
        line[9:] for line in host_wt.stdout.splitlines() if line.startswith("worktree ")
    ]
    assert len(host_wt_paths) == 1
    assert host_wt_paths[0] == str(host.resolve())
    host_branches = _git(host, "branch", "--list", "feat/*")
    assert not host_branches.stdout.strip()
    clone_dir = external_dir / target_slug.split("/")[-1]
    clone_wt = _git(clone_dir, "worktree", "list")
    assert f"issue-{number}-" not in clone_wt.stdout
    clone_branches = _git(clone_dir, "branch", "--list", f"feat/issue-{number}-*")
    assert not clone_branches.stdout.strip()


def test_issue_flows_to_other_target_repo_when_host_is_not_target_checkout(
    cross_repo_other_target_env,
) -> None:
    host, client, env, host_slug, target_slug, target_checkout, _external_dir = (
        cross_repo_other_target_env
    )
    number = client.issue_create(
        "cross other target",
        _cross_repo_issue_body(target_slug),
    )
    client.issue_update(number, labels_add=["issuesmith:develop-ready"])
    outcome = _run_until(client, number, env, host, expect_done=True)
    assert outcome == "done"
    show = _git(target_checkout, "show", "main:src/feature.py")
    assert show.returncode == 0, show.stderr
    assert "VALUE = 2" in show.stdout
    state = client.issue_get(number, fields=["state"])["state"]
    assert state == "CLOSED"
    pulls = client.pr_list(state="all", repo=target_slug)
    head_prefix = f"feat/issue-{number}-"
    merged = [
        p
        for p in pulls
        if str(p.get("headRefName") or "").startswith(head_prefix)
        and str(p.get("state") or "").upper() == "CLOSED"
    ]
    assert merged, f"expected merged PR on {target_slug}, got {pulls!r}"
    pr = client.pr_get(int(merged[0]["number"]), repo=target_slug)
    body = str(pr.get("body") or "")
    assert f"Closes {host_slug}#{number}" in body


def test_issue_flows_from_ready_to_done_on_local_forge(host_env) -> None:
    host, client, env, _slug = host_env
    number = client.issue_create("develop line", _issue_body())
    client.issue_update(number, labels_add=["issuesmith:develop-ready"])
    outcome = _run_until(client, number, env, host, expect_done=True)
    assert outcome == "done"
    labels = _labels(client, number)
    assert "issuesmith:develop-done" in labels
    state = client.issue_get(number, fields=["state"])["state"]
    assert state == "CLOSED"
    forge_used = Path(getattr(client, "_root", env["GHDAG_FORGE_ROOT"]))
    pulls = client.pr_list(state="all")
    issue_ref = re.compile(rf"(?<!\d)#{number}(?!\d)")
    pr_candidates = [
        p
        for p in pulls
        if issue_ref.search(str(p.get("title") or ""))
        and str(p.get("headRefName") or "").startswith(f"feat/issue-{number}-")
    ]
    assert pr_candidates, f"expected PR for issue #{number}, got {pulls!r}"
    pr = pr_candidates[0]
    pr_num = int(pr["number"])
    head = str(pr.get("headRefName") or "")
    assert head.startswith(f"feat/issue-{number}-"), head
    assert str(pr.get("state") or "").upper() == "CLOSED", pr
    pull_path = forge_used / ".forge" / "pulls" / f"{pr_num}.json"
    assert pull_path.is_file(), f"missing local-forge pull record: {pull_path}"
    pull = json.loads(pull_path.read_text(encoding="utf-8"))
    assert pull.get("merged") is True, pull


def test_failing_verify_raises_andon_and_resume_continues_from_verify(
    host_env,
) -> None:
    host, client, env, _slug = host_env
    _set_fake_engine_mode(host, "bad_lint")
    number = client.issue_create("repair line", _issue_body())
    client.issue_update(number, labels_add=["issuesmith:develop-ready"])
    outcome = _run_until(client, number, env, host, expect_done=False, max_rounds=60)
    assert outcome == "andon"
    open_andons = list_open(client)
    assert open_andons
    assert open_andons[0].kind == "decision"
    andon_id = open_andons[0].id
    _set_fake_engine_mode(host, "good")
    proc = subprocess.run(
        ["python3", "-m", "issuesmith", "andon", "answer", andon_id, "resume"],
        cwd=host,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    outcome = _run_until(client, number, env, host, expect_done=True, max_rounds=60)
    assert outcome == "done"


def test_no_pr_target_clones_from_configured_remote(no_pr_target_env) -> None:
    """refs sumipan/nexus#7391 AC1 — prepare clones from target_remotes bare path."""
    host, client, env, target_slug, origin, external_dir, bare_path = no_pr_target_env
    clone_dir = external_dir / target_slug.split("/")[-1]
    assert not clone_dir.exists()
    number = client.issue_create("no pr clone", _cross_repo_issue_body(target_slug))
    client.issue_update(number, labels_add=["issuesmith:develop-ready"])
    for _ in range(40):
        if clone_dir.is_dir():
            break
        _watch_once(env, host)
        _run_dag_once(env, host)
    assert clone_dir.is_dir(), "prepare did not create external clone"
    remote = _git(clone_dir, "remote", "get-url", "origin")
    assert remote.returncode == 0, remote.stderr
    assert Path(remote.stdout.strip()).resolve() == Path(bare_path).resolve()


def test_no_pr_target_merges_by_fast_forward(no_pr_target_env) -> None:
    """refs sumipan/nexus#7391 AC2 — merge fast-forwards base without opening a PR."""
    host, client, env, target_slug, origin, _external_dir, _bare_path = no_pr_target_env
    main_before = _git(origin, "rev-parse", "main").stdout.strip()
    number = client.issue_create("no pr merge", _cross_repo_issue_body(target_slug))
    client.issue_update(number, labels_add=["issuesmith:develop-ready"])
    outcome = _run_until(client, number, env, host, expect_done=True)
    assert outcome == "done"
    labels = _labels(client, number)
    assert "issuesmith:develop-done" in labels
    state = client.issue_get(number, fields=["state"])["state"]
    assert state == "CLOSED"
    main_after = _git(origin, "rev-parse", "main").stdout.strip()
    assert _git(
        origin, "merge-base", "--is-ancestor", main_before, main_after
    ).returncode == 0
    show = _git(origin, "show", "main:src/feature.py")
    assert show.returncode == 0, show.stderr
    assert "VALUE = 2" in show.stdout
    comments = _issue_comments(client, number)
    assert any("without a pull request" in body for body in comments)
    assert any(main_after in body for body in comments)
    pulls = client.pr_list(state="all", repo=target_slug)
    assert not pulls


def test_no_pr_target_rebases_when_base_advanced(no_pr_target_env) -> None:
    """refs sumipan/nexus#7391 AC3 — merge rebases cleanly when base moves during review."""
    host, client, env, target_slug, origin, _external_dir, _bare_path = no_pr_target_env
    _set_fake_engine_mode(host, "review_advance_clean")
    main_before = _git(origin, "rev-parse", "main").stdout.strip()
    number = client.issue_create("no pr rebase", _cross_repo_issue_body(target_slug))
    client.issue_update(number, labels_add=["issuesmith:develop-ready"])
    outcome = _run_until(client, number, env, host, expect_done=True)
    assert outcome == "done"
    merges = _git(origin, "rev-list", "--merges", f"{main_before}..main")
    assert merges.returncode == 0, merges.stderr
    assert not merges.stdout.strip()
    feature = _git(origin, "show", "main:src/feature.py")
    assert "VALUE = 2" in feature.stdout
    other = _git(origin, "show", "main:src/other.py")
    assert other.returncode == 0, other.stderr
    assert "OTHER = 1" in other.stdout


def test_no_pr_target_rebase_conflict_stops_with_andon(no_pr_target_env) -> None:
    """refs sumipan/nexus#7391 AC4 — rebase conflict on merge opens blocked andon."""
    host, client, env, target_slug, origin, _external_dir, _bare_path = no_pr_target_env
    _set_fake_engine_mode(host, "review_advance_conflict")
    number = client.issue_create("no pr conflict", _cross_repo_issue_body(target_slug))
    client.issue_update(number, labels_add=["issuesmith:develop-ready"])
    outcome = _run_until(client, number, env, host, expect_done=False, max_rounds=60)
    assert outcome == "andon"
    open_andons = list_open(client)
    assert open_andons
    andon = next(a for a in open_andons if a.rule_id == "merge.rebase_conflict")
    assert andon.kind == "blocked"
    show = _git(origin, "show", "main:src/feature.py")
    assert show.returncode == 0, show.stderr
    assert "VALUE = 3" in show.stdout
    state = client.issue_get(number, fields=["state"])["state"]
    assert state != "CLOSED"


def test_no_pr_merge_commits_gate_autofix_on_bare_main(no_pr_target_env) -> None:
    """refs sumipan/nexus#7391 — merge commits verify lint autofixes before no-PR push."""
    host, client, env, target_slug, origin, _external_dir, _bare_path = no_pr_target_env
    # Leaves a tracked, uncommitted fix in the worktree whether or not ruff
    # is on PATH, so the result does not depend on the lint gate's autofix.
    _set_fake_engine_mode(host, "autofix_tracked")
    number = client.issue_create("no pr autofix", _cross_repo_issue_body(target_slug))
    client.issue_update(number, labels_add=["issuesmith:develop-ready"])
    outcome = _run_until(client, number, env, host, expect_done=True, max_rounds=60)
    assert outcome == "done"
    feature = _git(origin, "show", "main:src/feature.py")
    assert feature.returncode == 0, feature.stderr
    assert feature.stdout == "VALUE = 2\n"
    subjects = _git(origin, "log", "--format=%s", "main").stdout.splitlines()
    assert "Apply gate auto-fixes" in subjects


def test_milestone_parent_stops_at_prepare(host_env) -> None:
    """refs #7357: scope:milestone parents stop at prepare before a worktree exists."""
    host, client, env, _slug = host_env
    number = client.issue_create("milestone parent", _issue_body())
    client.issue_update(
        number,
        labels_add=["issuesmith:develop-ready", "scope:milestone"],
    )
    outcome = _run_until(client, number, env, host, expect_done=False, max_rounds=40)
    assert outcome == "andon"
    open_andons = list_open(client)
    assert open_andons
    andon = open_andons[0]
    assert andon.kind == "decision"
    assert andon.step == "prepare"
    assert andon.rule_id == "prepare.milestone_parent"
    assert andon.summary == EN.messages["prepare.milestone_parent"]
    labels = _labels(client, number)
    assert "issuesmith:develop-done" not in labels
    host_wt = _git(host, "worktree", "list", "--porcelain")
    assert host_wt.returncode == 0, host_wt.stderr
    host_wt_paths = [
        line[9:] for line in host_wt.stdout.splitlines() if line.startswith("worktree ")
    ]
    assert len(host_wt_paths) == 1
    assert not _git(host, "branch", "--list", f"feat/issue-{number}-*").stdout.strip()


def test_milestone_parent_stops_at_prepare_when_host_is_detached(
    cross_repo_same_target_env,
) -> None:
    """refs #7357: milestone blocking uses Issue labels only on a detached host."""
    host, client, env, host_slug, target_slug, target_checkout, external_dir = (
        cross_repo_same_target_env
    )
    number = client.issue_create(
        "milestone parent detached",
        _cross_repo_issue_body(host_slug),
    )
    client.issue_update(
        number,
        labels_add=["issuesmith:develop-ready", "scope:milestone"],
    )
    outcome = _run_until(client, number, env, host, expect_done=False, max_rounds=40)
    assert outcome == "andon"
    open_andons = list_open(client)
    assert open_andons
    andon = open_andons[0]
    assert andon.step == "prepare"
    assert andon.rule_id == "prepare.milestone_parent"
    clone_dir = external_dir / target_slug.split("/")[-1]
    clone_wt = _git(clone_dir, "worktree", "list")
    assert f"issue-{number}-" not in clone_wt.stdout
    assert not _git(target_checkout, "branch", "--list", f"feat/issue-{number}-*").stdout.strip()


def test_prepare_label_fetch_failure_returns_broken_andon(monkeypatch) -> None:
    """refs #7357: forge failures stop prepare as a broken andon."""
    def fail(_issue_number: str) -> list[str]:
        raise RuntimeError("forge unavailable")

    monkeypatch.setattr(develop_prepare, "_issue_labels", fail)
    ctx = StepContext(
        issue_number="7357",
        base_branch="main",
        handler_name="develop",
        is_cross_repo="false",
        target_clone_path="",
        source="",
        workflow_name="issuesmith",
        m1_result_filename="",
        m1r_result_filename="",
    )
    result = develop_prepare.run(ctx, StepConfig())
    assert result.status == "andon"
    assert result.andon is not None
    assert result.andon.kind == "broken"
    assert result.andon.summary == "failed to fetch issue labels: forge unavailable"


def test_broken_acceptance_yaml_stops_at_prepare(host_env) -> None:
    """refs #7258 AC2: invalid Acceptance Criteria YAML blocks prepare (non-repairable)."""
    host, client, env, _slug = host_env
    number = client.issue_create("broken ac yaml", _broken_acceptance_issue_body())
    client.issue_update(number, labels_add=["issuesmith:develop-ready"])
    outcome = _run_until(client, number, env, host, expect_done=False, max_rounds=40)
    assert outcome == "andon"
    open_andons = list_open(client)
    assert open_andons
    andon = open_andons[0]
    assert andon.kind == "decision"
    assert andon.step == "prepare"
    assert "failed to parse the YAML block" in andon.summary
    labels = _labels(client, number)
    assert "issuesmith:develop-done" not in labels


def test_out_of_allow_paths_change_stops_at_verify(host_env) -> None:
    """refs #7258 AC3a: changes outside allow_paths stop at verify with a widen option."""
    host, client, env, _slug = host_env
    _set_fake_engine_mode(host, "out_of_scope")
    number = client.issue_create("out of scope", _issue_body())
    client.issue_update(number, labels_add=["issuesmith:develop-ready"])
    outcome = _run_until(client, number, env, host, expect_done=False, max_rounds=80)
    assert outcome == "andon"
    open_andons = list_open(client)
    assert open_andons
    andon = open_andons[0]
    assert andon.kind == "decision"
    assert andon.step == "verify"
    assert any(opt == "widen:docs/note.md" for opt in andon.options)
    labels = _labels(client, number)
    assert "issuesmith:develop-done" not in labels


def test_unmet_acceptance_contract_stops_at_verify(host_env) -> None:
    """refs #7258 AC3b: missing paths_must_exist paths stop at verify."""
    host, client, env, _slug = host_env
    body = _issue_body(acceptance_yaml="paths_must_exist:\n  - src/missing.py\npaths_must_not_exist: []")
    number = client.issue_create("unmet ac contract", body)
    client.issue_update(number, labels_add=["issuesmith:develop-ready"])
    outcome = _run_until(client, number, env, host, expect_done=False, max_rounds=80)
    assert outcome == "andon"
    open_andons = list_open(client)
    assert open_andons
    andon = next(a for a in open_andons if a.step == "verify")
    assert "ac_contract.paths_must_exist" in andon.summary
    labels = _labels(client, number)
    assert "issuesmith:develop-done" not in labels


def test_base_conflict_stops_at_verify(host_env) -> None:
    """refs #7258 AC3c: base_freshness blocks merge when origin main conflicts."""
    host, client, env, _slug = host_env
    _set_fake_engine_mode(host, "base_conflict")
    number = client.issue_create("base conflict", _issue_body())
    client.issue_update(number, labels_add=["issuesmith:develop-ready"])
    outcome = _run_until(client, number, env, host, expect_done=False, max_rounds=80)
    assert outcome == "andon"
    open_andons = list_open(client)
    assert open_andons
    assert any(a.step == "verify" for a in open_andons)
    show = _git(host, "show", "origin/main:src/feature.py")
    assert show.returncode == 0, show.stderr
    assert "VALUE = 3" in show.stdout
    labels = _labels(client, number)
    assert "issuesmith:develop-done" not in labels


def test_readme_mentions_host_not_target_checkout() -> None:
    """refs #7311 AC6: README documents host_is_repo_checkout false for the reference line."""
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    start = readme.find("### Reference develop line (packaged)")
    assert start >= 0
    section = readme[start : readme.find("\n### ", start + 1)]
    assert "host_is_repo_checkout" in section
    assert "external_dir" in section
    stop = readme.find("**When the line stops:**", start)
    assert stop >= 0
    stop_section = readme[stop : readme.find("\n\n", stop + 1)]
    assert "scope:milestone" in stop_section
    assert "ensure_base_included" in stop_section
    assert "base_freshness" in stop_section


def test_readme_lists_reference_line_gates_per_step() -> None:
    """refs #7258 AC4: README gate table matches packaged reference line requires."""
    config_path = _PACKAGE_FILES / "issuesmith.yaml"
    requires = _config_step_requires(config_path)
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    rows = _readme_gate_rows(readme)
    for step_id, gate_ids in requires.items():
        assert step_id in rows, f"missing README row for step {step_id!r}"
        row = rows[step_id]
        for gate_id in gate_ids:
            assert gate_id in row, f"README row for {step_id!r} missing gate {gate_id!r}"


def test_step_modules_import_only_public_issuesmith_modules() -> None:
    line_dir = _LINE_DEV_DIR
    for path in sorted(line_dir.glob("*.py")):
        if path.name in {"__init__.py", "hooks.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def _ok(name: str) -> bool:
            return any(name == allowed or name.startswith(f"{allowed}.") for allowed in _ALLOWED_ISSMITH_IMPORTS)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("issuesmith."):
                        assert _ok(alias.name), f"{path.name}: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod.startswith("issuesmith."):
                    assert _ok(mod), f"{path.name}: {mod}"
                if mod.startswith("ghdag"):
                    pytest.fail(f"{path.name} must not import ghdag ({mod})")


def test_gate_autofix_is_committed_and_merged(host_env, tmp_path: Path) -> None:
    """refs #7408 AC1: an auto-fixed lint violation reaches the merged base."""
    if shutil.which("ruff") is None:
        pytest.skip("ruff is not on PATH; lint auto-fix does not run")
    host, client, env, _slug = host_env
    _set_fake_engine_mode(host, "autofix_lint")
    number = client.issue_create("autofix line", _issue_body())
    client.issue_update(number, labels_add=["issuesmith:develop-ready"])
    outcome = _run_until(client, number, env, host, expect_done=True, max_rounds=60)
    assert outcome == "done"
    # The local forge records the PR merge without materialising base, so the
    # merged content is the PR head branch that merge pushed to origin.
    origin = tmp_path / "origin.git"
    refs = subprocess.run(
        [
            "git",
            "--git-dir",
            str(origin),
            "for-each-ref",
            "--format=%(refname)",
            f"refs/heads/feat/issue-{number}-*",
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    assert len(refs) == 1, refs
    merged = subprocess.run(
        ["git", "--git-dir", str(origin), "show", f"{refs[0]}:src/feature.py"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert merged.returncode == 0, merged.stderr
    assert merged.stdout == "VALUE = 2\n"
    subjects = subprocess.run(
        ["git", "--git-dir", str(origin), "log", "--format=%s", f"main..{refs[0]}"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    assert "Apply gate auto-fixes" in subjects


def test_commit_tracked_changes_is_noop_on_clean_tree(tmp_path: Path) -> None:
    """refs #7408 AC2: no tracked changes -> no commit, untracked files stay untracked."""
    repo = tmp_path / "repo"
    _init_feature_seed(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "untracked.txt").write_text("scratch\n", encoding="utf-8")
    from issuesmith.lines.develop.review import _commit_tracked_changes

    assert _commit_tracked_changes(str(repo)) == ""
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == before
    status = _git(repo, "status", "--porcelain").stdout
    assert "?? untracked.txt" in status


def test_commit_tracked_changes_commits_tracked_edits_only(tmp_path: Path) -> None:
    """refs #7408: tracked edits are committed as one fixed-subject commit."""
    repo = tmp_path / "repo"
    _init_feature_seed(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    (repo / "src" / "feature.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("scratch\n", encoding="utf-8")
    from issuesmith.lines.develop.review import _commit_tracked_changes

    assert _commit_tracked_changes(str(repo)) == ""
    assert _git(repo, "show", "HEAD:src/feature.py").stdout == "VALUE = 2\n"
    assert _git(repo, "log", "-1", "--format=%s").stdout.strip() == "Apply gate auto-fixes"
    files = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert files == ["src/feature.py"]


def _review_step_context(worktree: Path, *, branch: str = "feat/x") -> StepContext:
    return StepContext(
        issue_number="1",
        base_branch="main",
        handler_name="develop",
        is_cross_repo="false",
        target_clone_path="",
        source="test",
        workflow_name="issuesmith",
        m1_result_filename="",
        m1r_result_filename="",
        worktree_path=str(worktree),
        branch=branch,
    )


def _dispatch_context(worktree: Path, *, branch: str = "feat/x") -> dict[str, str]:
    return {
        "issue_number": "1",
        "base_branch": "main",
        "worktree_path": str(worktree),
        "branch": branch,
        "workflow_name": "issuesmith",
    }


def _bare_origin_with_worktree(
    tmp_path: Path, *, branch: str = "feat/x"
) -> tuple[Path, Path]:
    seed = tmp_path / "seed"
    origin = tmp_path / "origin.git"
    _init_feature_seed(seed)
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "init")
    subprocess.run(
        ["git", "clone", "--bare", str(seed), str(origin)],
        check=True,
        capture_output=True,
    )
    worktree = tmp_path / "worktree"
    subprocess.run(
        ["git", "clone", str(origin), str(worktree)],
        check=True,
        capture_output=True,
    )
    _git(worktree, "config", "user.email", "t@example.com")
    _git(worktree, "config", "user.name", "T")
    _git(worktree, "checkout", "-b", branch)
    return origin, worktree


def test_review_blocks_before_gate_when_remote_branch_moved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs sumipan/nexus#7400 AC3 — moved remote branch blocks before review gate."""
    from issuesmith.lines.develop import review as develop_review
    from issuesmith.ops.dispatch import map_step_result

    _origin, worktree = _bare_origin_with_worktree(tmp_path)
    (worktree / "src" / "feature.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "impl")
    _git(worktree, "push", "-u", "origin", "feat/x")

    mover = tmp_path / "mover"
    subprocess.run(
        ["git", "clone", str(_origin), str(mover)],
        check=True,
        capture_output=True,
    )
    _git(mover, "config", "user.email", "t@example.com")
    _git(mover, "config", "user.name", "T")
    _git(mover, "checkout", "feat/x")
    (mover / "remote-only.txt").write_text("x\n", encoding="utf-8")
    _git(mover, "add", "remote-only.txt")
    _git(mover, "commit", "-m", "advance remote")
    _git(mover, "push", "origin", "feat/x")

    gate_calls: list[int] = []
    monkeypatch.setattr(
        "issuesmith.ops.dispatch.run_requires_loop",
        lambda *args, **kwargs: gate_calls.append(1) or 1,
    )
    monkeypatch.setattr(
        "issuesmith.ops.dispatch.get_forge",
        lambda: MagicMock(),
    )
    ctx = _review_step_context(worktree)
    result = develop_review.run(ctx, StepConfig())
    assert result.andon is not None
    assert result.andon.rule_id == "review.branch_moved"
    assert result.andon.kind == "blocked"
    assert map_step_result(
        result, step_id="review", context=_dispatch_context(worktree)
    ) == 1
    assert gate_calls == []

    fresh = tmp_path / "fresh"
    subprocess.run(
        ["git", "clone", str(_origin), str(fresh)],
        check=True,
        capture_output=True,
    )
    _git(fresh, "config", "user.email", "t@example.com")
    _git(fresh, "config", "user.name", "T")
    _git(fresh, "checkout", "-b", "feat/unpushed")
    unpushed = develop_review.run(
        _review_step_context(fresh, branch="feat/unpushed"),
        StepConfig(),
    )
    assert unpushed.status == "done"
    assert unpushed.markers == ["REVIEW_READY"]


def test_review_repair_rechecks_gate_without_rerunning_branch_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs sumipan/nexus#7400 AC4 — repair re-runs review gate only, not branch check."""
    from issuesmith.gate_registry import Violation
    from issuesmith.lines.develop import review as develop_review
    from issuesmith.ops.dispatch import map_step_result
    from issuesmith.repair import RequiresResult

    _origin, worktree = _bare_origin_with_worktree(tmp_path)
    (worktree / "src" / "feature.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "impl")
    _git(worktree, "push", "-u", "origin", "feat/x")

    ls_remote_calls: list[int] = []
    real_git = develop_review._git_in_worktree

    def _counting_git(
        wt: Path, args: list[str], *, timeout: int = develop_review._BRANCH_CHECK_TIMEOUT_SEC
    ):
        if "ls-remote" in args:
            ls_remote_calls.append(1)
        return real_git(wt, args, timeout=timeout)

    monkeypatch.setattr(develop_review, "_git_in_worktree", _counting_git)

    blocking = Violation(
        rule_id="review.review",
        severity="fail",
        message="needs fix",
        location=None,
        auto_fixable=False,
        fix_hint=None,
    )
    eval_calls: list[int] = []

    def _eval_side_effect(*_args, **_kwargs):
        eval_calls.append(1)
        if len(eval_calls) == 1:
            return RequiresResult(blocking=[blocking])
        return RequiresResult(blocking=[])

    step_cfg = StepConfig(
        requires=("review",),
        review=ReviewConfig(
            role="review",
            template="review.md",
            success_status="REVIEW_PASS",
            failure_status="REVIEW_FAIL",
        ),
        repair=RepairConfig(max=1),
    )

    with (
        patch(
            "issuesmith.ops.dispatch._build_requires_gates",
            return_value={"review": MagicMock()},
        ),
        patch("issuesmith.ops.dispatch._fetch_fresh_issue_body", return_value=""),
        patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]),
        patch("issuesmith.repair.evaluate_requires", side_effect=_eval_side_effect),
        patch("issuesmith.ops.dispatch._run_repair_step", return_value=None),
        patch("issuesmith.ops.dispatch.get_forge", return_value=MagicMock()),
    ):
        ctx = _review_step_context(worktree)
        ready = develop_review.run(ctx, step_cfg)
        assert ready.markers == ["REVIEW_READY"]
        assert (
            map_step_result(
                ready,
                step_id="review",
                context=_dispatch_context(worktree),
                step_cfg=step_cfg,
            )
            == 0
        )

    assert len(ls_remote_calls) == 1
    assert len(eval_calls) == 2


@pytest.mark.parametrize(
    "git_side_effect",
    [
        pytest.param("rev_parse_fail", id="rev_parse_fail"),
        pytest.param("ls_remote_fail", id="ls_remote_fail"),
        pytest.param("timeout", id="timeout"),
        pytest.param("invalid_output", id="invalid_output"),
    ],
)
def test_review_returns_broken_andon_when_branch_check_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    git_side_effect: str,
) -> None:
    """Refs sumipan/nexus#7400 AC5 — branch check failures become broken andon."""
    from issuesmith.lines.develop import review as develop_review

    repo = tmp_path / "repo"
    _init_feature_seed(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    _git(repo, "checkout", "-b", "feat/x")
    ctx = _review_step_context(repo)

    def _runner(wt: Path, args: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        if git_side_effect == "rev_parse_fail":
            return subprocess.CompletedProcess(args, 1, "", "git failed")
        if git_side_effect == "ls_remote_fail":
            if args[:1] == ["rev-parse"]:
                return subprocess.CompletedProcess(args, 0, "abc123", "")
            return subprocess.CompletedProcess(args, 1, "", "remote failed")
        if git_side_effect == "timeout":
            if args[:1] == ["rev-parse"]:
                return subprocess.CompletedProcess(args, 0, "abc123", "")
            raise subprocess.TimeoutExpired("git", develop_review._BRANCH_CHECK_TIMEOUT_SEC)
        if args[:1] == ["rev-parse"]:
            return subprocess.CompletedProcess(args, 0, "abc123", "")
        return subprocess.CompletedProcess(args, 0, "not-a-remote-line", "")

    monkeypatch.setattr(develop_review, "_git_in_worktree", _runner)
    result = develop_review.run(ctx, StepConfig())
    assert result.andon is not None
    assert result.andon.rule_id == "review.branch_check_failed"
    assert result.markers != ["REVIEW_READY"]


@pytest.mark.parametrize(
    "git_side_effect",
    [
        pytest.param("invalid_remote_sha", id="invalid_remote_sha"),
        pytest.param("wrong_remote_ref", id="wrong_remote_ref"),
        pytest.param("invalid_local_sha", id="invalid_local_sha"),
    ],
)
def test_review_branch_check_invalid_refs_not_branch_moved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    git_side_effect: str,
) -> None:
    """Refs sumipan/nexus#7400 — malformed ls-remote output is branch_check_failed."""
    from issuesmith.lines.develop import review as develop_review

    repo = tmp_path / "repo"
    _init_feature_seed(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    _git(repo, "checkout", "-b", "feat/x")
    ctx = _review_step_context(repo)

    def _runner(wt: Path, args: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        if args[:1] == ["rev-parse"]:
            sha = "ghij" if git_side_effect == "invalid_local_sha" else "abc1234"
            return subprocess.CompletedProcess(args, 0, sha, "")
        if git_side_effect == "invalid_remote_sha":
            return subprocess.CompletedProcess(
                args, 0, "not-a-sha\trefs/heads/feat/x", ""
            )
        if git_side_effect == "wrong_remote_ref":
            return subprocess.CompletedProcess(
                args, 0, "def5678\trefs/heads/other", ""
            )
        raise AssertionError(f"unexpected git args: {args}")

    monkeypatch.setattr(develop_review, "_git_in_worktree", _runner)
    result = develop_review.run(ctx, StepConfig())
    assert result.andon is not None
    assert result.andon.rule_id == "review.branch_check_failed"
    assert result.andon.rule_id != "review.branch_moved"


def test_cli_dispatch_raises_broken_andon_when_commit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """refs #7408 AC3: commit failure -> broken andon with git stderr, no execv."""
    repo = tmp_path / "repo"
    _init_feature_seed(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    (repo / "src" / "feature.py").write_text("VALUE = 2\n", encoding="utf-8")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho hook-refused >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    from issuesmith.lines.develop import review as develop_review

    captured: list = []
    execv_calls: list = []

    def _fake_map(result, *, step_id, context):
        captured.append((result, step_id, context))
        return 1

    monkeypatch.setattr(develop_review, "map_step_result", _fake_map)
    monkeypatch.setattr(develop_review.os, "execv", lambda *a, **k: execv_calls.append(a))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "review",
            "dispatch",
            "issue_number=1",
            f"worktree_path={repo}",
            "base_branch=main",
            "branch=feat/x",
        ],
    )
    assert develop_review._cli_dispatch() == 1
    assert execv_calls == []
    assert len(captured) == 1
    result, step_id, _context = captured[0]
    assert step_id == "review"
    assert result.status == "andon"
    assert result.andon is not None
    assert result.andon.kind == "broken"
    assert "hook-refused" in result.andon.summary


def test_reference_config_passes_config_show() -> None:
    env = {
        **os.environ,
        "ISSUESMITH_CONFIG": str(_PACKAGE_FILES / "issuesmith.yaml"),
    }
    proc = subprocess.run(
        ["python3", "-m", "issuesmith", "config", "show"],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    steps = set(payload.get("steps") or {})
    assert steps == {"verify", "prepare", "review", "merge", "implement", "repair"}


@pytest.fixture
def develop_hooks_forge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Local forge + reference develop config for develop_hooks tests (nexus#7150)."""
    forge_root = tmp_path / "forge"
    forge_root.mkdir()
    monkeypatch.setenv("GHDAG_FORGE", "local")
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(forge_root))
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(_PACKAGE_FILES / "issuesmith.yaml"))
    for key in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_REPOSITORIES", "GITHUB_REPOSITORY"):
        monkeypatch.delenv(key, raising=False)
    reset_config_cache()
    client = get_forge()
    yield client
    reset_config_cache()


def _ensure_issue_number(client, number: int) -> None:
    while True:
        n = client.issue_create("padding", "")
        if n >= number:
            return


def _comment_count(client, number: int) -> int:
    return len(client.get_issue_comments(number))


def test_develop_hooks_comments_once_on_line_task_failure(develop_hooks_forge) -> None:
    client = develop_hooks_forge
    _ensure_issue_number(client, 42)
    assert client.issue_get(42, fields=["number"])["number"] == 42
    before = _comment_count(client, 42)
    task = SimpleNamespace(
        idempotency_key="issuesmith:develop:42",
        annotations={"step_name": "verify"},
    )
    metrics = SimpleNamespace(failure_class=None)
    DevelopLineHooks().on_task_failure("task-uuid-1", task, 1, "boom", metrics)
    assert _comment_count(client, 42) == before + 1
    body = client.get_issue_comments(42)[-1]["body"]
    assert "<!-- issuesmith-task-failure -->" in body
    assert "verify" in body
    assert "boom" in body
    assert "python3 -m issuesmith resume 42 --from verify --handler develop" in body


def test_develop_hooks_parses_generation_key(develop_hooks_forge) -> None:
    client = develop_hooks_forge
    _ensure_issue_number(client, 42)
    before = _comment_count(client, 42)
    task = SimpleNamespace(
        idempotency_key="issuesmith:develop:42:3",
        annotations={"step_name": "implement"},
    )
    metrics = SimpleNamespace(failure_class=None)
    DevelopLineHooks().on_task_failure("uuid-g", task, 2, "err", metrics)
    assert _comment_count(client, 42) == before + 1


def test_develop_hooks_ignores_non_line_task(develop_hooks_forge) -> None:
    client = develop_hooks_forge
    _ensure_issue_number(client, 42)
    before = _comment_count(client, 42)
    hooks = DevelopLineHooks()
    metrics = SimpleNamespace(failure_class=None)
    for key in (None, "other:develop:42", "issuesmith:develop:abc"):
        task = SimpleNamespace(idempotency_key=key, annotations={})
        hooks.on_task_failure("u", task, 1, "x", metrics)
    assert _comment_count(client, 42) == before


_EXPECTED_EXTRACT_PATHS = frozenset(
    {
        "issuesmith.yaml",
        "workflows/issuesmith.yml",
        "workflows/develop/templates/prepare.md",
        "workflows/develop/templates/implement.md",
        "workflows/develop/templates/verify.md",
        "workflows/develop/templates/review.md",
        "workflows/develop/templates/merge.md",
        "workflows/develop/prompts/implement.md",
        "workflows/develop/prompts/review.md",
    }
)


@pytest.fixture(scope="module")
def built_wheel_site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the wheel once (offline, no build isolation) and install it to a target dir."""
    tmp_path = tmp_path_factory.mktemp("wheel")
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            str(_REPO_ROOT),
            "--no-deps",
            "--no-build-isolation",
            "-w",
            str(wheel_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    wheels = sorted(wheel_dir.glob("issuesmith-*.whl"))
    assert len(wheels) == 1, wheels
    install_root = tmp_path / "site"
    install_root.mkdir()
    ins = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            str(wheels[0]),
            "--no-deps",
            "--target",
            str(install_root),
        ],
        capture_output=True,
        text=True,
    )
    assert ins.returncode == 0, ins.stderr
    return install_root


def test_built_wheel_imports_reference_line_modules(tmp_path: Path, built_wheel_site: Path) -> None:
    """refs #7197 AC1: wheel install exposes packaged develop-line modules."""
    install_root = built_wheel_site
    install_str = str(install_root.resolve())
    script = textwrap.dedent(
        f"""\
        import issuesmith
        from pathlib import Path
        root = Path({install_str!r}).resolve()
        pkg = Path(issuesmith.__file__).resolve()
        assert str(root) in str(pkg), (root, pkg)
        from issuesmith.lines.develop import hooks, implement, merge, prepare, review, verify
        assert hasattr(hooks, "DevelopLineHooks")
        """
    )
    (tmp_path / "outside").mkdir()
    probe = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path / "outside",
        env={**os.environ, "PYTHONPATH": str(install_root)},
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr + probe.stdout


def test_built_wheel_extract_line_writes_all_files(tmp_path: Path, built_wheel_site: Path) -> None:
    """refs #7197 AC2: extract-line from wheel writes nine wiring files."""
    install_root = built_wheel_site
    dest = tmp_path / "extracted"
    (tmp_path / "cwd").mkdir()
    proc = subprocess.run(
        [sys.executable, "-m", "issuesmith", "config", "extract-line", "develop", str(dest)],
        cwd=tmp_path / "cwd",
        env={**os.environ, "PYTHONPATH": str(install_root)},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    paths = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    assert paths == _EXPECTED_EXTRACT_PATHS
    for rel in _EXPECTED_EXTRACT_PATHS:
        assert (dest / rel).is_file(), rel


def test_extract_line_refuses_existing_file(tmp_path: Path) -> None:
    """refs #7197 AC7: extract-line refuses to overwrite existing wiring."""
    dest = tmp_path / "host"
    extract_line("develop", dest)
    before = (dest / "issuesmith.yaml").read_text(encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "issuesmith", "config", "extract-line", "develop", str(dest)],
        cwd=_REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stderr
    assert "error[CONFLICT]" in proc.stderr
    assert (dest / "issuesmith.yaml").read_text(encoding="utf-8") == before
    file_count = sum(1 for path in dest.rglob("*") if path.is_file())
    assert file_count == len(_EXPECTED_EXTRACT_PATHS)


def test_extract_line_rejects_unknown_line(tmp_path: Path) -> None:
    """refs #7197 AC8: unknown line name is INVALID_VALUE with develop hint."""
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "issuesmith",
            "config",
            "extract-line",
            "nosuch",
            str(tmp_path / "out"),
        ],
        cwd=_REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2, proc.stderr
    assert "error[INVALID_VALUE]" in proc.stderr
    assert f"lines: {', '.join(LINES)}" in proc.stderr


def test_repository_has_no_legacy_workflows_develop_tree() -> None:
    """refs #7197 AC10: reference line wiring is not duplicated under workflows/develop."""
    assert not (_REPO_ROOT / "workflows" / "develop").exists()


def test_merge_step_rebumps_once_then_merges(tmp_path: Path, monkeypatch) -> None:
    """refs #7398 AC8: merge step rebump once then merges when PR becomes mergeable."""
    from unittest.mock import MagicMock

    from issuesmith.lines.develop import merge as develop_merge
    from issuesmith.verbs.publish import RebumpResult

    worktree = tmp_path / "wt"
    worktree.mkdir()
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(worktree)],
        check=True,
        capture_output=True,
    )
    state_calls: list[str] = []

    def fake_merge_state(_client, _repo, _pr) -> dict:
        state_calls.append("state")
        if len(state_calls) == 1:
            return {"mergeable": "CONFLICTING"}
        return {"mergeable": "MERGEABLE"}

    monkeypatch.setattr(develop_merge, "merge_state", fake_merge_state)
    merge_calls: list[int] = []
    monkeypatch.setattr(
        develop_merge,
        "merge_pr",
        lambda _client, _repo, pr: merge_calls.append(pr),
    )
    monkeypatch.setattr(develop_merge, "publish_branch", lambda *_a, **_k: None)
    monkeypatch.setattr(develop_merge, "_locate_pr", lambda *_a, **_k: 42)
    monkeypatch.setattr(develop_merge, "get_forge", lambda **_k: MagicMock())
    monkeypatch.setattr(
        develop_merge,
        "rebump_onto_base",
        lambda *_a, **_k: RebumpResult(status="rebumped"),
    )
    monkeypatch.setattr(develop_merge, "cleanup_worktrees", lambda *_a, **_k: None)
    monkeypatch.setattr(develop_merge, "cleanup_branches", lambda *_a, **_k: None)
    monkeypatch.setattr(develop_merge, "close_issue", lambda *_a, **_k: None)

    ctx = StepContext(
        issue_number="7398",
        base_branch="main",
        handler_name="develop",
        is_cross_repo="false",
        target_clone_path="",
        source="",
        workflow_name="issuesmith",
        m1_result_filename="",
        m1r_result_filename="",
        worktree_path=str(worktree),
        branch="feat/issue-7398-test",
    )
    result = develop_merge.run(ctx, StepConfig())
    assert result.status == "done"
    assert result.markers == ["MERGE_DONE"]
    assert len(state_calls) == 2
    assert merge_calls == [42]


def test_merge_step_andons_when_rebump_not_bump_only(tmp_path: Path, monkeypatch) -> None:
    """refs #7398 AC9: not_bump_only rebump keeps blocked andon without merge."""
    from unittest.mock import MagicMock

    from issuesmith.lines.develop import merge as develop_merge
    from issuesmith.verbs.publish import RebumpResult

    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.setattr(
        develop_merge,
        "merge_state",
        lambda *_a, **_k: {"mergeable": "CONFLICTING"},
    )
    monkeypatch.setattr(develop_merge, "merge_pr", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no merge")))
    monkeypatch.setattr(develop_merge, "publish_branch", lambda *_a, **_k: None)
    monkeypatch.setattr(develop_merge, "_locate_pr", lambda *_a, **_k: 7)
    monkeypatch.setattr(develop_merge, "get_forge", lambda **_k: MagicMock())
    monkeypatch.setattr(
        develop_merge,
        "rebump_onto_base",
        lambda *_a, **_k: RebumpResult(
            status="not_bump_only", conflict_files=("src/x.py",), detail=""
        ),
    )

    ctx = StepContext(
        issue_number="7398",
        base_branch="main",
        handler_name="develop",
        is_cross_repo="false",
        target_clone_path="",
        source="",
        workflow_name="issuesmith",
        m1_result_filename="",
        m1r_result_filename="",
        worktree_path=str(worktree),
        branch="feat/issue-7398-test",
    )
    result = develop_merge.run(ctx, StepConfig())
    assert result.status == "andon"
    assert result.andon is not None
    assert result.andon.kind == "blocked"
    assert "src/x.py" in result.andon.summary


def test_merge_step_generic_merge_error_stays_broken(tmp_path: Path, monkeypatch) -> None:
    """refs #7398: only 405 / base-modified merge errors trigger a rebump."""
    from unittest.mock import MagicMock

    from issuesmith.lines.develop import merge as develop_merge

    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.setattr(
        develop_merge, "merge_state", lambda *_a, **_k: {"mergeable": "MERGEABLE"}
    )

    def _boom(*_a, **_k):
        raise RuntimeError("403 Forbidden")

    rebump_calls: list[object] = []
    monkeypatch.setattr(develop_merge, "merge_pr", _boom)
    monkeypatch.setattr(develop_merge, "publish_branch", lambda *_a, **_k: None)
    monkeypatch.setattr(develop_merge, "_locate_pr", lambda *_a, **_k: 7)
    monkeypatch.setattr(develop_merge, "get_forge", lambda **_k: MagicMock())
    monkeypatch.setattr(
        develop_merge, "rebump_onto_base", lambda *a, **_k: rebump_calls.append(a)
    )

    ctx = StepContext(
        issue_number="7398",
        base_branch="main",
        handler_name="develop",
        is_cross_repo="false",
        target_clone_path="",
        source="",
        workflow_name="issuesmith",
        m1_result_filename="",
        m1r_result_filename="",
        worktree_path=str(worktree),
        branch="feat/issue-7398-test",
    )
    result = develop_merge.run(ctx, StepConfig())
    assert result.status == "andon"
    assert result.andon is not None
    assert result.andon.kind == "broken"
    assert "403 Forbidden" in result.andon.summary
    assert rebump_calls == []
