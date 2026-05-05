# CareFlow v2 リリースノート

> **Release date**: 2026-05-06
> **対応設計書**: `docs/plans/v2-allocation-redesign.md` v0.9 / `docs/plans/v2-implementation-plan.md` v0.2
> **実行ログ**: `docs/plans/v2-execution-log.md`
> **本番 URL**: `https://carelink.kaipoke-api.net`

---

## 1. 概要

CareFlow v2 は、v1 (`careflow-scheduler` GAS + VPS Python 割当エンジン) で破綻したマスタ肥大化と全条件網羅型ロジックを全面的に作り直したリリースです。
**「人間が直感的にスケジュールを組み、システムは記憶して繰り返す」** という分業に切り替え、マスタの最小化・3 レイヤー自動割当・AI 自然言語入力・申請履歴の 4 軸で運用負荷を削減します。

---

## 2. 主要変更点

### 2.1 3 レイヤー構造の自動割当

v1 の単一巨大ロジックを 3 段階に分解。各レイヤーで人補正 UI を提供。

| レイヤー | 役割 | 実装 |
|---|---|---|
| **Layer 1** プール展開 | `weekly_pattern` → `visits` 生成。特別週判定。新規患者は保留プールへ | `backend/app/services/scheduling/layer1_expander.py` |
| **Layer 2** コース分け | K-means + 制約後処理。直線距離 (Haversine) で総移動を最小化 | `backend/app/services/scheduling/layer2_clustering.py` |
| **Layer 3** スタッフ割付 | ハンガリアン法 + ローテーション分散 (直近 1 週は強制除外、それ以前はソフトペナルティ) | `backend/app/services/scheduling/layer3_assignment.py` |

MVP 前提値: Q1 サービス時間枠消費 / Q3 ハイブリッドローテ / Q4 直線距離 / Q5 15 分粒度。

### 2.2 コース概念の導入

- 新規 `courses` テーブル (UNIQUE `(year, week, weekday, code)`) を追加。
- `visits.course_id` / `visits.required_staff_count` / `visits.visit_group_id` を追加し、
  `visit_staff_assignments` 多対多テーブルで 2 名体制 (visit_group_id 同一) も表現可能。
- 拠点 (Office) は稲毛・都賀をシード。`OfficeAssigner.resolve_for_address` で患者住所→拠点を自動紐付け。

### 2.3 AI 自然言語入力フロー

- `POST /api/v1/ai/interpret` の `context_type` を拡張: `staff_create` / `patient_cancel` / `patient_reschedule` / `patient_special_week` を追加。
- 範囲外発話は `out_of_scope` で明示的に拒否。
- `AiInputModal` に拡張ポイント (`onSubmitInterceptor` / `MissingInfoSlot` / `SubmissionMode`) を整備し、以下 3 機能を統合:
  - **AI 解釈→申請履歴フロー** (`SubmitToPendingHandler`): デバイス・ロールに応じて pending / approved を自動判定。
  - **不足情報補完モーダル** (`MissingInfoModal`): patient_create / staff_create で必須欠損を赤枠ハイライト。
  - **AI ヘルプページ** (`/help/ai`): できる/できない一覧 + 例文集 + 制約事項。
- モバイルは右下 FAB (`MobileAiFab`) から音声入力主体で利用可能。

### 2.4 申請履歴 (`pending_requests`)

- 全業務操作を「申請 → 承認 → 反映」モデルに統一。
- `PendingRequestApplier` が承認時に `request_type` ごとに実業務テーブルへ反映。
  - 対応 `request_type`: `staff_off` / `staff_event` / `staff_mentor` / `patient_create` / `staff_create` / `patient_cancel` / `patient_reschedule` / `patient_special_week_on` / `patient_special_week_off`
- **冪等性**: 同一申請を 2 回 approve しても 1 回しか反映されない (`applied_at` + `version` キー)。
- **失敗時 rollback**: 反映失敗時に DB トランザクションが rollback される。
- **RBAC**: admin / manager は承認可、staff は申請のみ。
- **UI**: `/admin/pending-requests` (PC) と スケジュール画面の `PendingRequestPanel` の両方から承認可能。

### 2.5 特別週統合

- 旧 `/special-weeks` 専用ページを廃止 (W6-MIG2、`410 Gone`)。
- 患者マスタに `special_weekly_pattern` / `special_week_active` (`string[]`) を統合。
- AI からの切替操作 (`patient_special_week_on` / `_off`) は申請履歴経由で反映。

### 2.6 マスタ整理 (削減 16 項目)

| マスタ | 削除項目 |
|---|---|
| 患者 (10 項目) | 年齢 / NG時間 / 指定タイプ / NGスタッフ / 同行希望スタッフ / 継続要望 / 必要スタッフ数 / 曜日優先度 / NG曜日 / エリア |
| スタッフ (6 項目) | can_double_team / 自宅住所 + lat/lng / 得意エリア / 1日最大訪問数 / スキル / 割付ボリューム |

スタッフ状態は `在籍 / 休職 / 退職` の 3 値に統一。メンターフィールドは「詳細」セクションへ移設。

### 2.7 スケジュール UI v2

- 縦軸時刻 (15 分粒度) × 横軸曜日のグリッド + コース行 (M/A/B/C/D) + 下部保留プール。
- dnd-kit によるドラッグ操作で保留⇔セル⇔保留の双方向移動、+1 人ボタンで 2 名体制化。
- 「**固定**」ボタンで `POST /api/v1/schedule/fix` がその週レイアウトを各患者の `weekly_pattern` に保存 (差分のみ書込み・トランザクショナル)。

---

## 3. マイグレーション手順

### 3.1 事前準備

1. `develop` を最新化し、本リリース (`feat/v2-w6-freeze-v1` 統合後の `master`) のタグ `v2-wave6` に揃える。
2. **本番 DB のフルバックアップ**を取得 (`docs/deployment/backup-restore-runbook.md` 準拠)。
3. staging で `up → 業務操作 → down → up` を必ず検証してから本番適用。

### 3.2 DB マイグレーション

`docs/plans/v2-migration-reservations.md` に予約された順序で適用:

```bash
# backend/ 配下で実行
alembic upgrade head
```

| バージョン | 内容 |
|---|---|
| `0007_v2_master_cleanup` | 患者マスタ廃止フィールド drop + `special_weekly_pattern` / `special_week_active` 追加 |
| `0008_staff_master_cleanup` | スタッフ廃止フィールド drop + 状態 3 値正規化 |
| `0009_courses_visits_extension` | `courses` / `visit_staff_assignments` 新設 + `visits` 拡張 |
| `0010_pending_requests` | `pending_requests` テーブル新設 |

**expand-contract 方式**: drop column / JSON 変換前に backup table へコピー済み。
ロールバック時は同 down migration 実行で旧フィールドを復元可能。

### 3.3 既存データ移行 (W6-MIG1)

- 既存患者・スタッフの廃止フィールドは backup table に退避 → drop。
- 既存 `special_weeks` レコードは `patients.special_weekly_pattern` / `special_week_active` へ統合。
- 拠点 (稲毛・都賀) シードを `backend/scripts/seed_offices_v2.py` で投入。

### 3.4 v1 → v2 切替

1. 旧 `careflow-scheduler` (GAS) を read-only モードへ切替 (運用周知後)。
2. CareFlow v2 (`carelink.kaipoke-api.net`) を本番 deploy (`.github/workflows/deploy.yml` 経由)。
3. 切替日から 1 週間は v1 / v2 並走で差異を観察。
4. VPS Python 割当エンジンの cron / systemd を停止し、kaipoke-api 中継ジョブのみ v2 backend 経由に切替。
5. **1 ヶ月の経過観察後**、v1 GAS / VPS Python エンジンを撤去。

---

## 4. Breaking Changes

| 項目 | 影響 | 対応 |
|---|---|---|
| 患者マスタ 10 項目削除 | 旧 API レスポンスに依存するクライアントは失敗 | v2 schema (`PatientV2`) へ移行 |
| スタッフマスタ 6 項目削除 | 同上 | `StaffV2` へ移行 |
| `/special-weeks` ページ・API 廃止 | アクセスで `410 Gone` | 患者マスタの `special_weekly_pattern` を使用 |
| 旧 Python 割当エンジン v1 廃止 | VPS Python の cron / systemd を停止 | v2 の Layer 1〜3 へ完全移行 |
| 全業務操作の申請履歴経由化 | 直接更新 API は admin/manager 限定に縮退 | staff ロールは `pending_requests` 経由で申請 |
| `frontend/lib/schemas/{patient,staff,office}.ts` | 旧 schema は v2 schema からの re-export に縮退 (Wave 1 完了時に削除予定) | 新コードは `frontend/lib/schemas/v2/*` を直接 import |
| `careflow-scheduler` (GAS) リポジトリ | 保守凍結 | バグ修正・機能追加は v2 で実施 |

---

## 5. 既知の制限事項

| 項目 | 状態 | 備考 |
|---|---|---|
| **Gemini SDK 移行**: `google-generativeai` (deprecated) → `google-genai` | 未対応 | 別 sprint で対応予定。`backend/app/services/gemini_client.py` 冒頭 docstring 参照 |
| **訪問頻度・訪問週の具体仕様** | 保留 | 設計書 §12 残課題に記載。後追いで決定可能 |
| **Layer 2 距離計算** | 直線距離 (Haversine) のみ | 必要に応じて Google Maps Distance Matrix API へ差し替え予定 |
| **Layer 3 ローテーション強度** | ハイブリッド固定 | 現場フィードバックに応じて強度調整可能 |
| **Layer 4 (家族構成・同居者考慮)** | 未実装 | v2.1 以降で検討 |
| **VNC iframe** | kaipoke-api 既存実装に依存 | CSP frame-ancestors 許可で動作 |
| **`staff_weekly_overrides` / `staff_events`** | v1 維持 | Wave 1 で要決定とした項目、v1 のまま継続使用 |
| **AI 信頼度しきい値** | 0.7 未満で確認モーダル強制 | 実運用ログを見て調整予定 |
| **モバイル AI FAB** | 雛形のみ (デスクトップ AiFab の薄いラッパ) | 音声入力 UX 強化は v2.1 以降 |
| **2 名体制 visit_group_id** | 同一時刻・同一患者のみ | 異時刻分割訪問のグルーピングは未対応 |

---

## 6. 関連ドキュメント

- 設計仕様: `docs/plans/v2-allocation-redesign.md`
- 実装手順書: `docs/plans/v2-implementation-plan.md`
- 実行ログ: `docs/plans/v2-execution-log.md`
- API 契約: `docs/plans/v2-api-contracts.md`
- マイグレーション予約表: `docs/plans/v2-migration-reservations.md`
- デプロイ手順: `docs/deployment/runbook.md`
- バックアップ・復元: `docs/deployment/backup-restore-runbook.md`
- Secrets ローテーション: `docs/deployment/secrets-rotation-runbook.md`

---

## 7. 改訂履歴

| 日付 | バージョン | 内容 |
|---|---|---|
| 2026-05-06 | v2.0 | Wave 6 完了に伴う初版発行 (v1 凍結通知含む) |
