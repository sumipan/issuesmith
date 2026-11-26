"""Develop line diagnose step (issue #8078)."""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

from issuesmith.andon import Andon, list_open_records, raise_andon
from issuesmith.config import StepConfig, reset_config_cache
from issuesmith.contract import StepContext
from issuesmith.lines.develop import diagnose as develop_diagnose


def _write_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    template_body: str,
    on_proposal: str | None = None,
) -> Path:
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    quota_path = jobs / "quota-gate.json"
    quota_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "deferred_tasks": {},
                "draining_engines": {},
                "running_tasks": {},
                "engines": {
                    "shell": {
                        "status": "available",
                        "observed_at": "2026-11-26T00:00:00+00:00",
                        "reason": "t",
                        "resume_at": None,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    done = jobs / "done"
    done.mkdir()
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "diagnose.md").write_text(template_body, encoding="utf-8")
    (tmp_path / "workflows" / "issuesmith.yml").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "workflows" / "issuesmith.yml").write_text(
        "name: issuesmith\ntemplate_dir: templates\n",
        encoding="utf-8",
    )
    engine_state = tmp_path / ".pipeline-state" / "issuesmith-engine.yml"
    engine_state.parent.mkdir(parents=True, exist_ok=True)
    engine_state.write_text(
        yaml.safe_dump({"design": {"engine": "shell", "model": "bash"}}),
        encoding="utf-8",
    )
    shell_bin = tmp_path / "bin"
    shell_bin.mkdir()
    data = {
        "repo": "example/repo",
        "supported_repos": ["sumipan/issuesmith"],
        "andon": {"diagnose": {"role": "design", "on_proposal": on_proposal}},
        "paths": {
            "exec_jsonl": str(jobs / "exec.jsonl"),
            "done_dir": str(done),
            "diagnoses": str(jobs / "issuesmith-diagnoses.jsonl"),
            "template_dir": str(templates),
            "workflow": str(tmp_path / "workflows" / "issuesmith.yml"),
            "quota_state": str(quota_path),
            "queue_lock": str(jobs / "queue.lock"),
            "metrics": str(jobs / "metrics.jsonl"),
            "engine_state": str(engine_state),
        },
        "engines": {"design": {"heavy": [["shell", "bash"]], "timeout_sec": 10}},
        "steps": {"p1": {"module": "x"}, "implement": {"module": "x"}},
    }
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    monkeypatch.setenv("GHDAG_FORGE", "local")
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(tmp_path / "forge"))
    monkeypatch.chdir(tmp_path)
    reset_config_cache()
    from ghdag.llm import Permission

    import issuesmith.engine as engine_mod

    shell_perm = Permission(
        permission_mode="bypassPermissions",
        output_format="text",
        stream=False,
    )
    monkeypatch.setitem(engine_mod._PERMISSION_OVERRIDES, "shell", shell_perm)
    monkeypatch.setattr(engine_mod, "_maybe_raise_guarded_transient", lambda *_a, **_k: None)
    monkeypatch.setattr(engine_mod, "QUOTA_STATE_PATH", quota_path)
    monkeypatch.setattr(
        engine_mod,
        "_GUARDED_TRANSIENT_STATE_FILE",
        quota_path.parent / "engine-transient-retries.json",
    )
    monkeypatch.setattr(engine_mod, "METRICS_FILE", jobs / "metrics.jsonl")
    monkeypatch.setenv("METRICS_JSONL_PATH", str(jobs / "metrics.jsonl"))
    monkeypatch.setitem(engine_mod.HEAVY_ROUTES, "design", (("shell", "bash"),))
    monkeypatch.setitem(engine_mod.ROLE_ENGINES, "design", ("shell",))

    def _shell_guarded(
        role: str,
        template_path: str,
        variables: list[str],
        success_statuses: list[str],
        failure_status: str,
        cwd: str | None = None,
    ) -> tuple[int, str]:
        from issuesmith.engine import _render_template

        order = _render_template(template_path, variables)
        proc = subprocess.run(
            ["bash", "-c", order],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        vals = {
            item.split("=", 1)[0]: item.split("=", 1)[1]
            for item in variables
            if "=" in item
        }
        sidecar = Path(vals["diagnosis_input"] + ".out")
        output = sidecar.read_text(encoding="utf-8") if sidecar.is_file() else proc.stdout
        done = any(
            line.strip() == f"PIPELINE_STATUS: {status}"
            for line in output.splitlines()
            for status in success_statuses
        )
        return (0, output) if done and proc.returncode == 0 else (1, output or proc.stderr)

    monkeypatch.setattr(develop_diagnose, "run_guarded_output", _shell_guarded)
    return cfg_path


def _engine_state_dir(tmp_path: Path) -> Path:
    path = tmp_path / "jobs" / "diagnose"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _shell_template(mode_file: str = "diagnose-engine-mode.txt") -> str:
    return textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        INPUT="${{diagnosis_input}}"
        STATE_DIR="$$(dirname "$$INPUT")"
        OUTPUT="$$INPUT.out"
        MODE_FILE="$$STATE_DIR/{mode_file}"
        MODE="edit_issue"
        if [[ -f "$$MODE_FILE" ]]; then MODE="$$(cat "$$MODE_FILE")"; fi
        COUNT_FILE="$$STATE_DIR/diagnose-engine-count.txt"
        n=0
        if [[ -f "$$COUNT_FILE" ]]; then n="$$(cat "$$COUNT_FILE")"; fi
        echo $$((n + 1)) >"$$COUNT_FILE"
        case "$$MODE" in
          edit_issue)
            cat <<'EOF' >"$$OUTPUT"
```andon-diagnosis
fix_kind: edit_issue
summary: widen paths
allow_paths_add:
  - src/foo.py
```
PIPELINE_STATUS: DIAGNOSIS_DONE
EOF
            ;;
          fix_line)
            cat <<'EOF' >"$$OUTPUT"
```andon-diagnosis
fix_kind: fix_line
summary: root fix
target_repo: sumipan/issuesmith
title: fix root cause
body: design fix
```
PIPELINE_STATUS: DIAGNOSIS_DONE
EOF
            ;;
          bad_kind)
            cat <<'EOF' >"$$OUTPUT"
```andon-diagnosis
fix_kind: clean_env
summary: nope
steps:
  - wipe
rerun_from: p1
```
PIPELINE_STATUS: DIAGNOSIS_DONE
EOF
            ;;
          engine_fail)
            echo PIPELINE_STATUS: DIAGNOSIS_FAILED
            exit 1
            ;;
          done_exit_1)
            cat <<'EOF' >"$$OUTPUT"
```andon-diagnosis
fix_kind: edit_issue
summary: must not persist
allow_paths_add:
  - src/foo.py
```
PIPELINE_STATUS: DIAGNOSIS_DONE
EOF
            exit 1
            ;;
        esac
        """
    )


def _ctx(tmp_path: Path, issue_number: int) -> StepContext:
    return StepContext(
        issue_number=str(issue_number),
        base_branch="main",
        handler_name="develop",
        is_cross_repo="false",
        target_clone_path="",
        target_repo="example/repo",
        issue_repo="example/repo",
        source="",
        workflow_name="issuesmith",
        m1_result_filename="",
        m1r_result_filename="",
        worktree_path=str(tmp_path / "worktree"),
        branch="feat/x",
    )


def _raise_blocked(client, number: int, metrics: Path) -> Andon:
    andon = Andon(
        id=f"wf:{number}:p1:0",
        kind="blocked",
        issue=number,
        step="p1",
        summary="blocked",
        facts={"failed_step": "p1", "trigger": "step", "rule_ids": ["ac_format.missing"]},
        options=[],
    )
    raise_andon(client, andon, metrics_path=metrics)
    return andon


@pytest.fixture
def forge_client(tmp_path, monkeypatch):
    from ghdag.forge import get_forge

    _write_config(tmp_path, monkeypatch, template_body=_shell_template())
    client = get_forge()
    yield client
    reset_config_cache()


def test_diagnose_builds_input_from_andon_and_artifacts(tmp_path, forge_client, monkeypatch):
    """Refs #8078 AC1."""
    metrics = tmp_path / "m.jsonl"
    number = forge_client.issue_create("body", "issue body")
    andon = _raise_blocked(forge_client, number, metrics)
    from issuesmith.andon import note

    note(forge_client, andon.id, "diagnosis_requested", andon.id)
    wt = tmp_path / "worktree"
    wt.mkdir()
    result = develop_diagnose.run(_ctx(tmp_path, number), StepConfig(template="diagnose.md"))
    assert result.status == "done"
    digest_path = tmp_path / "jobs" / "diagnose"
    files = list(digest_path.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["andon"]["facts"]["failed_step"] == "p1"
    assert payload["paths"]["worktree"] == str(wt.resolve())
    assert "edit_issue" in payload["allowed_fix_kinds"]
    count = int((tmp_path / "jobs" / "diagnose" / "diagnose-engine-count.txt").read_text())
    assert count == 1
    records = list_open_records(forge_client)
    assert records[0]["notes"]["diagnosis"] == "edit_issue"


def test_resolves_result_and_stderr_from_exec_and_audit(tmp_path, monkeypatch):
    """Refs #8078 AC2."""
    template = _shell_template()
    _write_config(tmp_path, monkeypatch, template_body=template)
    from ghdag.forge import get_forge

    client = get_forge()
    metrics = tmp_path / "m.jsonl"
    number = client.issue_create("body", "body")
    andon = _raise_blocked(client, number, metrics)
    from issuesmith.andon import note

    note(client, andon.id, "diagnosis_requested", andon.id)
    jobs = tmp_path / "jobs"
    result_file = jobs / "p1-result.md"
    result_file.write_text("failed", encoding="utf-8")
    stderr_file = jobs / "p1.err"
    stderr_file.write_text("boom", encoding="utf-8")
    task_uuid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    exec_row = {
        "uuid": task_uuid,
        "result_path": str(result_file),
        "idempotency_key": f"issuesmith:p1:{number}:2",
        "annotations": {"step_name": "p1"},
    }
    (jobs / "exec.jsonl").write_text(json.dumps(exec_row) + "\n", encoding="utf-8")
    audit_row = {"uuid": task_uuid, "stderr_path": str(stderr_file)}
    (jobs / "audit.jsonl").write_text(json.dumps(audit_row) + "\n", encoding="utf-8")

    record = next(r for r in list_open_records(client) if r["id"] == andon.id)
    record = dict(record)
    record["evidence"] = f"uuid={task_uuid}"

    input_payload = develop_diagnose._build_input(
        client,
        record,
        issue=number,
        worktree=None,
        exec_jsonl=jobs / "exec.jsonl",
    )
    assert input_payload["paths"]["result_path"] == str(result_file.resolve())
    assert input_payload["paths"]["stderr_path"] == str(stderr_file.resolve())
    reset_config_cache()


def test_edit_issue_proposal_leaves_issue_body_unchanged(tmp_path, forge_client):
    """Refs #8078 AC3."""
    metrics = tmp_path / "m.jsonl"
    body_before = "line one\n"
    number = forge_client.issue_create("body", body_before)
    andon = _raise_blocked(forge_client, number, metrics)
    from issuesmith.andon import note

    note(forge_client, andon.id, "diagnosis_requested", andon.id)
    develop_diagnose.run(_ctx(tmp_path, number), StepConfig(template="diagnose.md"))
    body_after = forge_client.issue_get(number, fields=["body"])["body"]
    assert body_after == body_before
    ledger = (tmp_path / "jobs" / "issuesmith-diagnoses.jsonl").read_text(encoding="utf-8")
    assert "edit_issue" in ledger


def test_fix_line_adds_signature_without_new_issue(tmp_path, forge_client, monkeypatch):
    """Refs #8078 AC4."""
    _engine_state_dir(tmp_path).joinpath("diagnose-engine-mode.txt").write_text(
        "fix_line", encoding="utf-8"
    )
    metrics = tmp_path / "m.jsonl"
    open_count = len(forge_client.list_all_issues("open"))
    number = forge_client.issue_create("body", "body")
    andon = Andon(
        id=f"wf:{number}:p1:0",
        kind="blocked",
        issue=number,
        step="p1",
        summary="blocked",
        facts={"failed_step": "p1", "trigger": "gate_error", "rule_ids": []},
        options=[],
    )
    raise_andon(forge_client, andon, metrics_path=metrics)
    from issuesmith.andon import note

    note(forge_client, andon.id, "diagnosis_requested", andon.id)
    develop_diagnose.run(_ctx(tmp_path, number), StepConfig(template="diagnose.md"))
    assert len(forge_client.list_all_issues("open")) == open_count + 1
    comments = forge_client.get_issue_comments(number)
    joined = "\n".join(c["body"] for c in comments)
    assert "<!-- issuesmith-diagnosis:" in joined


def test_rejects_disallowed_fix_kind(tmp_path, forge_client, monkeypatch):
    """Refs #8078 AC5."""
    _engine_state_dir(tmp_path).joinpath("diagnose-engine-mode.txt").write_text(
        "bad_kind", encoding="utf-8"
    )
    metrics = tmp_path / "m.jsonl"
    number = forge_client.issue_create("body", "body")
    andon = _raise_blocked(forge_client, number, metrics)
    from issuesmith.andon import note

    note(forge_client, andon.id, "diagnosis_requested", andon.id)
    develop_diagnose.run(_ctx(tmp_path, number), StepConfig(template="diagnose.md"))
    records = list_open_records(forge_client)
    assert records[0]["notes"]["diagnosis"] == "failed"


def test_skips_second_llm_run_after_failure(tmp_path, forge_client, monkeypatch):
    """Refs #8078 AC6."""
    _engine_state_dir(tmp_path).joinpath("diagnose-engine-mode.txt").write_text(
        "bad_kind", encoding="utf-8"
    )
    metrics = tmp_path / "m.jsonl"
    number = forge_client.issue_create("body", "body")
    andon = _raise_blocked(forge_client, number, metrics)
    from issuesmith.andon import note

    note(forge_client, andon.id, "diagnosis_requested", andon.id)
    step = StepConfig(template="diagnose.md")
    develop_diagnose.run(_ctx(tmp_path, number), step)
    develop_diagnose.run(_ctx(tmp_path, number), step)
    count = int((tmp_path / "jobs" / "diagnose" / "diagnose-engine-count.txt").read_text())
    assert count == 1


def test_nonzero_engine_exit_with_done_marker_is_failure(tmp_path, forge_client):
    """A guarded engine failure cannot be promoted by a stale success marker."""
    _engine_state_dir(tmp_path).joinpath("diagnose-engine-mode.txt").write_text(
        "done_exit_1", encoding="utf-8"
    )
    number = forge_client.issue_create("body", "body")
    andon = _raise_blocked(forge_client, number, tmp_path / "m.jsonl")
    from issuesmith.andon import note

    note(forge_client, andon.id, "diagnosis_requested", andon.id)
    result = develop_diagnose.run(_ctx(tmp_path, number), StepConfig(template="diagnose.md"))
    assert result.status == "done"
    record = next(r for r in list_open_records(forge_client) if r["id"] == andon.id)
    assert record["notes"]["diagnosis"] == "failed"


def test_on_proposal_hook_records_payload(tmp_path, monkeypatch):
    """Refs #8078 AC13."""
    import sys

    out_path = tmp_path / "hook-out.txt"
    hook_mod = tmp_path / "hook_mod.py"
    hook_mod.write_text(
        f"OUT = {out_path.as_posix()!r}\n"
        "def capture(payload):\n"
        "    open(OUT, 'w', encoding='utf-8').write(payload['fix_kind'])\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(tmp_path))
    template = _shell_template()
    _write_config(
        tmp_path,
        monkeypatch,
        template_body=template,
        on_proposal="hook_mod:capture",
    )
    from ghdag.forge import get_forge

    client = get_forge()
    metrics = tmp_path / "m.jsonl"
    number = client.issue_create("body", "body")
    andon = _raise_blocked(client, number, metrics)
    from issuesmith.andon import note

    note(client, andon.id, "diagnosis_requested", andon.id)
    develop_diagnose.run(_ctx(tmp_path, number), StepConfig(template="diagnose.md"))
    assert out_path.read_text(encoding="utf-8") == "edit_issue"
    reset_config_cache()
    sys.path.remove(str(tmp_path))


def test_on_proposal_hook_error_still_persists_proposal(tmp_path, monkeypatch):
    """Refs #8078 AC14."""
    hook_mod = tmp_path / "hook_mod.py"
    hook_mod.write_text("def boom(_payload): raise RuntimeError('nope')\n", encoding="utf-8")
    import sys

    sys.path.insert(0, str(tmp_path))
    template = _shell_template()
    _write_config(
        tmp_path,
        monkeypatch,
        template_body=template,
        on_proposal="hook_mod:boom",
    )
    from ghdag.forge import get_forge

    client = get_forge()
    metrics = tmp_path / "m.jsonl"
    number = client.issue_create("body", "body")
    andon = _raise_blocked(client, number, metrics)
    from issuesmith.andon import note

    note(client, andon.id, "diagnosis_requested", andon.id)
    result = develop_diagnose.run(_ctx(tmp_path, number), StepConfig(template="diagnose.md"))
    assert result.status == "done"
    records = list_open_records(client)
    assert records[0]["notes"]["diagnosis"] == "edit_issue"
    reset_config_cache()
    sys.path.remove(str(tmp_path))
