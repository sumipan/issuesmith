# issuesmith — queue / engine 運用メモ

本ドキュメントは issuesmith パッケージ内のキュー投入とエンジン待機の仕様を記す。
nexus 側の総合ドキュメントは `docs/ISSUESMITH.md`（nexus リポジトリ）を参照。

## queue: ロール別 paused 判定（#3091）

`dispatch_one()` / `queue tick` は、投入候補を **フェーズのロール** ごとに
`QuotaGate` の paused 状態を見る。

| フェーズ | ロール（`issuesmith.yaml` / config） | 見る engine（現状の典型） |
|---------|--------------------------------------|---------------------------|
| `draft` | `design` | claude |
| `sub` | `implementation` | cursor |
| `develop` | `implementation` | cursor |
| `merge` | `implementation` | cursor |

- design engine だけが paused でも、implementation ロールの `develop` / `merge` / `sub` は投入できる。
- 当該ロールの engine が paused の候補はスキップし、理由は
  `required engine paused: <engine> (role=<role>)` を返す。
- `queue status` も同じ文言で待機理由を表示する。

## engine: design ロールの sleep & retry（#3091）

develop DAG 内の CP2（design ロール）は、design 用 engine が全 paused でも即 REJECT しない。

| 環境変数 | 既定 | 意味 |
|---------|------|------|
| `ISSUESMITH_ENGINE_WAIT_INTERVAL_SEC` | `3600`（1h） | `resume_at` が null（budget brake 等）のときの再試行間隔 |
| `ISSUESMITH_ENGINE_WAIT_MAX_SEC` | `21600`（6h） | 合計待機の上限。超えたら従来どおり `RuntimeError`（REJECT） |

待機中は 1 回あたり最大 `_RETRY_WAIT_MAX_SECONDS`（1800s）ずつ sleep し、
`call_managed` の `timeout` には待機予測分を加算する（外側の DAG task timeout 食い潰し緩和）。
