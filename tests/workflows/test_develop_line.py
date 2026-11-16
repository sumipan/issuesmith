"""Reference develop line acceptance tests (sumipan/nexus#6614)."""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import signal
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml
from ghdag.forge import get_forge

from issuesmith.andon import list_open
from issuesmith.config import reset_config_cache

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEVELOP_DIR = _REPO_ROOT / "workflows" / "develop"
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
    MODE="${FAKE_ENGINE_MODE:-good}"
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
    shutil.copytree(_DEVELOP_DIR, seed / "workflows" / "develop")
    dag_dir = seed / "workflows" / "develop" / "dag"
    dag_dir.mkdir()
    wf_src = seed / "workflows" / "develop" / "workflow.yml"
    wf_data = yaml.safe_load(wf_src.read_text(encoding="utf-8"))
    wf_data["template_dir"] = "../templates"
    (dag_dir / "workflow.yml").write_text(
        yaml.safe_dump(wf_data), encoding="utf-8"
    )
    cfg_path = seed / "workflows" / "develop" / "issuesmith.yaml"
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    data["repo"] = slug
    data["supported_repos"] = [slug]
    data.setdefault("paths", {})["workflow"] = "workflows/develop/dag/workflow.yml"
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
    host_cfg = yaml.safe_load((host / "workflows" / "develop" / "issuesmith.yaml").read_text())
    host_cfg["repo"] = slug
    host_cfg["supported_repos"] = [slug]
    host_cfg.setdefault("paths", {})["workflow"] = "workflows/develop/dag/workflow.yml"
    (host / "issuesmith.yaml").write_text(yaml.safe_dump(host_cfg), encoding="utf-8")
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
        "PYTHONPATH": f"{host / 'workflows' / 'develop'}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        "GHDAG_FORGE": "local",
        "GHDAG_FORGE_ROOT": str(forge_root),
        "ISSUESMITH_QUEUE_DIR": str(tmp_path / "queue"),
        "FAKE_ENGINE_MODE": "good",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
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
            "workflows/develop/dag",
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


def _run_dag_once(env: dict[str, str], host: Path) -> None:
    exec_path = host / "jobs" / "exec.jsonl"
    if not exec_path.exists():
        return
    uuids: list[str] = []
    for line in exec_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        uuids.append(json.loads(line)["uuid"])
    if not uuids:
        return
    done_dir = host / "jobs" / "done"
    proc = subprocess.Popen(
        [
            "python3",
            "-m",
            "ghdag",
            "run",
            str(exec_path),
            "--interval-sec",
            "0.2",
        ],
        cwd=host,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        while proc.poll() is None:
            if all((done_dir / uuid).exists() for uuid in uuids):
                break
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            proc.wait()


def _resume_verify_from_andon(host: Path, env: dict[str, str], issue: int) -> None:
    """Re-run verify and downstream after a verify-step andon (ghdag recover)."""
    proc = subprocess.run(
        [
            "python3",
            "-m",
            "ghdag",
            "dag",
            "recover",
            "--issue",
            str(issue),
            "--handler",
            "develop",
            "--from",
            "verify",
            "--workflows-dir",
            "workflows/develop/dag",
            "--exec-jsonl",
            "jobs/exec.jsonl",
        ],
        cwd=host,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


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
    state = client.issue_get(number, fields=["state"])["state"]
    assert state == "CLOSED"
    pulls = client.pr_list(state="all")
    assert any(
        p.get("merged") or str(p.get("state") or "").upper() == "CLOSED" for p in pulls
    )


def test_failing_verify_raises_andon_and_resume_continues_from_verify(
    host_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    host, client, env, _slug = host_env
    monkeypatch.setenv("FAKE_ENGINE_MODE", "bad_lint")
    env["FAKE_ENGINE_MODE"] = "bad_lint"
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
    monkeypatch.setenv("FAKE_ENGINE_MODE", "good")
    env["FAKE_ENGINE_MODE"] = "good"
    proc = subprocess.run(
        ["python3", "-m", "issuesmith", "andon", "answer", andon_id, "resume"],
        cwd=host,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    _resume_verify_from_andon(host, env, number)
    outcome = _run_until(
        client, number, env, host, expect_done=True, max_rounds=60
    )
    assert outcome == "done"


def test_step_modules_import_only_public_issuesmith_modules() -> None:
    line_dir = _DEVELOP_DIR / "develop_line"
    for path in sorted(line_dir.glob("*.py")):
        if path.name == "__init__.py":
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


def test_reference_config_passes_config_show(host_env) -> None:
    host, _client, env, _slug = host_env
    proc = subprocess.run(
        ["python3", "-m", "issuesmith", "config", "show"],
        cwd=host,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    steps = set(payload.get("steps") or {})
    assert steps == {"verify", "prepare", "review", "merge", "implement", "repair"}


def test_readme_quick_start_references_existing_files() -> None:
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"## Quick Start\n(.*?)(?:\n## |\Z)", readme, re.DOTALL)
    assert match, "Quick Start section missing"
    section = match.group(1)
    for path in re.findall(r"workflows/develop/[\w./-]+", section):
        if path.endswith("/"):
            continue
        assert (_REPO_ROOT / path).exists(), path
    for mod in re.findall(r"develop_line\.(\w+)", section):
        assert (_DEVELOP_DIR / "develop_line" / f"{mod}.py").exists(), mod
