from __future__ import annotations

import re

import yaml
from ghdag.workflow.gates import GATE_REGISTRY, Violation

from issuesmith.ac_contract import (
    KNOWN_POST_MERGE_KINDS,
    MANUAL_CHECK_DESCRIPTION_ERROR,
    POST_MERGE_REQUIRED_FIELDS,
    manual_check_description,
)
from issuesmith.config import get_config
from issuesmith.gate_rules.b1_ac_format import extract_yaml_block, get_ac_section


def _migration_procedure_skeleton() -> str:
    heading = get_config().sections["migration"]
    return f"""\
## {heading}

（MG1 が実行するコマンドをここに記述する）

```bash
# 例: /var/tmp/mltgnt を main 最新に更新
cd /var/tmp/mltgnt && git fetch origin && git checkout main && git pull origin main
pip install -e "/var/tmp/mltgnt/[dev]" --no-deps

# マージ済みファイルの存在確認
test -f <対象ファイル> && echo "OK: file exists"
```
"""


def _state_survey_skeleton() -> str:
    heading = get_config().sections["migration_state_survey"]
    return f"""\
### {heading}

- **永続 state ファイル**: （移行対象コードが読み書きする logs/ ・.pipeline-state/ 等のファイルを列挙。無ければ（該当なし））
- **untracked 実データ**: （git 管理外に存在する旧版の実データ。無ければ（該当なし））
- **データ間の不変条件**: （state・snapshot・hash 等が満たすべき整合条件。無ければ（該当なし））
- **途中停止時の復旧**: （書き込み途中でプロセスが停止した場合に不整合から自己復旧できるか。atomic write の有無）
"""


_POST_MERGE_SKELETON = """\
post_merge:
  - kind: stable_install
    repo: sumipan/<repo>
    path: /var/tmp/<repo>
  - kind: tag
    repo: sumipan/<repo>
    tag: v0.1.0
  - kind: restart
    processes: [release_watcher, mltgnt_daemon]
  - kind: manual_check
    description: "run python3 scripts/preflight.py after MG1; no WARN is reported"
"""

_REMOVED_TREES_SKELETON = """\
removed_trees:
  - tools/<package>
"""


def has_migration_procedure_section(body: str) -> bool:
    heading = get_config().sections["migration"]
    return re.search(
        rf"^##\s+{re.escape(heading)}\s*(?:\n|$)",
        body,
        re.MULTILINE,
    ) is not None


def get_state_survey_section(body: str) -> str | None:
    """実行時状態調査サブセクションの中身を返す（無ければ None）。"""
    heading = get_config().sections["migration_state_survey"]
    match = re.search(
        rf"^###\s+{re.escape(heading)}\s*\n(.*?)(?=^#{{1,3}}\s|\Z)",
        body,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else None


def ac_contract_has_test_path(body: str) -> bool:
    """AC YAML の paths_must_exist に tests/ 配下のパスが含まれるか。"""
    section = get_ac_section(body)
    if section is None:
        return False
    yaml_text = extract_yaml_block(section)
    if yaml_text is None:
        return False
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return False
    if not isinstance(data, dict):
        return False
    paths = data.get("paths_must_exist")
    if not isinstance(paths, list):
        return False
    return any(
        isinstance(p, str) and re.match(r"(tools/[^/]+/)?tests?/", p) for p in paths
    )


def _ac_contract_yaml(body: str) -> dict | None:
    section = get_ac_section(body)
    if section is None:
        return None
    yaml_text = extract_yaml_block(section)
    if yaml_text is None:
        return None
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def ac_contract_has_key(body: str, key: str) -> bool:
    data = _ac_contract_yaml(body)
    return isinstance(data, dict) and key in data


# Same order as POST_MERGE_REQUIRED_FIELDS (stable_install, tag, restart, manual_check)
_ALLOWED_KINDS_TEXT = ", ".join(POST_MERGE_REQUIRED_FIELDS)


def post_merge_schema_errors(post_merge: object) -> list[str]:
    """Return the problems that would make M2 reject post_merge (empty when valid)."""
    if not isinstance(post_merge, list):
        return ["post_merge must be a list"]
    errors: list[str] = []
    for i, item in enumerate(post_merge):
        if not isinstance(item, dict):
            errors.append(f"post_merge[{i}]: each post_merge item must be a dict")
            continue
        kind = item.get("kind")
        if not isinstance(kind, str) or kind not in KNOWN_POST_MERGE_KINDS:
            errors.append(
                f"post_merge[{i}]: unknown kind: {kind}; allowed: {_ALLOWED_KINDS_TEXT}"
            )
            continue
        missing = [f for f in POST_MERGE_REQUIRED_FIELDS[kind] if f not in item]
        for field in missing:
            errors.append(
                f"post_merge[{i}]: kind {kind}: missing required field: {field}"
            )
        if kind == "manual_check" and not missing and manual_check_description(item) is None:
            errors.append(f"post_merge[{i}]: {MANUAL_CHECK_DESCRIPTION_ERROR}")
    return errors


def removed_trees_schema_errors(removed_trees: object) -> list[str]:
    """Return the problems with removed_trees items (empty when all are strings)."""
    if not isinstance(removed_trees, list):
        return [f"removed_trees must be a list, got {type(removed_trees).__name__}"]
    return [
        f"removed_trees[{i}]: removed_trees items must be strings, "
        f"got {type(item).__name__}"
        for i, item in enumerate(removed_trees)
        if not isinstance(item, str)
    ]


class B1MigrationRules:
    def check(self, body: str, labels: list[str]) -> list[Violation]:
        if "scope:migration" not in labels:
            return []

        violations: list[Violation] = []
        sections = get_config().sections
        migration = sections["migration"]
        survey = sections["migration_state_survey"]
        ac = sections["acceptance_criteria"]

        if not has_migration_procedure_section(body):
            violations.append(Violation(
                rule_id="b1_migration.migration_procedure_missing",
                severity="fail",
                message=f"## {migration} セクションが存在しません",
                location=None,
                auto_fixable=True,
                fix_hint=_migration_procedure_skeleton(),
            ))

        survey_body = get_state_survey_section(body)
        if survey_body is None or not survey_body.strip():
            violations.append(Violation(
                rule_id="b1_migration.state_survey_missing",
                severity="fail",
                message=(
                    f"### {survey} サブセクションが存在しません"
                    "（永続 state・untracked 実データ・不変条件・途中停止時の復旧を"
                    "調査し、該当なしの場合もその旨を明記すること）"
                ),
                location=None,
                auto_fixable=True,
                fix_hint=_state_survey_skeleton(),
            ))

        if not ac_contract_has_test_path(body):
            violations.append(Violation(
                rule_id="b1_migration.verification_test_missing",
                severity="fail",
                message=(
                    f"## {ac} の ```yaml ブロックの paths_must_exist に"
                    " tests/ 配下の移行検証テストが含まれていません"
                    "（旧フォーマット fixture を使うテストをコミットし、"
                    "そのパスを paths_must_exist に列挙すること）"
                ),
                location=None,
                auto_fixable=True,
                fix_hint=(
                    "```yaml\npaths_must_exist:\n"
                    "  - tests/<領域>/test_<対象>_migration.py\n```"
                ),
            ))

        if not ac_contract_has_key(body, "post_merge"):
            violations.append(Violation(
                rule_id="b1_migration.post_merge_missing",
                severity="fail",
                message=(
                    f"## {ac} の ```yaml ブロックに post_merge が含まれていません"
                    "（マージ後の stable install・tag 発行・プロセス再起動を列挙すること）"
                ),
                location=None,
                auto_fixable=True,
                fix_hint=_POST_MERGE_SKELETON,
            ))

        if not ac_contract_has_key(body, "removed_trees"):
            violations.append(Violation(
                rule_id="b1_migration.removed_trees_missing",
                severity="fail",
                message=(
                    f"## {ac} の ```yaml ブロックに removed_trees が含まれていません"
                    "（git 追跡下から削除すべきディレクトリプレフィックスを列挙すること）"
                ),
                location=None,
                auto_fixable=True,
                fix_hint=_REMOVED_TREES_SKELETON,
            ))

        contract = _ac_contract_yaml(body) or {}

        if "post_merge" in contract:
            errors = post_merge_schema_errors(contract["post_merge"])
            if errors:
                violations.append(Violation(
                    rule_id="b1_migration.post_merge_schema",
                    severity="fail",
                    message=(
                        f"## {ac} yaml block: post_merge is not in the form M2 accepts"
                        f" (allowed kinds: {_ALLOWED_KINDS_TEXT}): " + "; ".join(errors)
                    ),
                    location=None,
                    auto_fixable=False,
                    fix_hint=_POST_MERGE_SKELETON,
                ))

        if "removed_trees" in contract:
            errors = removed_trees_schema_errors(contract["removed_trees"])
            if errors:
                violations.append(Violation(
                    rule_id="b1_migration.removed_trees_schema",
                    severity="fail",
                    message=(
                        f"## {ac} yaml block: removed_trees is malformed: "
                        + "; ".join(errors)
                    ),
                    location=None,
                    auto_fixable=False,
                    fix_hint="removed_trees:\n  - tools/<package>",
                ))

        return violations


GATE_REGISTRY["b1_migration"] = B1MigrationRules
