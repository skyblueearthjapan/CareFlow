# INV-1 GAS App 監査 クロスレビュー B（受入観点）

## Verdict
**Status**: INCOMPLETE
**Confidence**: high
**Blockers**: 4
**Recommendation**: REQUEST_CHANGES

## 受入基準評価

| # | 観点 | Status | Evidence |
|---|---|---|---|
| 1 | 監査結論が客観的に検証可能か | PARTIAL | コード参照は付与、ただしエッジケースの運用頻度・影響件数不明、「廃棄/保護」境界根拠が主観的 |
| 2 | CareLink設計仕様との整合性 | PARTIAL | 設計仕様 §8-10 と INV-1 §6 の VPS API 14エンドポイント の1対1対応マッピングなし、ステータス用語不統一 |
| 3 | データ移行戦略の検証可能性 | MISSING | 4案比較は★印のみ、各フェーズの完了条件・担当・依存・ロールバックなし |
| 4 | 受入基準の網羅性 | MISSING | 監査に「受入基準」セクションが存在しない |

## Gaps

**Gap 1: VPS API → CareLink API 対応表の欠落（HIGH）**
- §4 に14エンドポイント列挙、設計仕様 §8-10 と1対1マッピングなし
- 設計着手後に「この GAS 呼出しをどこに移すか」が都度判断
- 提案: 旧API→新API マッピング表（廃止/維持/新設の3列）

**Gap 2: エッジケース4件の影響評価未記載（HIGH）**
- §3 のエッジケースに発生頻度・実害・CareLink 対応方針なし
- 特に「イベント行備考正規表現失敗」は本番で無音欠損リスク
- 提案: 各エッジケースに「発生頻度」「CareLink 対処方針」付記

**Gap 3: スプレッドシート ↔ PostgreSQL スキーマ未突合（MEDIUM）**
- §2 の GAS 列定義と CareLink REST API（§7-13）の型・名称差異が未検証
- 例: `start_time_minutes`（整数）と時刻型の扱い
- 提案: 両スキーマ横断対応表（旧→新 + 型変換ルール）

**Gap 4: 認証・認可の扱いが空白（MEDIUM）**
- §4 で「Bearer なし」事実記録のみ
- CareLink 移行後の認証設計方針なし（NextAuth セッション vs API Key）
- 提案: CareLink Backend 認証モデルと VPS プロキシ適用方針

## 受入チェックリスト（CareLink着手前の前提条件）

| # | 項目 | 必須度 |
|---|---|---|
| 1 | 旧 VPS API 14エンドポイント ↔ CareLink 新 API の対応表確定（廃止/代替/新設） | MUST |
| 2 | イベント行備考正規表現失敗の本番実績と対処方針 | MUST |
| 3 | GAS↔PostgreSQL の型・名称マッピング表（_minutes 整数 等） | MUST |
| 4 | Phase 1〜4 各フェーズの acceptance criteria 定義 | MUST |
| 5 | VPS 全 API「Bearer なし」状態の CareLink 認証方針 | MUST |
| 6 | スタッフ4人以上の同行ケース本番発生件数と対応方針 | SHOULD |
| 7 | PythonAllocateBridge.js 10種ペイロードと INV-2/3 IF 整合性 | SHOULD |
| 8 | noVNC iframe CSP frame-ancestors 設定の kaipoke-api.net 側対応可否 | SHOULD |

## Recommendation
REQUEST_CHANGES — VPS API↔CareLink API 対応表の欠落（Gap 1）と受入基準セクション不在が致命的、これら2点の追記なしに承認不可。
