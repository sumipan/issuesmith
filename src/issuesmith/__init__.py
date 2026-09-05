"""
issuesmith — ghdag ワークフロー用ツール

主要モジュール:
    context_hook    — ghdag context_hook (impl/merge ハンドラー用コンテキスト生成)
    engine          — LLM role switcher / runner
    ops             — dispatch / publish / preflight 等の運用コマンド
    cli             — 統一 CLI（python3 -m issuesmith）

stash 設計書ツール（apply / ingest_review）は tools/stash/ へ移動済み。

パイプラインのオーケストレーション（polling / DAG 構築 / ラベル遷移 / 冪等性）は
ghdag WorkflowDispatcher が担う。workflows/issuesmith.yml を参照。
"""

__all__: list[str] = []
