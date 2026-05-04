# D4: External Integrations 実装計画書

## 1. 概要・目的

CareLink Backend (D1) を「中継ハブ」として、既存 VPS の `kaipoke-api`（Flask + Playwright + noVNC）、Gemini API、Google Maps Geocoding API を Frontend (D2/D3) から安全に利用可能にする。既存 `kaipoke-api` のコアロジックは無傷で残し、CareLink 側はラッパー (`/api/integration/*`)・AI入力 (`/api/ai/interpret`)・Geocoding (`/api/geocoding/*`)・ジョブ履歴永続化・差分プレビュー編集 UI バックエンドを担う。

### 設計原則
- **既存資産の保護**: kaipoke-api 側の変更は CSP の `frame-ancestors` 1行追加のみ
- **機密の集約**: `KAIPOKE_API_TOKEN` / `GEMINI_API_KEY` / `GOOGLE_MAPS_API_KEY` は CareLink Backend のみが保持。Frontend は決して触れない
- **非同期ジョブ**: kaipoke-api の async ジョブ ID を Backend が中継しつつ、`KaipokeJob` テーブルで永続化
- **Bearer Token + 短命 token**: VNC URL は 30分 TTL の使い捨て JWT 風 token を Backend が発行

## 2. 統合アーキテクチャ図

```
┌──────────────┐     HTTPS        ┌─────────────────────┐     HTTPS+Bearer    ┌──────────────────┐
│ Frontend     │ ───────────────▶ │ CareLink Backend    │ ──────────────────▶ │ kaipoke-api (VPS)│
│ (Next/React) │                  │ (FastAPI + Prisma   │                     │ Flask+Playwright │
└──────┬───────┘                  │  もしくは SQLAlchemy)│                     └──────────────────┘
       │                          └────┬────────────────┘
       │                               │           │
       │                               │           ├──HTTPS+API Key──▶ Gemini API
       │                               │           │
       │                               │           └──HTTPS+API Key──▶ Google Maps Geocoding
       │                               │
       │  ┌──────────────────┐         │ Postgres
       └─▶│ noVNC iframe     │         │   - KaipokeJob / KaipokeJobItem
          │ (kaipoke-api直)  │         │   - CorrectionSheet / CorrectionItem
          └──────────────────┘         │   - GeocodingCache
                                       │   - AiInterpretLog
                                       └──────
```

- **VNC のみ Frontend → kaipoke-api を直接 iframe**（30分 token 付き URL を Backend 経由で発行）
- 他の全ての REST 呼び出しは Backend が中継

## 3. 依存関係

| 依存 | 詳細 | 用途 |
|---|---|---|
| D1 Backend | FastAPI + 認証 Middleware + DB client | 全エンドポイントの土台 |
| 既存 kaipoke-api | https://kaipoke-api.net (72.60.211.213) | `/api/expand` `/api/export` `/api/diff` `/api/apply` `/api/status` `/api/stop` 等 |
| Gemini API | `google-generativeai` Python SDK / model `gemini-1.5-flash` | 自然言語→構造化 JSON |
| Google Maps Geocoding | REST `maps.googleapis.com/maps/api/geocode/json` | 住所→緯度経度 |
| Web Speech API | ブラウザ標準（Frontend） | 音声認識 |

## 4. タスク分解

### Phase A: 中継基盤と環境構築（2日）

A-1. **環境変数 / シークレット管理** (0.25d) — `.env.example` に `KAIPOKE_API_BASE_URL` `KAIPOKE_API_TOKEN` `GEMINI_API_KEY` `GOOGLE_MAPS_API_KEY` `VNC_TOKEN_SECRET` 追加、zod/pydantic 検証
A-2. **KaipokeClient 共通モジュール** (1d) — httpx + Bearer 自動付与 + timeout 30s + 5xx で 1回リトライ + HTTP 409 → KaipokeBusyError マッピング
A-3. **DB スキーマ追加** (0.5d) — KaipokeJob / KaipokeJobItem / CorrectionSheet / CorrectionItem / GeocodingCache / AiInterpretLog
A-4. **認証 Middleware の admin ガード再利用** (0.25d) — `/api/integration/*` admin only、`/api/ai/*` admin/staff

### Phase B: Kaipoke 中継エンドポイント（3日）

B-1. `GET /api/integration/status` (0.25d) — kaipoke `/api/status` 中継 + 直近 KaipokeJob 結合
B-2. `POST /api/integration/expand` (0.5d) — KaipokeJob 作成 → kaipoke 呼び出し → jobId 返却
B-3. `POST /api/integration/export` (0.5d) — 非同期、CSV を `/tmp` に 30分 TTL キャッシュ
B-4. `POST /api/integration/diff` (1d) — 差分結果を CorrectionSheet/Item に展開保存。**delete+add 統合ロジック**（同一 patient × 近接日付 ±1日 → companion_change）
B-5. `POST /api/integration/apply` (1d) — `CorrectionItem.include=true` のみ抽出 → 修正シート JSON 再構築 → kaipoke `/api/apply`
B-6. `GET /api/integration/jobs/:id` (0.5d) — kaipoke status + DB カウント
B-7. `POST /api/integration/jobs/:id/stop` (0.25d)
B-8. `GET /api/integration/jobs?limit=20` (0.25d)
B-9. `PATCH /api/integration/job-items/:id` (0.25d) — manuallyHandled / comment

### Phase C: 差分プレビュー UI 用 API（1日）

C-1. `GET /api/integration/correction-sheets/latest?month=` (0.25d)
C-2. `GET /api/integration/correction-sheets/:id/items?type&include&limit` (0.25d)
C-3. `PATCH /api/integration/correction-items/:id` (0.25d) — include / comment / manuallyHandled
C-4. `POST /api/integration/correction-sheets/:id/items/bulk` (0.25d) — 全選択 / 全解除 / 失敗のみ等

### Phase D: VNC URL 発行と CSP（0.5日）

D-1. `GET /api/integration/vnc-url` (0.25d) — 30分 TTL JWT 発行 (HS256, exp, sub=userId, aud=novnc)
D-2. **kaipoke-api 側 CSP 1行追加** (0.25d) — `frame-ancestors 'self' https://carelink.kaipoke-api.net`

### Phase E: Gemini AI 入力（2.5日）

E-1. **GeminiClient モジュール** (0.5d) — `google-generativeai` ラッパー、`response_mime_type: "application/json"` + JSON Schema 強制
E-2. **プロンプトビルダー** (0.5d) — スタッフ・患者一覧を DB から動的取得・5分キャッシュ、今日日付・週番号注入
E-3. `POST /api/ai/interpret` (1d) — Gemini 呼び出し → JSON パース → AiInterpretLog 記録 → `{ actions: [{action_type, confidence, fields, raw}] }`
E-4. **アクション登録パススルー** (0.5d) — 確認モーダル承認後は既存 D1 endpoint を Frontend が直接呼ぶ

### Phase F: Geocoding 中継（0.5日）

F-1. `GET /api/geocoding/forward?address=` (0.5d) — GeocodingCache 検索 → ヒットなら即返却 / ミスなら Google Maps → キャッシュ保存

### Phase G: 失敗時スクリーンショット（0.5日）

G-1. `GET /api/integration/job-items/:id/screenshot` (0.5d) — kaipoke の screenshots を Bearer 付きで取得 → 5分 TTL 署名 URL or stream

### Phase H: テストと運用（1.5日）

H-1. ユニットテスト (0.5d) — KaipokeClient モック、CorrectionSheet 統合ロジック、Gemini パーサ
H-2. 統合テスト dryrun (0.5d) — apply --dry-run、ジョブ履歴記録
H-3. 監視 / レート制御 (0.25d) — Gemini quota / Maps quota Daily カウンタ + 429
H-4. ドキュメント (0.25d) — `docs/api/integration.md` `docs/api/ai.md`

合計 **約11.5人日**

## 5. 中継エンドポイント仕様一覧

| Method | Path | Auth | Body / Query | Response |
|---|---|---|---|---|
| GET | `/api/integration/status` | admin | — | `{ kaipoke, loginRemainSec, runningJob, lastSyncAt }` |
| POST | `/api/integration/expand` | admin | `{ month, dryRun? }` | `{ jobId }` |
| POST | `/api/integration/export` | admin | `{ month, format }` | `{ jobId }` |
| POST | `/api/integration/diff` | admin | `{ month }` | `{ sheetId, summary }` |
| POST | `/api/integration/apply` | admin | `{ sheetId, dryRun? }` | `{ jobId }` |
| GET | `/api/integration/jobs/:id` | admin | — | `{ id, status, phase, progress, items }` |
| POST | `/api/integration/jobs/:id/stop` | admin | — | `{ ok: true }` |
| GET | `/api/integration/jobs` | admin | `?limit&offset&type&status` | `{ jobs, total }` |
| PATCH | `/api/integration/job-items/:id` | admin | `{ manuallyHandled?, comment? }` | `{ item }` |
| GET | `/api/integration/correction-sheets/latest` | admin | `?month` | `{ sheet, items }` |
| GET | `/api/integration/correction-sheets/:id/items` | admin | `?type&include` | `{ items }` |
| PATCH | `/api/integration/correction-items/:id` | admin | `{ include?, comment?, manuallyHandled? }` | `{ item }` |
| POST | `/api/integration/correction-sheets/:id/items/bulk` | admin | `{ ids[], patch }` | `{ updated }` |
| GET | `/api/integration/vnc-url` | admin | — | `{ url, expiresAt }` |
| GET | `/api/integration/job-items/:id/screenshot` | admin | — | image/png stream |
| POST | `/api/ai/interpret` | admin/staff | `{ text, source }` | `{ actions, logId }` |
| GET | `/api/geocoding/forward` | admin/staff | `?address` | `{ lat, lng, formattedAddress, source }` |

### エラーレスポンス共通
- 409 `KAIPOKE_BUSY` — 同時実行中
- 502 `KAIPOKE_UNREACHABLE` — VPS 接続失敗
- 503 `KAIPOKE_LOGIN_EXPIRED` — セッション切れ
- 429 `RATE_LIMITED` — Gemini/Maps quota 超過

## 6. Gemini プロンプト案

### システムプロンプト
```
あなたは訪問看護スケジュール管理システム CareLink のAIアシスタントです。
ユーザーの自然言語入力を、以下の JSON スキーマに厳密に従って構造化してください。
解釈不能な場合は action_type を "unknown" にし confidence を 0 にしてください。

【今日の日付】{today_iso} ({weekday_ja}・第{iso_week}週)
【利用可能なスタッフ一覧】
{staff_list}
【利用可能な患者一覧】
{patient_list}

【出力スキーマ】
{
  "actions": [
    {
      "action_type": "staff_weekly_override" | "staff_event" | "visit_cancel"
                   | "visit_postpone" | "visit_add" | "unknown",
      "confidence": 0.0-1.0,
      "fields": { staff_id?, patient_id?, iso_week?, weekday?, time_start?, time_end?, reason?, from_date?, to_date? }
    }
  ]
}

複数アクションが含まれる場合は actions を複数返してください。
信頼度は: 完全一致 0.95+、推測 0.7-0.9、曖昧 <0.7。
```

### 例
**入力**: 「田中さん木曜の午前休み、火曜午後 管理者会議」
**出力**:
```json
{
  "actions": [
    { "action_type": "staff_weekly_override", "confidence": 0.92,
      "fields": { "staff_id": "S001", "iso_week": "2026-W18", "weekday": "thu", "time_start": "09:30", "time_end": "13:00" } },
    { "action_type": "staff_event", "confidence": 0.88,
      "fields": { "staff_id": "S001", "iso_week": "2026-W18", "weekday": "tue", "time_start": "13:00", "time_end": "18:00", "reason": "管理者会議" } }
  ]
}
```

### Gemini 設定
- model: gemini-1.5-flash
- temperature: 0.2
- response_mime_type: application/json
- response_schema: actions[] を JSON Schema 強制
- リトライ: 1回（5xx と JSON parse 失敗のみ）
- timeout: 15秒

## 7. CSP 更新 + 既存 kaipoke-api への変更

**変更箇所は最小1箇所のみ**。

### nginx の場合
```
add_header Content-Security-Policy "frame-ancestors 'self' https://carelink.kaipoke-api.net" always;
```

### Flask (flask-talisman) の場合
```python
csp = {
  ...
  'frame-ancestors': ["'self'", "https://carelink.kaipoke-api.net"],
}
```

ルーティング、Playwright、auto_apply.py 等は **触らない**。

## 8. テスト方針

### モック（必須）
- KaipokeClient: nock または respx で `/api/expand` `/api/diff` `/api/apply` のレスポンスをスタブ
- GeminiClient: SDK モック化、固定 JSON 応答
- GeocodingClient: 既知住所 → 既知座標のフィクスチャ

### ユニット
- CorrectionSheet 展開ロジック（delete+add → companion_change の境界条件）
- KaipokeJob status 集計
- VNC token 発行/検証（exp 切れ）

### 統合
- `/api/integration/diff` → `/correction-sheets/latest` → `/apply` の連続フロー（dryrun のみ実 VPS）
- Gemini 失敗時の `unknown` フォールバック

### E2E
- 実 kaipoke-api への呼び出しは **dryRun=true のみ**

## 9. 受入基準

- [ ] `/api/integration/*` 全 13 エンドポイントが Postman/Insomnia で疎通
- [ ] kaipoke-api の HTTP 409 が Frontend に `KAIPOKE_BUSY` Toast として伝播
- [ ] CSP 1行追加後、CareLink Frontend で noVNC iframe 表示
- [ ] Bearer Token / API Key が Frontend バンドルに含まれない（grep 確認）
- [ ] `/api/ai/interpret` がスタッフ/患者一覧をプロンプトに動的注入
- [ ] 複数アクション解釈で actions[] が 2件以上返るケース確認
- [ ] delete+add → companion_change 統合が CorrectionItem に正しく反映
- [ ] 失敗ジョブの manuallyHandled 切替で履歴 UI 即時更新
- [ ] Geocoding が同一住所 2回目以降キャッシュヒット
- [ ] Gemini quota / Maps quota 超過時 429
- [ ] dryRun 統合テスト全パス、ユニット 80%+

## 10. リスク + 対策

| リスク | 影響 | 対策 |
|---|---|---|
| Gemini API 解釈失敗 / 低信頼度 | AI入力が無効化 | confidence < 0.7 で「手動修正」UI へ強制誘導、unknown 時はテンプレ案内 |
| Gemini quota 枯渇 | 全 AI 入力停止 | Daily カウンタ + 429、staff/admin 別 quota、Google Cloud アラート |
| Maps API quota 枯渇 | 住所登録に座標が付かない | キャッシュ優先、失敗時は座標 null で登録継続 |
| kaipoke ログインセッション切れ | apply ジョブ全滅 | `/api/integration/status` の loginRemainSec を 1分 polling、5分以下で警告 Toast |
| kaipoke 同時実行 (HTTP 409) | 複数管理者の操作衝突 | Backend で in-flight Job 検知 → 即座に 409、Frontend は実行中ジョブ表示 |
| Bearer Token 漏洩 | kaipoke-api 第三者侵入 | Backend のみ保持、環境変数管理、ログ出力時マスク必須 |
| VNC token 流出 | iframe 不正利用 | 30分 TTL + audience=novnc、HMAC ローテーション |
| 修正シート JSON 構造のドリフト | apply 失敗 | `auto_apply.py` 側スキーマと共有 zod スキーマで Backend 再検証 |
| CSP 適用ミス | iframe ブロック | ステージングで frame-ancestors 検証、ロールバック手順をデプロイ手順書に明記 |
| Web Speech API 非対応端末 | iOS Safari 旧版で音声 NG | テキスト入力にフォールバック、UI で明示 |
| Frontend からの Geocoding 直叩き誘惑 | API key 漏洩 | Backend のみ提供、Maps JS SDK は本機能では不使用 |

## 11. 想定工数

| Phase | 内容 | 工数 |
|---|---|---|
| A | 基盤・KaipokeClient・スキーマ | 2.0d |
| B | Kaipoke 中継 | 3.0d |
| C | 差分プレビュー API | 1.0d |
| D | VNC URL + CSP | 0.5d |
| E | Gemini AI 入力 | 2.5d |
| F | Geocoding | 0.5d |
| G | スクリーンショット中継 | 0.5d |
| H | テスト・監視・ドキュメント | 1.5d |
| **合計** | | **約 11.5 人日（約 2.5 週間）** |

依存解決順: **A → B/C/F/G 並列可 → D → E（独立） → H**。Phase E は独立なので 2 名体制なら A 完了後並走可能（最短 7 営業日）。
