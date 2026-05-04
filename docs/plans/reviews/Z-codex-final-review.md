# CareLink 実装計画 最終レビュー（Codex）

実施日: 2026-05-05 / レビュアー: Codex CLI（シニアアーキテクト視点）

## 1. 総合判定

| ドメイン | 判定 | 理由 |
|---|---|---|
| D1 Backend | **REVISE** | API/DB/Auth の骨格は良いが、D4テーブル所管、API prefix、RBAC、staffスコープが未確定 |
| D2 Frontend Foundation | **REVISE** | トークン設計は概ね良いが、refresh、D3必須コンポーネント、モバイルルート不一致が残る |
| D3 Screens | **REVISE** | 画面網羅性は高いが、API契約未確定、受入基準が代表例止まり、E2E/性能基準不足 |
| D4 Integrations | **REVISE** | 最重要。既存kaipoke-apiの実API/VNC方式との乖離があり、このまま実装すると既存資産保護に失敗する |
| D5 DevOps/QA | **REVISE** | 方針は妥当だが、cloudflared/ネットワーク/VPS実態未確認、health path、nginx/Caddy矛盾が残る |

**全体判定: 本番投入不可。** 実装着手も「全域同時開始」は不可。まず契約凍結と既存VPS/kaipoke-api実態調査を完了させ、D2の純UI基盤のみ限定先行が妥当。

## 2. クリティカル統合論点

1. **API名前空間が未統一** — D1は `/api/v1/*`、D3/設計/D4は `/api/*`。geocodeも `POST /api/v1/geocode`、`POST /api/geocode`、`GET /api/geocoding/forward` が混在
2. **Auth/RBAC契約が未確定** — `admin/staff/manager/viewer` が計画間で揺れ、NextAuth refresh / Remember me / staff スコープが未完
3. **DBマイグレーション所管が重複** — D1は12テーブル前提、D4は KaipokeJob/Item/GeocodingCache/AiInterpretLog 追加前提。Alembic 単一責任が曖昧
4. **既存kaipoke-api連携方式が危険** — D4のJWT式VNC tokenは既存noVNC検証方式と合わない可能性が高い。`/api/export/result`、`/api/apply/result` 等のポーリング型実APIも未反映
5. **運用トポロジーの前提未検証** — `kaipoke-net` 実在性、cloudflared形態、dev/prodポート、VPS余力、Caddy vs nginx、health path が未確定

## 3. 計画書間の整合性リスク

- **API prefix**: `/api/v1` に統一 or nginx で `/api` → `/api/v1` rewrite
- **Health**: D1 `/healthz`/`/readyz` vs D5 `/api/health` `localhost:8000/health` 不一致 → 本番監視失敗
- **認証**: D1 `/auth/refresh` に D2 未対応、Remember me 30日/24h 破綻
- **ロール**: D1 `manager`、D5 `viewer`、D2/D3 admin/staff のみ。認可表1枚統合必須
- **DB**: D4 追加テーブルは D1 管理に寄せるべき。D4 独自スキーマは migration 事故
- **Geocode**: D1/D3/D4/設計でパス・メソッド不一致
- **Mobile routes**: D2 `this-week/mypage` vs D3 `/m/week`/`/m/me` 不一致
- **Dockerfile/compose**: D2とD5で責務重複。最終Docker は D5 所管、D2 は build 要件提供に留める

## 4. 既存資産保護の妥当性

現案の「kaipoke-api は CSP 1行のみ」は**まだ妥当と証明できない**。

特に VNC token を CareLink 側 JWT で新規発行する設計は危険。既存 kaipoke-api がランダム token + サーバ側 TTL 辞書で検証しているなら、JWT は検証不能で、kaipoke-api 変更が必要になる。**既存資産保護を守るなら、既存の VNC URL 発行 API を Backend が中継し、その URL をそのまま Frontend へ返す方式にすべき**。

既存 API の認証有無、ジョブ結果取得、diff/apply JSON schema、`companion_change` の根拠が未確定。実装前に**1〜2日の「既存API棚卸しスパイク」が必須**。

## 5. 工数見積もり

素直に合算すると約66人日ではなく **71.5〜76人日** に見える：

- D1: 15.0d、バッファ込み19.5d
- D2: 10d、修正後は11〜12d
- D3: 27d、QA強化後は30d超
- D4: 11.5d、既存API調査・修正込みで14〜16d
- D5: 8d、Phase 0再設計込みで10d前後

3名体制でも理論値4〜5週だが依存関係上の詰まりあり。現実的には **MVP検証まで5〜6週、本番投入まで6〜8週**。

## 6. 本番投入前の必須ゲート

- **G1 API契約凍結**: OpenAPI、prefix、health、error形式、geocode、integration全endpoint を確定
- **G2 DB契約凍結**: D1/D4統合ERD、Alembic upgrade/downgrade、seed、監査ログ仕様を確定
- **G3 Auth/RBAC合格**: JWT refresh、Remember me、admin/manager/staff 権限、staff スコープ制限 E2E
- **G4 kaipoke実API検証**: export/diff/apply/result、VNC、dryRun、409/503、CSP をステージングで確認
- **G5 画面E2E合格**: ログイン→週ビュー→DnD→AI入力→差分適用→履歴までの業務フルフロー
- **G6 既存VPS無影響**: kaipoke-api P95 が baseline ±10% 以内、リソース上限、dev/prod 分離、rollback 実証
- **G7 運用受入**: backup restore、secrets rotation、runbook 他者実行、PIIログ非出力、gitleaks CI green

## 7. A/B レビュアーの盲点（追加リスク）

- **既存 GAS/Excel/手動データからの移行計画がない** — 初期マスタ投入、差分検証、旧新並行期間が未定義
- **割当ロジックの正当性検証が薄い** — 業務ルール、例外、祝日、移動時間、同住所、2名体制の期待結果セット必要
- **同時編集・排他制御が弱い** — 複数管理者の DnD、差分適用、AI登録が競合する
- **閲覧監査が不足** — D1 は書込監査中心、医療情報では閲覧ログも検討対象
- **障害時の業務継続手順が未定義** — CareLink 停止時、kaipoke 停止時、AI/Maps 停止時の手動運用が必要

## 8. 推奨次アクション

1. **契約凍結会議を実施** — API prefix、Auth/RBAC、DB所管、health、geocode、integration endpoint を1枚の決定表に
2. **既存kaipoke-api/VPS調査スパイクを先行** — 実API、VNC token、cloudflared、Docker network、VPS余力、バックアップ先を確認
3. **工数とマイルストーンを再ベースライン** — 66人日想定を撤回し、G1〜G7 を通過条件にした MVP/本番二段階計画へ組み直す
