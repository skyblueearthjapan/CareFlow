# v2 実装実行ログ

> Director: Claude (Opus 4.7)
> 開始日: 2026-05-05
> 対応設計書: `v2-allocation-redesign.md` v0.9 / `v2-implementation-plan.md` v0.2

## 実行体制

| ロール | 種別 | 担当 |
|---|---|---|
| Director | Claude Opus 4.7 (本会話) | 進捗管理 / 品質確認 / マージ / デプロイ |
| Implementer | Agent (`executor`) | チケット実装 |
| Reviewer | Agent (`code-reviewer` or `critic`) | レビュー |
| Final Reviewer | mcp__codex__codex | 全実装の総合レビュー |

## Wave 別進捗

### Wave 0: 基盤契約定義（Sequential）

| ID | 内容 | Implementer 状態 | Reviewer 状態 | マージ |
|---|---|---|---|---|
| 0-A | DB マイグレーション番号予約 | ⏳ 起動中 | — | — |
| 0-B | OpenAPI 雛形 + context_type↔request_type 対応表 | ⏳ 起動中 | — | — |
| 0-C | 共有型 11 種定義 | ⏳ 起動中 | — | — |

> Wave 0 は 1 エージェントで 0-A/0-B/0-C を統合実装（小粒・密結合のため）

### Wave 1: マスタ整理（Parallel × 6）

| ID | 内容 | Implementer 状態 | Reviewer 状態 | マージ |
|---|---|---|---|---|
| W1-BE1 | 患者マスタ整理 | 待機 | 待機 | — |
| W1-BE2 | スタッフマスタ整理 | 待機 | 待機 | — |
| W1-BE3 | 拠点 (Office) 整備 | 待機 | 待機 | — |
| W1-FE1 | 患者フォーム削減 | 待機 | 待機 | — |
| W1-FE2 | スタッフフォーム削減 | 待機 | 待機 | — |
| W1-FE3 | 拠点フォーム | 待機 | 待機 | — |

### Wave 2: 新エンティティ（Parallel × 4）

| ID | 内容 | Implementer 状態 | Reviewer 状態 | マージ |
|---|---|---|---|---|
| W2-BE4 | Course/Visit 拡張 | 待機 | 待機 | — |
| W2-BE5 | pending_requests + Applier | 待機 | 待機 | — |
| W2-BE6 | AI scope 拡張 | 待機 | 待機 | — |
| W2-FE4 | モバイル AI FAB（雛形） | 待機 | 待機 | — |

### Wave 3: スケジュール UI v2

| ID | 内容 | Implementer 状態 | Reviewer 状態 | マージ |
|---|---|---|---|---|
| W3-BE-FIX | schedule/fix エンドポイント | 待機 | 待機 | — |
| W3-FE5 | スケジュールグリッド v2 | 待機 | 待機 | — |
| W3-FE6 | 申請履歴ビュー（PC） | 待機 | 待機 | — |
| W3-FE7 | スケジュール画面への申請パネル統合 | 待機 | 待機 | — |

### Wave 4: アルゴリズム L1〜L3

| ID | 内容 | Implementer 状態 | Reviewer 状態 | マージ |
|---|---|---|---|---|
| W4-BE7 | Layer 1 アルゴリズム | 待機 | 待機 | — |
| W4-BE8 | Layer 2 アルゴリズム | 待機 | 待機 | — |
| W4-BE9 | Layer 3 アルゴリズム | 待機 | 待機 | — |
| W4-FE8 | コース表示 + 微調整 UI | 待機 | 待機 | — |
| W4-FE9 | スタッフ割付実行 UI | 待機 | 待機 | — |

### Wave 5: AI 統合

| ID | 内容 | Implementer 状態 | Reviewer 状態 | マージ |
|---|---|---|---|---|
| W5-FE10 | AiInputModal 拡張ポイント | 待機 | 待機 | — |
| W5-FE11 | AI 解釈→申請履歴フロー | 待機 | 待機 | — |
| W5-FE12 | 不足情報補完モーダル | 待機 | 待機 | — |
| W5-FE13 | AI ヘルプページ | 待機 | 待機 | — |

### Wave 6: 移行・E2E・凍結

| ID | 内容 | 状態 |
|---|---|---|
| W6-MIG1 | 既存データ移行 | 待機 |
| W6-MIG2 | /special-weeks ページ廃止 | 待機 |
| W6-E2E | Playwright E2E | 待機 |
| W6-FREEZE | v1 凍結 | 待機 |

## Codex 最終レビュー

| 項目 | 状態 |
|---|---|
| 全 Wave 完了確認 | 待機 |
| Codex 全体レビュー実行 | 待機 |
| 指摘事項対応 | 待機 |

## デプロイ

| 項目 | 状態 |
|---|---|
| 最終 commit | 待機 |
| GitHub push | 待機 |
| VPS デプロイ | 待機 |
| 動作確認 (https://carelink.kaipoke-api.net/) | 待機 |

## 実行履歴

| 日時 | イベント | 詳細 |
|---|---|---|
| 2026-05-05 | 設計書 v0.9 + 実装手順書 v0.2 commit | `ee6d4e5` |
| 2026-05-05 | 実行ログ作成 | このファイル |
| 2026-05-05 | Wave 0 起動 | (実行中) |
