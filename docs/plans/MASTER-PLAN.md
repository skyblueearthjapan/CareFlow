# CareFlow 統合実装計画書（マネージャー総括）

**作成日**: 2026-05-05 (初版) / 2026-05-05 (Wave 4 完了反映)
**作成者**: ディレクター・マネージャー（Claude）
**入力資料**:
- 実装計画 5本（D1〜D5）
- クロスレビュー 10本（critic A × 5 + verifier B × 5）
- Codex 最終レビュー 1本（シニアアーキテクト視点）

> **製品名注記**: 計画書群は「CareLink」名で着手したが、現行プロダクト
> 表記は **CareFlow** に確定済み (本番ドメイン `carelink.kaipoke-api.net`
> はそのまま、UI / リポジトリ / docs 先頭は CareFlow 表記)。

---

## 0. エグゼクティブサマリ

**初版判定 (2026-05-05 計画着手時)**: 5ドメイン全て REVISE / 本番投入不可。

5本の計画書はそれぞれの領域では水準以上だが、**ドメイン横断の整合性に9件のクリティカル/メジャー齟齬**があり、現状のまま実装着手すると結合フェーズで全面手戻りが発生する。Codex のシニア所見では「契約凍結」と「既存 VPS/kaipoke-api 実態調査スパイク」の先行が**必須**と判断された。

工数も再評価が必要。当初合算 66人日 → 修正込み **71.5〜76人日**、3名体制で **MVP 5〜6週・本番投入 6〜8週**が現実的なベースライン。

**ディレクターとしての推奨**: 直ちに契約凍結会議＋ VPS 調査スパイク（**Phase 0**）を実施 → 計画書群を1次改訂 → 段階的に着手（D2 純UI先行）→ 7ゲートで本番投入を判定。

### ✅ 2026-05-05 時点 進捗反映

| 旧計画項目 | 状態 | 備考 |
|---|---|---|
| **契約凍結会議を今週実施** | ✅ 完了 (2026-05-04) | 整合性パッチ P-01〜P-25 を計画書群に反映済 |
| **VPS 実態調査スパイク (来週)** | ✅ 完了 (2026-05-04) | `docs/audit/` に INV-1〜5 として記録 |
| **既存 kaipoke-api 棚卸し** | ✅ 完了 (2026-05-04) | W4-A で 14 endpoints 中継実装に活用 |
| **Phase 1 基盤構築** | ✅ 完了 (Wave 1) | backend skeleton + auth + RBAC + frontend foundation |
| **Phase 2 機能実装** | ✅ 完了 (Wave 2-3) | 5 画面 + マスタ拡張 + 初期 import |
| **Phase 3 中核機能** | ✅ 完了 (Wave 4) | kaipoke 中継 + Gemini + Geocoding + DnD + AI モーダル |
| **Phase 4 統合・QA** | 進行中 (Wave 5) | W5-A/B/C 完了、W5-F (docs) を本タスクで実施中 |

**実績工数 (2026-05-05 時点)**: 当初見積 71.5〜76 人日 → Wave 1〜4 合計
で recordable な作業は 5 月初旬時点でほぼスコープ通り消化。Wave 5 残
タスク (docs / SDK 移行) は ~3 人日見込み。

### Wave 体系 ↔ Phase 体系 対応表

| Wave | 目的 | 対応 Phase | 対応ドメイン | 主要成果物 |
|---|---|---|---|---|
| **W1** | 基盤・骨格 | Phase 1 | D1 / D2 / D5 | Backend skeleton, Auth, RBAC, frontend foundation |
| **W2** | コア画面 | Phase 2 | D3 + D1 拡張 | 週ビュー, dashboard, mobile, PWA, integrations UI |
| **W3** | マスタ拡張 | Phase 2 | D1 + D3 | special_weeks, weekly_pattern 構造化, 初期 import |
| **W4** | 統合・AI | Phase 3 | D4 + D1 + D3 | kaipoke 中継, Gemini, Geocoding, audit log, security |
| **W5** | 運用・QA | Phase 4 | D5 + 横断 | CI/CD, 監視・バックアップ, E2E, ドキュメント整備 |

---

## 1. ドメイン構成と判定一覧

| ドメイン | 範囲 | 当初工数 | 修正後工数 | 判定 | 主因 |
|---|---|---|---|---|---|
| **D1 Backend** | FastAPI / PostgreSQL / SQLAlchemy / 10テーブル / Auth | 15.0d (19.5d 含バ) | 19.5d | REVISE | API prefix、D4 テーブル所管、RBAC、staff スコープ |
| **D2 Frontend Foundation** | Next.js / Tailwind / shadcn/ui / Layout / Auth 接続 | 10.0d | 11〜12d | REVISE | refresh、共通コンポ未明記、モバイルパス、ロール |
| **D3 Screens** | 5画面 + 4モーダル + モバイル4 | 27.0d | 30d+ | REVISE | API契約、受入基準弱、E2E/性能基準不足、訪問詳細モーダル仕様欠落 |
| **D4 Integrations** | kaipoke-api 中継 / Gemini / Geocoding / VNC | 11.5d | 14〜16d | **REVISE（最重要）** | 既存実 API 乖離、VNC token 方式、companion_change 根拠なし |
| **D5 DevOps & QA** | Docker / CI/CD / Cloudflare / バックアップ | 8.0d | 10d | REVISE | kaipoke-net 不在、cloudflared 形態、VPS スペック未測定、Caddy/nginx 矛盾 |
| **合計** | | **約 71.5〜76人日** | | | |

---

## 2. クリティカル統合論点（5件・全ドメイン横断）

### CL-1. API 名前空間の未統一（Frontend⇄Backend 結合不能）
- D1: `/api/v1/*`、D3 / 設計仕様 / D4: `/api/*`
- Geocode は3者で異なるパス・メソッド（`/api/v1/geocode` POST、`/api/geocode` POST、`/api/geocoding/forward` GET）
- **判定**: Frontend が叩く先が確定不能、結合初日に全エンドポイント 404
- **解決方向**: 全計画書を **`/api/v1/*` で統一**、Geocode は `POST /api/v1/geocode`（OpenAPI で型配布、設計MDも更新）

### CL-2. Auth / RBAC 契約が未確定
- ロール: D1 `admin/staff/manager`、D2 `admin/staff` のみ、D5 `viewer` 言及
- D1 提供 `/auth/refresh` を D2 が一切実装しない → Remember me 30日/24h 破綻
- staff スコープ制限（自分以外の Visit が見える）テストなし
- **解決方向**: ロールを **`admin / manager / staff` の3値で確定**、認可マトリックス1枚作成、refresh フロー実装、staff スコープ E2E 追加

### CL-3. DB マイグレーション所管が重複（migration 事故リスク）
- D1: 12テーブル（users + audit_logs + 10）、`kaipoke_sync` は空定義
- D4: KaipokeJob / KaipokeJobItem / GeocodingCache / AiInterpretLog の4テーブル追加前提
- 両者の所管が未定義
- **解決方向**: **D1 が全テーブルの Alembic 単一責任**を持つ（D4 は schema 設計を D1 に提供のみ）、D1 の受入基準も「全テーブル数」に合わせて更新

### CL-4. 既存 kaipoke-api 連携方式の重大な乖離
- VNC token: 既存 = ランダム + サーバメモリ TTL 辞書、D4 = JWT 新規発行 → **既存検証エンドポイント `/novnc/verify` で検証不能**、kaipoke-api 変更が必要になり「CSP 1行のみ」原則が崩れる
- ポーリング型 `/api/export/result` `/api/apply/result` が D4 計画から欠落
- `companion_change` がコードベースに根拠なし（既存は `date_change` + ACCOMPANY ロール）
- **解決方向**: VNC は **既存 `/api/kaipoke/vnc-url` を中継、ランダム token URL をそのまま転送**（JWT 廃止）。ポーリング中継方式を D4 に追加。companion_change は既存 diff JSON の判定擬似コード化

### CL-5. 運用トポロジー前提の未検証
- `kaipoke-net` 名前付きネットワークは**実在しない**（既存は default bridge）
- cloudflared は config.yml 管理 / Dashboard 管理 / コンテナ / systemd のいずれか不明
- VPS スペック（メモリ・CPU・ディスク）が一切測定されていない
- D1: Caddy、D5: nginx の矛盾未解決
- Health endpoint: `/healthz` `/readyz` vs `/api/health` 不一致 → 監視常時失敗
- **解決方向**: **Phase 0（実態調査スパイク 1〜2日）を Go/No-Go ゲートに格上げ**。Caddy/nginx は nginx に統一（Cloudflare Tunnel 経由で TLS 不要）、Health は `/api/v1/healthz` `/api/v1/readyz` に統一

---

## 3. 整合性パッチ（修正必須事項表）

| ID | 内容 | 影響ドメイン | 修正者 |
|---|---|---|---|
| P-01 | API prefix 全 `/api/v1/*` に統一 | D1, D3, D4, 設計MD | D1 オーナーシップ |
| P-02 | Geocode を `POST /api/v1/geocode` に確定 | D1, D3, D4 | D1 |
| P-03 | ロール3値統一（admin/manager/staff）+ 認可マトリックス | D1, D2, D5 | D1 |
| P-04 | `/auth/refresh` を D2 NextAuth 実装に追加 | D2 | D2 |
| P-05 | staff スコープ制限 E2E テスト追加 | D1, D3 | D1 + D3 |
| P-06 | KaipokeJob/Item/GeocodingCache/AiInterpretLog を D1 所管に移管 | D1, D4 | D1 |
| P-07 | VNC token を既存中継方式に変更（JWT 廃止） | D4 | D4 |
| P-08 | `/api/export/result` `/api/apply/result` ポーリング中継を D4 に追加 | D4 | D4 |
| P-09 | companion_change 判定ロジック擬似コード化 | D4 | D4 |
| P-10 | `kaipoke-net` 不在を前提に Docker network 設計再構築 | D5 | D5 |
| P-11 | cloudflared 形態調査 + 両形態の手順分岐 | D5 | D5 |
| P-12 | VPS スペック実測 + Phase 0 を Go/No-Go ゲートに | D5 | D5 |
| P-13 | Caddy/nginx を nginx に統一 | D1, D5 | D1 修正 |
| P-14 | Health endpoint を `/api/v1/healthz`/`/readyz` に統一 | D1, D5 | D1 + D5 |
| P-15 | D2/D3 モバイルパス `/m/home`/`/m/today`/`/m/week`/`/m/me` に統一 | D2, D3 | D2 |
| P-16 | ModalShell / SegmentedControl / ConfirmDialog / Spinner を D2 に明記（+0.5d） | D2 | D2 |
| P-17 | `--bg-surface` `--c-manager` トークンを D2 に追加 | D2 | D2 |
| P-18 | サイドバー アニメ 200ms に統一（設計仕様 1-10 表を修正） | 設計MD | 設計修正 |
| P-19 | 訪問詳細モーダル仕様を 06-screen-weekly-view.md に追記 | 設計MD | 設計修正 |
| P-20 | Bearer Token 漏洩検出を D5 CI に組込（grep step 追加） | D5 | D5 |
| P-21 | gitleaks pre-commit + CI workflow を D5 に明示 | D5 | D5 |
| P-22 | runbook 他者実行検証プロセスを D5 受入基準に追加 | D5 | D5 |
| P-23 | DnD パフォーマンス計測手段（Playwright tracing + FPS閾値） | D3 | D3 |
| P-24 | E2E 業務フルフロー（ログイン→週ビュー→DnD→AI入力→差分適用）追加 | D3, D5 | D3 |
| P-25 | iOS Safari dnd-kit タッチ DnD 制約対策 | D3 | D3 |

---

## 4. 既存資産保護の妥当性

**現状判定: 「CSP 1行のみ」は未証明状態**

| 観点 | 状態 | 必要アクション |
|---|---|---|
| CSP 1行追加 | 仕様明確 | OK、ただしステージング検証手順を D4 受入基準に追加 |
| VNC token | **危険**（CL-4） | JWT 方式廃止、既存ランダム token 中継に変更 |
| ポーリング型 API | **欠落**（CL-4） | D4 に中継方式追加 |
| diff JSON schema | 未確認 | 既存 `commands/auto_apply.py` の入力スキーマを Backend で zod 再検証 |
| companion_change | 根拠なし | 既存 diff レスポンス参照、判定擬似コード化 |

→ 実装前に **「既存API棚卸しスパイク 1〜2日」が必須**（Phase 0 に組込）

---

## 5. 改訂版マイルストーン（再ベースライン）

```
[Phase 0: 契約凍結 + 実態調査] (1週間 / 全ドメイン参加必須)
  - 契約凍結会議（API prefix / Auth / DB所管 / Health / Geocode / Roles 1枚決定表）
  - VPS 実態調査（cloudflared / network / disk / spec / kaipoke-api 実 API 棚卸し）
  - 既存 kaipoke-api dryRun 連携検証
  - 計画書 D1〜D5 の整合性パッチ P-01〜P-25 反映

[Phase 1: 基盤構築] (2週間 / 並列)
  - D1: T01-T15（Backend skeleton + Auth + 10テーブル + Alembic）
  - D2: Phase 1-3（Next.js / tokens / Layout / shadcn/ui Warm化）
  - D5: Phase 1-3（Docker / VPS デプロイ準備 / Cloudflare 設定）

  → M1 ゲート: dev 環境疎通、ログイン成功、admin/staff ロール、Sidebar 折りたたみ

[Phase 2: 機能実装] (3週間 / 並列)
  - D1: T16-T35（API endpoints / RBAC / Geocode / AI 中継 / Integration forward）
  - D3: Phase A-F（ログイン / ダッシュボード / マスタ / モバイル）
  - D4: Phase A-D（Kaipoke Client / 中継 / 差分プレビュー / VNC）

  → M2 ゲート: マスタ CRUD 完成、週ビュー読み取り、Integration ステータス疎通

[Phase 3: 中核機能] (2週間)
  - D3: Phase D（週ビュー DnD・モード切替・5日間集中）
  - D3: Phase E-G（モーダル4本 + 連携センター + AI入力）
  - D4: Phase E-G（Gemini + Geocoding + スクショ）

  → M3 ゲート: DnD E2E、AI入力承認モーダル、差分プレビュー チェック機構

[Phase 4: 統合・QA] (1〜2週間)
  - D5: Phase 4-7（CI/CD / 監視 / バックアップ / 自動デプロイ）
  - 業務フルフロー E2E
  - kaipoke-api 無影響検証
  - 7ゲート最終判定

  → M4 ゲート: 全 G1-G7 通過 → 本番投入

合計: 約 6〜8週（3名体制）/ MVP 5〜6週
```

---

## 6. 本番投入前の必須ゲート（Codex 提案）

| Gate | 条件 | 担当 | 検証方法 |
|---|---|---|---|
| **G1 API契約凍結** | OpenAPI / prefix / health / error / geocode / integration 確定 | D1 | OpenAPI artifact + 全計画書 grep |
| **G2 DB契約凍結** | D1/D4 統合 ERD、Alembic up/down、seed、監査ログ | D1 | `alembic downgrade base && upgrade head` 通過 |
| **G3 Auth/RBAC合格** | JWT refresh / Remember me / 3ロール / staff スコープ E2E | D1 + D2 | pytest + Playwright |
| **G4 kaipoke実API検証** | export/diff/apply/result / VNC / dryRun / 409/503 / CSP ステージング確認 | D4 + D5 | 統合テスト + DevTools スクショ |
| **G5 画面E2E合格** | ログイン → 週ビュー → DnD → AI入力 → 差分適用 → 履歴 | D3 + D5 | Playwright trace |
| **G6 既存VPS無影響** | kaipoke-api P95 baseline ±10%、リソース、dev/prod、rollback | D5 | curl 100回計測 + 比較 |
| **G7 運用受入** | backup restore、secrets rotation、runbook 他者実行、PII非出力、gitleaks CI green | D5 | runbook 実行ログ |

---

## 7. レビュアーが見落とした追加リスク（Codex 抽出）

1. **既存 GAS/Excel/手動データからの移行計画なし** — 初期マスタ投入、差分検証、旧新並行期間が未定義
2. **割当ロジックの正当性検証が薄い** — 業務ルール、祝日、移動時間、同住所、2名体制の期待結果セット必要
3. **同時編集・排他制御が弱い** — 複数管理者の DnD・差分適用・AI登録が競合
4. **閲覧監査が不足** — 医療情報では閲覧ログも検討対象（D1 は書込監査中心）
5. **障害時の業務継続手順が未定義** — CareLink / kaipoke / AI / Maps 停止時の手動運用

→ Phase 0 で**追加4タスク**として組み込むこと推奨：
- 移行計画タスク（旧マスタ → CareLink マスタの mapping + 並行期間プラン）
- 割当ロジック期待結果セット（テストケース 30 個以上）
- 同時編集排他戦略（楽観ロック or DB トランザクション）
- 業務継続 runbook（5シナリオ）

---

## 8. ディレクターの推奨実行プラン

### 即実行（今週） ✅ 完了 (2026-05-04)
1. ~~**契約凍結会議**を 1 日で実施（決定表 1 枚を docs/plans/CONTRACTS.md として確定）~~
2. ~~**既存 kaipoke-api / VPS 実態調査スパイク** を 2 日で実施（D5 主導、D4 同席）~~
3. ~~**整合性パッチ P-01〜P-25 を 5 計画書に反映**（私 = ディレクターが取りまとめ）~~

### 来週（Phase 0 完了後） ✅ 完了 (2026-05-05)
4. ~~**改訂版 5 計画書 + 統合 ERD + 認可マトリックス**を承認~~
5. ~~**D2 純 UI 先行着手**（D1/D4 完了を待たず Foundation を進められる範囲）~~
6. ~~**3名体制でのアサイン確定**（誰が D1/D2/D3/D4/D5 のどこを担うか）~~

### Phase 1 開始時（来々週） ✅ 完了 (Wave 1)
7. ~~**D1/D2/D5 の Phase 1 並列着手**~~
8. ~~**G1 ゲート（API 契約凍結）通過**を Phase 1 完了条件に設定~~

### 残タスク (2026-05-05 時点)
9. **Wave 5 残**: ドキュメント整備 (W5-F, 本タスク) + Gemini SDK 移行調査
10. **Phase 5 (Wave 5 後)**: 移行計画 (旧マスタ → CareFlow)、業務継続 runbook 5 シナリオ、同時編集排他戦略

---

## 9. 結論

**現状の 5 計画書は実装着手前に整合性修正が必須。** ただし骨格は良質で、修正内容は明確（P-01〜P-25）。Phase 0 を 1 週間集中で完遂すれば、Phase 1 以降は安定した並列実装が可能。

**ユーザー（あなた）への次のアクション選択肢**:

- **A. 契約凍結会議 + Phase 0 スパイクを実施する判断**（私の推奨）
- **B. 整合性パッチ P-01〜P-25 を計画書に反映する作業を開始**（ディレクター主導）
- **C. 5 計画書のうちどれかを優先して着手したい**（理由を明示）
- **D. その他の方針**

ご判断ください。判断後、各ドメインのオーナーまたは実装エージェントへ作業を割り振ります。
