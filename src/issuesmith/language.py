"""Language packs: host-facing vocabulary and GitHub-posted text (nexus #4469).

A :class:`LanguagePack` holds two kinds of strings:

* the vocabulary issuesmith uses to read the host's Issue bodies (section
  headings, sub-design header prefix, table columns, change-kind words, ...)
* the text issuesmith posts to GitHub (Issue comments, andon summaries and
  option descriptions), keyed ``<module>.<id>`` in :attr:`LanguagePack.messages`

The package ships the English pack :data:`EN` only. A host that writes Issues in
another language points ``language_pack:`` in ``issuesmith.yaml`` at a YAML file
loaded by :func:`load_language_pack`. Logs, exception messages, CLI output and
Violation ``message`` / ``fix_hint`` stay English literals and are not part of a pack.

This module must stay import-light: it is imported by ``issuesmith.config``.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


@dataclass(frozen=True)
class LanguagePack:
    """Vocabulary and GitHub-posted text of one host language."""

    # --- Issue body vocabulary -------------------------------------------------
    # H2 section headings, keyed by role (acceptance_criteria, design, ...).
    sections: Mapping[str, str]
    # Bold-label subsections every ``#### <sub_header_prefix>N:`` block must have:
    # (scope, design policy, changed files, acceptance criteria).
    sub_design_subsections: tuple[str, ...]
    # ``#### <prefix>N:`` marks one sub design.
    sub_header_prefix: str
    # Sub-issue plan table: (#, title, target repo, content, depends on).
    sub_plan_columns: tuple[str, ...]
    # Change table: (repository, file path, change type, description).
    change_table_columns: tuple[str, ...]
    # Depends-on cell value meaning "no dependency".
    no_deps_word: str
    # Change-type words (matched case-insensitively as substrings).
    delete_words: tuple[str, ...]
    new_words: tuple[str, ...]
    # Verbs in scope text that announce a removal (scope_coupling).
    removal_words: tuple[str, ...]
    # Placeholder words that mark an unfilled cell (milestone V3).
    placeholder_words: tuple[str, ...]
    # Words that make an acceptance criterion too vague to verify.
    vague_ac_words: tuple[str, ...]
    # Phrase a child Issue body uses to name its parent ("derived from #N").
    derived_from_phrase: str
    # Label in front of the parent Issue reference in a child body.
    parent_issue_label: str
    # Header row of the dependency table SUB1 writes into a child body.
    dependencies_table_header: str
    # H2 heading of the out-of-scope section.
    out_of_scope_heading: str

    # --- GitHub-posted text ----------------------------------------------------
    # ``<module>.<id>`` -> ``str.format`` template with keyword placeholders.
    messages: Mapping[str, str]

    @property
    def sub_plan_header(self) -> str:
        """Sub-issue plan table header row built from :attr:`sub_plan_columns`."""
        return "| " + " | ".join(self.sub_plan_columns) + " |"

    def message(self, key: str, /, **kwargs: Any) -> str:
        """Render ``messages[key]`` with ``str.format(**kwargs)``."""
        return self.messages[key].format(**kwargs)


_SECTION_KEYS: tuple[str, ...] = (
    "acceptance_criteria",
    "migration",
    "migration_state_survey",
    "sub_plan",
    "design",
    "background",
    "dependencies",
    "impact_survey",
    "milestone",
    "changed_files",
)

# Fixed tuple lengths (index positions are part of the contract).
_TUPLE_LENGTHS: dict[str, int] = {
    "sub_design_subsections": 4,
    "sub_plan_columns": 5,
    "change_table_columns": 4,
}

# GitHub-posted text, one entry per call site (keys ``<module>.<id>``). Machine
# markers (HTML comments, ``PIPELINE_STATUS:`` lines, ``Refs #N``) are not part of
# a template: callers append them so hosts cannot break parsers by translating them.
_EN_MESSAGES: dict[str, str] = {
    # andon
    "andon.widen_failed": (
        "Cannot widen allow_paths: no yaml metadata block found in Issue body.\n"
        "Requested files: {files}"
    ),
    "andon.answered": "andon `{andon_id}` answered: **{action}**",
    # engine
    "engine.pre_llm_gate_violation": "pre-LLM gate violation in step {step}: {messages}",
    # ops/dispatch
    "dispatch.andon_fallback": "step {step} raised andon {kind}",
    "dispatch.gate_build_failed": "gate could not be built in step {step}: {error}",
    "dispatch.gate_exception": "requires gate raised exception in step {step}: {error}",
    "dispatch.repair_oscillation": (
        "repair oscillation detected in step {step} after {count} repair(s): {rule_ids}"
    ),
    "dispatch.non_repairable": "non-repairable gate violation in step {step}: {messages}",
    "dispatch.repairs_exhausted": (
        "requires evaluation failed after {count} repair(s) in step {step}: {messages}"
    ),
    "dispatch.preexisting_violations": (
        "## requires: preexisting violations (non-blocking)\n\n{items}"
    ),
    "dispatch.preexisting_violation_item": "- `{rule_id}`: {message}",
    "dispatch.step_no_module": (
        "step {step} has no module; run it via engine run-guarded --requires-step {step}"
    ),
    "dispatch.repair_reentered": "repair step re-entered itself (ISSUESMITH_REPAIR_ACTIVE is set)",
    # ops/repair_step
    "repair_step.reentered": "repair step re-entered itself (ISSUESMITH_REPAIR_ACTIVE is set)",
    "repair_step.no_violations": "repair launched without violations",
    "repair_step.no_template": "repair step has no template configured",
    # observe/policy (andon summary / evidence)
    "policy.dag_terminated": "DAG {key} terminated at step {step}; running label removed",
    "policy.dag_terminated_evidence": "uuid={uuid} result={result_path}",
    "policy.issue_stall": "issue #{issue} stalled in {phase} for {minutes} minutes",
    "policy.orphan_exec": "orphan exec UUID {uuid} has no in_flight tracking",
    "policy.chain_halted": "milestone chain parent #{parent} is halted: {reason}",
    "policy.task_timeout": "task {uuid} timed out after {elapsed} minutes",
    "policy.systemic_failure": "systemic step failure: {step} {failure_class}",
    "policy.systemic_failure_evidence": "affected issues: {issues}",
    "policy.forge_unavailable": "forge API unavailable: {consecutive} consecutive errors",
    "policy.github_api_low": "GitHub API rate limit low: {remaining} remaining",
    "policy.github_api_recovered": "GitHub API rate limit recovered",
    "policy.main_red": "main is red at {sha}: {reason}",
    "policy.main_red_evidence": "failing: {failing}",
    "policy.version_skew": "version skew: {package} pinned={pinned} installed={installed}",
    # queue
    "queue.repair_limit": "{reason} (repair limit reached)",
    "queue.intake_repair": (
        "## Intake check: asked B1 to fix the Issue body\n\n{reason}\n\n"
        "The queue will re-run draft (B1) to fix the Issue body, then re-check this"
        " request after draft completes."
    ),
    "queue.deletion_refs_before_dispatch": (
        "## Gate: uncovered references to deleted files (before dispatch)\n\n{references}\n\n"
        "Dispatch stopped. Fix the Issue body; the next tick re-checks it."
    ),
    "queue.deletion_refs_lead_dispatch": "On the base at dispatch time, ",
    "queue.redispatch_ok": " ghdag generation bumped (redispatch).",
    "queue.redispatch_failed": (
        " WARNING: ghdag redispatch failed (rc={rc}); run"
        " `ghdag trigger {issue} --handler {handler} --redispatch` manually."
    ),
    "queue.dispatched": (
        "issuesmith queue dispatched `{label}` for request `{request_id}`.{note}"
    ),
    "queue.skipped": "issuesmith queue: skipped issue #{issue} from last_issue. reason: {reason}",
    "queue.dequeued": "issuesmith queue: dequeued request {request_id}. reason: {reason}",
    # queue_triage (decision reasons posted as comments)
    "queue_triage.closed": "issue #{issue} is CLOSED",
    "queue_triage.already_present": "{label} already present",
    "queue_triage.sub_requires_milestone": "sub phase requires scope:milestone label",
    "queue_triage.milestone_no_develop": (
        "scope:milestone issues cannot enter develop phase"
        " (P0 raises MILESTONE_BLOCKED; reject at queue intake)"
    ),
    "queue_triage.superseded": "superseded by #{issue} (v{version})",
    "queue_triage.missing_yaml": "missing YAML fields: {fields}",
    # gate_rules/scope_breadth (CP1 autofix note)
    "scope_breadth.autofix_note": (
        "## CP1: allow_paths narrowed automatically\n\n"
        "**Reason**: allow_paths exceeded the breadth gate"
        " (files={before_files}, lines={before_lines}). Narrowed deterministically from"
        " the changed-files table and the acceptance criteria `paths_must_exist`,"
        " then continued.\n\n"
        "- Before: {old}\n"
        "- After: {new} (files={after_files}, lines={after_lines})\n"
    ),
    "scope_breadth.none": "(none)",
    # gate_rules/scope_coupling
    "scope_coupling.autofix_note": (
        "## CP1: missing files added to allow_paths automatically\n\n"
        "**Reason**: allow_paths did not include files that must change along with this"
        " change. Added the deterministically detected missing files.\n\n"
        "- Added files: {added}\n"
        "- allow_paths file count after change: {count}\n"
    ),
    "scope_coupling.deletion_refs_item": "{lead}the following files reference deleted `{path}`:",
    "scope_coupling.deletion_refs_footer": "Add them to `allow_paths` or `paths_must_not_exist`.",
    # gates/dep
    "dep.deletion_refs_after_merge": (
        "## Gate warning: uncovered references to deleted files"
        " (re-check after dependency #{dep} merged)\n\n{references}"
    ),
    "dep.deletion_refs_lead": "After #{dep} merged, ",
    # scope_gate
    "scope_gate.preflight_contradiction": (
        "\n**Exceeded after passing the pre-gate = gate contradiction**: this Issue passed"
        " CP1 (`scope_breadth`) and reached P0. P0 is a safety net and should not fire"
        " here. The CP1 auto-narrowing logic may have a workflow defect.\n"
    ),
    "scope_gate.too_large": (
        "## P0 stopped: allow_paths scope is too large\n{contradiction_note}\n"
        "**Reason**: `{reason}`\n\n"
        "| Metric | Value |\n|---|---|\n"
        "| Files | {files} |\n"
        "| Lines (text) | {lines} |\n"
        "| Binary (excluded from lines) | {skipped_binary} |\n"
        "| `*.jsonl` (excluded from lines) | {skipped_jsonl} |\n\n"
        "### Files per directory (top 5)\n\n"
        "| Directory | Files |\n|---|---|\n{rows}\n\n"
        "### Split hint\n\n"
        "Split the Issue by directory (top entries above) or by feature, narrow each"
        " child Issue's `allow_paths`, then remove `issuesmith:scope-too-large` and add"
        " `issuesmith:develop-ready`.\n"
    ),
    "scope_gate.no_rows": "| (none) | 0 |",
    # steps/p0_worktree
    "p0_worktree.milestone_blocked": (
        "## P0 stopped: scope:milestone issue\n"
        "This issue is design-only. Automatic implementation via develop-ready is not allowed."
    ),
    "p0_worktree.stale_base": (
        "## P0 stopped: base_branch sync failed (rebase conflict)\n\n"
        "Rebasing onto `origin/{base_branch}` hit conflicts.\n"
        "Resolve the conflicts manually, then dispatch again.\n\n"
        "**Conflicting files:**\n```\n{conflict_files}\n```"
    ),
    # steps/m1_merge
    "m1_merge.companion_not_ready": (
        "## M1: companion PR not approved or CI not passing\n\n"
        "Re-run after companion PR (#{pr}, branch: {branch}-diary) is approved and CI passes."
    ),
    "m1_merge.m2_gate_blocked": (
        "## M1 stopped: M2 gate not passed\n\n"
        "The pre-merge M2 gate check found unresolved problems:\n\n{items}\n\n"
        "Check every acceptance criterion, then re-run."
    ),
    # steps/m2_finalize
    "m2_finalize.migrate": (
        "## M2: acceptance criteria incomplete\n\n"
        "Unchecked acceptance criteria remain. Add `issuesmith:migrate-ready` after the"
        " migration completes."
    ),
    "m2_finalize.contract_failed_detail": (
        "Acceptance criteria YAML contract check failed:\n{failures}"
    ),
    "m2_finalize.contract_failed_step1": (
        "1. Make the files listed in `paths_must_exist` exist on {base_branch}"
        " (add the missing files or fix the contract)"
    ),
    "m2_finalize.unchecked_detail": "Unchecked acceptance criteria remain.",
    "m2_finalize.unchecked_step1": "1. Check every acceptance criterion",
    "m2_finalize.retry_impl": (
        "## M2: acceptance criteria incomplete\n\n{detail}\n\n"
        "Recovery (impl context):\n{step1}\n"
        "2. Run `python3 -m issuesmith queue enqueue --issue {issue} --phase merge"
        " --source recovery --actor-kind human --priority high --requested-by <login> --force`\n"
        "   Note: the impl idempotency key is consumed but the merge key is unused,"
        " so no reset is needed"
    ),
    "m2_finalize.retry": (
        "## M2: acceptance criteria incomplete\n\n{detail}\n\n"
        "Recovery:\n{step1}\n"
        "2. Add the `issuesmith:reset` label to reset\n"
        "3. Wait for ghdag to process the reset on its next cycle (about 30s)\n"
        "4. Re-enqueue with `python3 -m issuesmith queue enqueue --issue {issue} --phase merge"
        " --source recovery --actor-kind human --priority high --requested-by <login> --force`"
    ),
    "m2_finalize.compaction_failed": (
        "## M2 compaction failed\n\n"
        "The compaction LLM step failed with exit code {rc}"
        " (possibly a provider refusal or timeout).\n"
        "The PR merge is already complete. Compact the source document manually.\n"
        "Target: `{source}`"
    ),
    # ops/publish (PR title / body; callers append the ``Refs`` line)
    "publish.pr_title_cross_repo": "Implement {issue_repo}#{issue}",
    "publish.pr_title": "Implement Issue #{issue}",
    "publish.pr_body": "Auto-generated from the P1/P2 result.",
    # milestone (parent comments and SUB1 results)
    "milestone.sub1_no_milestone_no_link": (
        "## SUB1 error: no milestone and sub-issue link failed\n\n"
        "The parent Issue has no milestone and linking child #{child} as a sub-issue also failed."
    ),
    "milestone.chain_no_children": "No child Issues were created. Check the SUB1 result.",
    "milestone.chain_validation_failed": "Child Issue validation failed.\n{items}",
    "milestone.chain_validation_item": "- #{issue}: {failures}",
    "milestone.chain_closed_without_merge": "Needs review: #{issue} closed without {label}",
    "milestone.chain_all_done_closed": "All sub-issues done ({children})",
    "milestone.chain_all_done_manual": "All sub-issues done. A human closes the parent.",
    "milestone.sub1_dup_design": "## SUB1 error: duplicate `## {design}` section",
    "milestone.sub1_missing_design_ac": (
        "## SUB1 error: design or acceptance criteria missing\n\n"
        "The parent issue needs both `## {design}` and `## {acceptance_criteria}`."
    ),
    "milestone.sub1_no_sub_blocks": (
        "## SUB1 error: sub-issue designs not generated (B1 may be incomplete)\n\n"
        "The parent's `## {design}` has no `#### {sub_prefix}N` subsections."
    ),
    "milestone.sub1_not_milestone": (
        "## SUB1 error: no scope:milestone\n\n"
        "`issuesmith:sub-ready` can only be used on Issues labeled `scope:milestone`."
    ),
    "milestone.sub1_cp1_not_ready": (
        "## SUB1 error: CP1 not finished\n\n"
        "The CP1 gate after the B1 brush-up has not completed yet."
    ),
    "milestone.sub1_cp1_blocked": (
        "## SUB1 error: CP1 not passed\n\n"
        "The parent Issue's CP1 gate is FAIL (not an intentional_hold)."
    ),
    "milestone.sub1_milestone_created": (
        "## SUB1: milestone created automatically\n\n"
        "The parent Issue had no milestone, so `{title}` (#{number}) was created and attached.\n"
    ),
    "milestone.sub1_milestone_create_failed": (
        "## SUB1 warning: automatic milestone creation failed\n\n"
        "The parent Issue has no milestone and automatic creation failed ({error})."
        " Continuing with sub-issue links.\n"
    ),
    "milestone.sub1_no_plan": "## SUB1 error: sub-issue plan not found",
    "milestone.sub1_no_target_repo": (
        "## SUB1 error: parent YAML has no target_repo\n\n"
        "The YAML block of parent Issue #{issue} needs a `target_repo` field."
    ),
    "milestone.sub1_row_repo_empty": (
        "{sub_prefix}{row}: target repo is empty in the 5-column table"
    ),
    "milestone.sub1_existing_link_failed": (
        "## SUB1 error: failed to link existing child #{child} as a sub-issue"
    ),
    "milestone.sub1_existing_skipped": "Existing: #{child} / {title} (skipped)",
    "milestone.sub1_no_row_paths": (
        "{sub_prefix}{row}: no {repo} paths could be extracted from the changed-files table,"
        " so no child is created (parent allow_paths are not inherited)"
    ),
    "milestone.sub1_cp1_forbidden": (
        "## SUB1 error: CP1 forbidden words remain in {sub_prefix}{row} body\n\n{detail}\n"
    ),
    "milestone.sub1_cp1_forbidden_item": "{sub_prefix}{row}: CP1 forbidden words",
    "milestone.sub1_prefail_item": "{sub_prefix}{row}:\n{failures}",
    "milestone.sub1_create_failed": (
        "## SUB1 error: Issue creation failed ({sub_prefix}{row})\n\n{error}"
    ),
    "milestone.sub1_new_link_failed": "## SUB1 error: failed to link child #{child} as a sub-issue",
    "milestone.sub1_created_item": "#{child} / {title} / {repo}",
    "milestone.sub1_all_prevalidation_failed": (
        "## SUB1 error: child Issue body validation failed\n\n"
        "No Issues were created because pre-creation validation (V1-V5) failed for these"
        " rows:\n\n{failures}\n"
    ),
    "milestone.sub1_partial_validation": (
        "## SUB1 warning: validation failed for some rows\n\n{failures}\n"
    ),
    "milestone.sub1_post_validate_failed": (
        "## SUB1 error: validate_children failed after creation\n\n{details}"
    ),
    "milestone.sub1_done": (
        "## SUB1 sub-issues created\n\nCreated sub-issues:\n{created}\n\n"
        "milestone: {milestone}\n{logs}\n"
        "The queue enqueues the validated child develop requests in dependency order.\n"
    ),
    "milestone.none_item": "- (none)",
    "milestone.no_milestone": "none (sub-issue links only)",
    "milestone.log_resolved": "Resolved plan ref: row {row} -> {target}",
    "milestone.log_unresolved_forward": "Unresolved forward ref: row {row}",
    "milestone.log_excluded_milestone": "Excluded scope:milestone dependency: #{issue}",
    "milestone.v2_missing_path": "V2: not covered by allow_paths ({path})",
    "milestone.v3_cjk_path": "V3: CJK placeholder ({path})",
    "milestone.v4_missing_dep_section": "V4: has dependencies but no ## {dependencies} section",
    "milestone.v5_dep_self": "V5: dependency points to the parent Issue itself (#{issue})",
    "milestone.v5_dep_milestone": "V5: dependency points to a scope:milestone Issue (#{issue})",
    # milestone (child Issue body written by SUB1)
    "milestone.child_from_parent_ac": "- [ ] (from parent)",
    "milestone.child_cross_repo_note": (
        "> Enqueue this child after the external repository release and the"
        " release-watcher bump Issue reach merge-done"
    ),
    "milestone.child_dependency_row": "| {index} | #{issue} ({title}) | {state} |",
}


EN = LanguagePack(
    sections=dict(
        {
            "acceptance_criteria": "Acceptance Criteria",
            "migration": "Migration Steps",
            "migration_state_survey": "Runtime State Survey",
            "sub_plan": "Sub-issue Plan",
            "design": "Design",
            "background": "Background",
            "dependencies": "Dependencies",
            "impact_survey": "Impact Survey",
            "milestone": "Milestone",
            "changed_files": "Changed Files",
        }
    ),
    sub_design_subsections=("Scope", "Design Policy", "Changed Files", "Acceptance Criteria"),
    sub_header_prefix="Sub",
    sub_plan_columns=("#", "Title", "Target repo", "Content", "Depends on"),
    change_table_columns=("Repository", "File path", "Change type", "Description"),
    no_deps_word="none",
    delete_words=("delete",),
    new_words=("new", "add"),
    removal_words=("delete", "remove", "replace", "substitute", "deprecate"),
    placeholder_words=("TBD", "TODO", "FIXME", "XXX", "placeholder"),
    vague_ac_words=("works correctly", "properly", "without problems", "as needed"),
    derived_from_phrase="derived",
    parent_issue_label="Parent issue",
    dependencies_table_header="| # | Dependency | State |",
    out_of_scope_heading="Out of Scope",
    messages=dict(_EN_MESSAGES),
)

FIELD_NAMES: tuple[str, ...] = tuple(f.name for f in dataclasses.fields(LanguagePack))


def _config_error(msg: str) -> Exception:
    # Imported lazily: issuesmith.config imports this module.
    from issuesmith.config import ConfigError

    return ConfigError(msg)


def _as_str(where: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _config_error(f"{where} must be a non-empty string")
    return value


def _as_str_tuple(where: str, value: Any) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(x, str) and x.strip() for x in value)
    ):
        raise _config_error(f"{where} must be a non-empty list of strings")
    return tuple(value)


def _as_str_map(where: str, value: Any, expected: tuple[str, ...]) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise _config_error(f"{where} must be a mapping of strings")
    keys = {str(k) for k in value}
    missing = sorted(set(expected) - keys)
    unknown = sorted(keys - set(expected))
    if missing or unknown:
        raise _config_error(f"{where}: missing keys {missing}, unknown keys {unknown}")
    return {str(k): _as_str(f"{where}.{k}", v) for k, v in value.items()}


def language_pack_from_mapping(data: Any, *, source: str = "language pack") -> LanguagePack:
    """Build a :class:`LanguagePack` from a parsed mapping, validating every field.

    Missing fields and mapping entries fall back to :data:`EN`.
    Unknown keys and invalid supplied values raise ``ConfigError``.
    """
    if not isinstance(data, Mapping):
        raise _config_error(f"{source} must be a mapping")
    defaults = {
        key: list(value) if isinstance(value, tuple) else value
        for key, value in dataclasses.asdict(EN).items()
    }
    merged = {**defaults, **data}
    for name in ("sections", "messages"):
        if isinstance(data.get(name), Mapping):
            merged[name] = {**defaults[name], **data[name]}
    data = merged
    keys = {str(k) for k in data}
    missing = sorted(set(FIELD_NAMES) - keys)
    unknown = sorted(keys - set(FIELD_NAMES))
    if missing or unknown:
        raise _config_error(f"{source}: missing keys {missing}, unknown keys {unknown}")
    values: dict[str, Any] = {}
    for f in dataclasses.fields(LanguagePack):
        raw = data[f.name]
        where = f"{source}: {f.name}"
        if f.name == "sections":
            values[f.name] = _as_str_map(where, raw, _SECTION_KEYS)
        elif f.name == "messages":
            values[f.name] = _as_str_map(where, raw, tuple(EN.messages))
        elif isinstance(getattr(EN, f.name), tuple):
            items = _as_str_tuple(where, raw)
            expected_len = _TUPLE_LENGTHS.get(f.name)
            if expected_len is not None and len(items) != expected_len:
                raise _config_error(f"{where} must have exactly {expected_len} entries")
            values[f.name] = items
        else:
            values[f.name] = _as_str(where, raw)
    return LanguagePack(**values)


def load_language_pack(path: str | Path) -> LanguagePack:
    """Load a language pack YAML file (keys are :class:`LanguagePack` field names)."""
    p = Path(path)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _config_error(f"cannot read language pack {p}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise _config_error(f"invalid YAML in language pack {p}: {exc}") from exc
    return language_pack_from_mapping(raw, source=f"language pack {p}")
