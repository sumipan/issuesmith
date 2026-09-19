```yaml
target_repo: sumipan/issuesmith
base_branch: main
allow_paths:
  - "src/issuesmith/**"
  - "tests/**"
  - "issuesmith.yaml"
```

<!-- Japanese text intentionally kept for CJK processing test -->
## Background

issuesmith c3092_c300Cghdag c4E0A_c306E_c6C4E_c7528_c30EF_c30FC_c30AF_c30D5_c30ED_c30FC_c57FA_c76E4_c300D_c306B_c3059_c308B_c305F_c3081_c3001_c30D1_c30C3_c30B1_c30FC_c30B8_c306B_c713C_c304D_c8FBC_c307E_c308C_c305F nexus c56FA_c6709_c30DD_c30EA_c30B7_c30FC_c3092_c8A2D_c5B9A_c3078_c5916_c51FA_c3057_c3059_c308B_c3002

## Design

### c65B9_c91DD

- 4 Sub_c306F_c540C_c3058 `src/issuesmith/**` c3092_c89E6_c308B_c305F_c3081_c76F4_c5217_c3002
- c5404_Sub_c3067 `config.py` c306B_c30AD_c30FC_c3092_c8FFD_c52A0_c3057_c3001_c6D88_c8CBB_c5074_c306F_c3059_c3079_c3066 `get_config()` c7D4C_c7531_c3067_c8AAD_c3080_c3002

### Sub-issue Plan

| # | Title | c5185_c5BB9 | Dependency |
|---|--------|------|------|
| 1 | c30D5_c30A7_c30FC_c30BA_c5B9A_c7FA9_c3092_c5916_c51FA_c3057 | phases c8A2D_c5B9A_c5316 | None |
| 2 | c30BB_c30AF_c30B7_c30E7_c30F3_c540D_c3092_c5916_c51FA_c3057 | sections c8A2D_c5B9A_c5316 | 1 |
| 3 | nexus c914D_c7F6E_c3092_c5916_c3059 | supported_repos c7A7A_c65E2_c5B9A | 2 |
| 4 | c30B9_c30C6_c30C3_c30D7_c5B9F_c88C5_c3092_c5916_c51FA_c3057 | steps c8A2D_c5B9A_c5316 | 3 |

#### Sub 1: c30D5_c30A7_c30FC_c30BA_c5B9A_c7FA9_c3092 phases c8A2D_c5B9A_c306B_c5916_c51FA_c3057_c3059_c308B

**Scope**: PhaseConfig c3092_c5B9A_c7FA9_c3057 queue / recovery / milestone c304C config c304B_c3089_c5C0E_c51FA_c3059_c308B
**Design Policy**: c65E2_c5B9A_c306F_c73FE_c884C 4 c30D5_c30A7_c30FC_c30BA
**Changed Files**:
| Repository | File Path | Change Type | Description |
|---|---|---|---|
| `sumipan/issuesmith` | `src/issuesmith/config.py` | Modify | PhaseConfig c8FFD_c52A0 |

**Acceptance Criteria**:
- [ ] phases c672A_c8A2D_c5B9A_c3067_c73FE_c884C_c52D5_c4F5C
- [ ] c30AB_c30B9_c30BF_c30E0 phases c3067_c53CD_c6620
- [ ] 3 c30A8_c30F3_c30B8_c30F3_c3067 engine resolve c4E0D_c5909

<!-- Japanese text intentionally kept for CJK processing test -->
#### Sub 2: c30BB_c30AF_c30B7_c30E7_c30F3_c540D_c3092 sections c8A2D_c5B9A_c306B_c5916_c51FA_c3057_c3059_c308B

**Scope**: sections / sub_design_subsections c3092 config c5316
**Design Policy**: c65E2_c5B9A_c306F_c73FE_c884C_c65E5_c672C_c8A9E_c898B_c51FA_c3057
**Changed Files**:
| Repository | File Path | Change Type | Description |
|---|---|---|---|
| `sumipan/issuesmith` | `src/issuesmith/config.py` | Modify | sections c8FFD_c52A0 |

**Acceptance Criteria**:
- [ ] sections c672A_c8A2D_c5B9A_c3067_c73FE_c884C_c52D5_c4F5C
- [ ] c30AB_c30B9_c30BF_c30E0_c898B_c51FA_c3057_c3067_c30B2_c30FC_c30C8_c304C_c8FFD_c5F93
- [ ] re.escape c9069_c7528_c3092_c78BA_c8A8D

#### Sub 3: c65E2_c5B9A_c5024_c304B_c3089 nexus c914D_c7F6E_c3092_c5916_c3059

**Scope**: supported_repos c65E2_c5B9A_c3092_c7A7A_c306B_c3057 repo c5FC5_c9808_c5316
**Design Policy**: get_config c9045_c5EF6_c8A55_c4FA1_c3067_c30D0_c30EA_c30C7_c30FC_c30B7_c30E7_c30F3
**Changed Files**:
| Repository | File Path | Change Type | Description |
|---|---|---|---|
| `sumipan/issuesmith` | `src/issuesmith/config.py` | Modify | supported_repos c7A7A_c65E2_c5B9A |

**Acceptance Criteria**:
- [ ] repo c672A_c8A2D_c5B9A_c3067_c660E_c793A_c30A8_c30E9_c30FC
- [ ] config show c304C JSON c3092_c8FD4_c3059
- [ ] nexus issuesmith.yaml c306F_c65E2_c5B58 repo c3067_c901A_c308B

<!-- Japanese text intentionally kept for CJK processing test -->
#### Sub 4: c30B9_c30C6_c30C3_c30D7_c5B9F_c88C5_c3092 steps c8A2D_c5B9A_c306B_c5916_c51FA_c3057_c3059_c308B

**Scope**: StepConfig c3068 steps c30AD_c30FC
**Design Policy**: dispatch c306F config c304B_c3089_c5C0E_c51FA
**Changed Files**:
| Repository | File Path | Change Type | Description |
|---|---|---|---|
| `sumipan/issuesmith` | `src/issuesmith/ops/dispatch.py` | Modify | steps c5C0E_c51FA |

**Acceptance Criteria**:
- [ ] steps c672A_c8A2D_c5B9A_c3067_c73FE_c884C_c52D5_c4F5C
- [ ] c30AB_c30B9_c30BF_c30E0 step c3067_c30E2_c30B8_c30E5_c30FC_c30EB_c89E3_c6C7A
- [ ] m2 compaction c30C6_c30F3_c30D7_c30EC_c540D_c304C_c8A2D_c5B9A_c5316

## Out of Scope

- skills/system-issuesmith c306E_c624B_c9806_Change
