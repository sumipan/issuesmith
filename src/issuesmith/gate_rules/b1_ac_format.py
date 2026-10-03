from __future__ import annotations

import re

import yaml
from ghdag.workflow.gates import GATE_REGISTRY, Violation

from issuesmith.ac_contract import normalize_reference_entry
from issuesmith.config import get_config

_ALLOWED_KEYS = frozenset({
    "paths_must_exist",
    "paths_must_not_exist",
    "references_must_resolve",
    # scope:migration contract keys (required by b1_migration, read by ac_contract / m2_gate)
    "post_merge",
    "removed_trees",
})


def get_ac_section(body: str) -> str | None:
    heading = get_config().sections["acceptance_criteria"]
    match = re.search(
        rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##|\Z)",
        body,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else None


def extract_yaml_block(section: str) -> str | None:
    match = re.search(r"^```yaml\n(.*?)\n```", section, re.DOTALL | re.MULTILINE)
    return match.group(1) if match else None


def _extract_new_file_paths_from_design(body: str) -> list[str]:
    """Return the paths of "new" rows in the change table inside the design section.

    The change-type cell (the cell right after the backticked path) is matched
    case-insensitively against the configured ``new_words`` vocabulary.
    """
    design = get_config().sections["design"]
    match = re.search(
        rf"^##\s+{re.escape(design)}\s*\n(.*?)(?=^##[^#]|\Z)",
        body,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return []
    design_section = match.group(1)
    new_words = get_config().scope_size.new_words
    paths: list[str] = []
    for line in design_section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        for i, cell in enumerate(cells[:-1]):
            path_match = re.fullmatch(r"`([^`]+\.[a-z]+)`", cell)
            if path_match is None:
                continue
            kind = cells[i + 1].lower()
            if any(word in kind for word in new_words):
                paths.append(path_match.group(1))
            break
    return paths


class B1AcFormatRules:
    def check(self, body: str, labels: list[str]) -> list[Violation]:
        is_structured = "scope:milestone" in labels or "scope:migration" in labels
        ac_heading = get_config().sections["acceptance_criteria"]
        section = get_ac_section(body)

        if is_structured:
            if section is None:
                return [Violation(
                    rule_id="b1_ac_format.section_missing",
                    severity="fail",
                    message=f"## {ac_heading} section is missing",
                    location=None,
                    auto_fixable=False,
                    fix_hint=None,
                )]
            yaml_text = extract_yaml_block(section)
            if yaml_text is None:
                new_paths = _extract_new_file_paths_from_design(body)
                if new_paths:
                    hint_lines = ["```yaml", "paths_must_exist:"]
                    hint_lines.extend(f"  - {p}" for p in new_paths)
                    hint_lines.append("```")
                    fix_hint = "\n".join(hint_lines)
                else:
                    changed = get_config().sections["changed_files"]
                    fix_hint = f"Derive paths_must_exist from the {changed} table"
                return [Violation(
                    rule_id="b1_ac_format.yaml_block_missing",
                    severity="fail",
                    message=f"## {ac_heading} section is missing a ```yaml block",
                    location=None,
                    auto_fixable=True,
                    fix_hint=fix_hint,
                )]
        else:
            if section is None:
                return []
            yaml_text = extract_yaml_block(section)
            if yaml_text is None:
                return []

        try:
            data = yaml.safe_load(yaml_text)
        except yaml.YAMLError:
            return [Violation(
                rule_id="b1_ac_format.yaml_invalid",
                severity="fail",
                message="failed to parse the YAML block",
                location=None,
                auto_fixable=False,
                fix_hint=None,
            )]

        if not isinstance(data, dict):
            return [Violation(
                rule_id="b1_ac_format.yaml_invalid",
                severity="fail",
                message="the YAML block must be a mapping",
                location=None,
                auto_fixable=False,
                fix_hint=None,
            )]

        unknown_keys = set(data.keys()) - _ALLOWED_KEYS
        if unknown_keys:
            return [Violation(
                rule_id="b1_ac_format.yaml_invalid",
                severity="fail",
                message=f"the YAML block contains disallowed keys: {sorted(unknown_keys)}",
                location=None,
                auto_fixable=False,
                fix_hint=None,
            )]

        violations: list[Violation] = []
        refs = data.get("references_must_resolve")
        if refs is not None:
            if not isinstance(refs, list):
                violations.append(Violation(
                    rule_id="b1_ac_format.reference_entry_invalid",
                    severity="fail",
                    message="references_must_resolve must be a list",
                    location="references_must_resolve",
                    auto_fixable=False,
                    fix_hint=None,
                ))
            else:
                _SYMBOL_HINT = (
                    "`path::symbol` is not supported. "
                    "Use a file path or `{file, key_path}` only; "
                    "verify symbol existence via checked AC and tests."
                )
                for i, entry in enumerate(refs):
                    _, _, error_kind = normalize_reference_entry(entry)
                    if error_kind == "symbol_form":
                        violations.append(Violation(
                            rule_id="b1_ac_format.reference_symbol_form",
                            severity="fail",
                            message=f"references_must_resolve[{i}] contains a `path::symbol` form",
                            location=f"references_must_resolve[{i}]",
                            auto_fixable=True,
                            fix_hint=_SYMBOL_HINT,
                        ))
                    elif error_kind is not None:
                        violations.append(Violation(
                            rule_id="b1_ac_format.reference_entry_invalid",
                            severity="fail",
                            message=(
                                f"references_must_resolve[{i}] has an invalid form"
                                " (str or {{file, key_path}} required)"
                            ),
                            location=f"references_must_resolve[{i}]",
                            auto_fixable=False,
                            fix_hint=None,
                        ))
        return violations


GATE_REGISTRY["b1_ac_format"] = B1AcFormatRules
