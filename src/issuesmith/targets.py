"""targets.py — multi-target Issue モデル（#2866）"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from issuesmith.ac_contract import extract_contract_from_body

ContractDict = dict[str, list[str]]


@dataclass(frozen=True)
class Target:
    repo: str
    base: str
    allow_paths: tuple[str, ...]
    contract: dict[str, list[str]]
    primary: bool


def _normalize_allow_paths(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,)
    return tuple(str(p) for p in raw)


def _empty_contract() -> ContractDict:
    return {}


def _parse_ac_contracts(body: str | None) -> tuple[dict[str, ContractDict] | None, ContractDict]:
    """受け入れ条件 YAML を旧形式契約と per-repo 契約に分割する。"""
    if not body:
        return None, _empty_contract()
    ac = extract_contract_from_body(body)
    if not ac:
        return None, _empty_contract()
    if "targets" not in ac:
        return None, {k: list(v) if isinstance(v, list) else v for k, v in ac.items()}

    raw_targets = ac["targets"]
    if not isinstance(raw_targets, list):
        raise ValueError("受け入れ条件の targets は list である必要があります")

    per_repo: dict[str, ContractDict] = {}
    for item in raw_targets:
        if not isinstance(item, dict):
            raise ValueError("受け入れ条件の targets 各要素は dict である必要があります")
        repo = item.get("repo")
        if not repo:
            raise ValueError("受け入れ条件の targets 各要素に repo が必要です")
        contract = item.get("contract", {})
        if not isinstance(contract, dict):
            raise ValueError("contract は dict である必要があります")
        per_repo[str(repo)] = {
            k: list(v) if isinstance(v, list) else v for k, v in contract.items()
        }

    legacy = {
        k: list(v) if isinstance(v, list) else v
        for k, v in ac.items()
        if k != "targets"
    }
    return per_repo, legacy


def _contract_for_repo(
    repo: str,
    per_repo: dict[str, ContractDict] | None,
    legacy: ContractDict,
    *,
    primary: bool,
) -> ContractDict:
    if per_repo is not None:
        return dict(per_repo.get(repo, _empty_contract()))
    if primary:
        return dict(legacy)
    return _empty_contract()


def _targets_from_metadata_list(
    raw_targets: list[Any],
    *,
    base: str,
    per_repo: dict[str, ContractDict] | None,
    legacy: ContractDict,
) -> list[Target]:
    if not raw_targets:
        raise ValueError("metadata targets が空です")

    built: list[Target] = []
    primary_count = 0
    for item in raw_targets:
        if not isinstance(item, dict):
            raise ValueError("metadata targets 各要素は dict である必要があります")
        repo = str(item.get("repo", ""))
        if not repo:
            raise ValueError("metadata targets 各要素に repo が必要です")
        primary = bool(item.get("primary", False))
        if primary:
            primary_count += 1
        target_base = str(item.get("base", base))
        allow_paths = _normalize_allow_paths(item.get("allow_paths", ()))
        built.append(
            Target(
                repo=repo,
                base=target_base,
                allow_paths=allow_paths,
                contract=_contract_for_repo(
                    repo, per_repo, legacy, primary=primary
                ),
                primary=primary,
            )
        )

    if primary_count != 1:
        raise ValueError(
            f"primary=True のターゲットは 1 つである必要があります (got {primary_count})"
        )

    built.sort(key=lambda t: (not t.primary, t.repo))
    return built


def targets_from_issue(
    body_metadata: dict,
    *,
    issue_repo: str,
    body: str | None = None,
) -> list[Target]:
    """Issue body の YAML メタデータから Target リストを生成する。

    primary=True が先頭、secondary が後続。len >= 1。
    primary=True は常に 1 つ。2 つ以上なら ValueError。
    """
    base = str(body_metadata.get("base_branch", "main"))
    per_repo, legacy = _parse_ac_contracts(body)

    if "targets" in body_metadata:
        raw = body_metadata["targets"]
        if not isinstance(raw, list):
            raise ValueError("metadata targets は list である必要があります")
        return _targets_from_metadata_list(
            raw, base=base, per_repo=per_repo, legacy=legacy
        )

    target_repo = str(body_metadata.get("target_repo", "") or "")
    if target_repo and target_repo == issue_repo:
        target_repo = ""

    allow_paths = _normalize_allow_paths(body_metadata.get("allow_paths", []))
    diary_allow_paths = _normalize_allow_paths(
        body_metadata.get("diary_allow_paths", [])
    )

    if not target_repo:
        return [
            Target(
                repo=issue_repo,
                base=base,
                allow_paths=allow_paths,
                contract=_contract_for_repo(
                    issue_repo, per_repo, legacy, primary=True
                ),
                primary=True,
            )
        ]

    primary = Target(
        repo=target_repo,
        base=base,
        allow_paths=allow_paths,
        contract=_contract_for_repo(target_repo, per_repo, legacy, primary=True),
        primary=True,
    )

    if not diary_allow_paths:
        return [primary]

    secondary = Target(
        repo=issue_repo,
        base=base,
        allow_paths=diary_allow_paths,
        contract=_contract_for_repo(issue_repo, per_repo, legacy, primary=False),
        primary=False,
    )
    return [primary, secondary]
