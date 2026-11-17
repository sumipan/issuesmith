"""Reference develop line acceptance tests."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from ghdag.forge import get_forge

from issuesmith.andon import list_open
from issuesmith.config import reset_config_cache
from issuesmith.lines import LINES, extract_line
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
      printf '%s\\n' 'PIPELINE_STATUS: REVIEW_PASS'
      exit 0
    fi
    TARGET="src/feature.py"
    case "$MODE" in
      bad_lint)
        printf 'import os\\n\\nVALUE = 2\\n' >>"$TARGET"
        ;;
      good)
        printf 'VALUE = 2\\n' >"$TARGET"
        printf '%s\\n' \
          'from src import feature' \
          '' \
          'def test_value() -> None:' \
          '    assert feature.VALUE == 2' \
          >tests/test_feature.py
        ;;
      *)
        echo "unknown mode: $MODE" >&2
        exit 1
        ;;
    esac
    git add src/feature.py
    if [[ "$MODE" == good ]]; then
      git add tests/test_feature.py
    fi
    git commit -m "fake engine change"
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
        "[tool.ruff]\nline-length = 100\n",
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


def _issue_body() -> str:
    return textwrap.dedent(
        """\
        ```yaml
        base_branch: main
        allow_paths:
          - src/**
          - tests/**
        ```

        ## Task
        Bump VALUE in src/feature.py.
        """
    )


@pytest.fixture
def host_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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


def _run_dag_once(env: dict[str, str], host: Path) -> None:
    """Run ``ghdag run`` until every queued task is terminal.

    A ``--hooks`` module sends SIGINT to the runner once the last task finishes,
    so the test blocks on the process instead of polling a clock.
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
    hooks_dir.mkdir(exist_ok=True)
    (hooks_dir / "develop_line_stop_hooks.py").write_text(_STOP_HOOKS, encoding="utf-8")
    run_env = {
        **env,
        "PYTHONPATH": f"{hooks_dir}{os.pathsep}{env.get('PYTHONPATH', '')}",
        "DEVELOP_LINE_TEST_UUIDS": ",".join(uuids),
        "DEVELOP_LINE_TEST_DONE_DIR": str(done_dir),
    }
    subprocess.run(
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
        check=False,
    )


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
        if any(
            lb.endswith(":andon-decision") or lb == "issuesmith:andon-decision"
            for lb in labels
        ):
            return "andon"
        if expect_done and state == "CLOSED" and "issuesmith:develop-done" in labels:
            return "done"
        _watch_once(env, host)
        _run_dag_once(env, host)
    return "timeout"


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
    outcome = _run_until(
        client, number, env, host, expect_done=False, max_rounds=60
    )
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
    outcome = _run_until(
        client, number, env, host, expect_done=True, max_rounds=60
    )
    assert outcome == "done"


def test_step_modules_import_only_public_issuesmith_modules() -> None:
    line_dir = _LINE_DEV_DIR
    for path in sorted(line_dir.glob("*.py")):
        if path.name in {"__init__.py", "hooks.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def _ok(name: str) -> bool:
            return any(
                name == allowed or name.startswith(f"{allowed}.")
                for allowed in _ALLOWED_ISSMITH_IMPORTS
            )

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
    DevelopLineHooks().on_task_failure(
        "task-uuid-1", task, 1, "boom", metrics
    )
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


def test_built_wheel_imports_reference_line_modules(
    tmp_path: Path, built_wheel_site: Path
) -> None:
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


def test_built_wheel_extract_line_writes_all_files(
    tmp_path: Path, built_wheel_site: Path
) -> None:
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
