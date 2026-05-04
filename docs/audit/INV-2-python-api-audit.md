# INV-2: Python/Playwright API サーバ監査レポート

## 1. システム構成

### コンテナ内プロセス（supervisord 管理）
| priority | プロセス | 説明 | ポート |
|---|---|---|---|
| 100 | Xvfb | X11仮想フレームバッファ (1280x720x24) | (X11) |
| 200 | Fluxbox | ウィンドウマネージャ | (X11) |
| 300 | x11vnc | VNCサーバ | 5901 |
| 400 | noVNC/websockify | WebSocketラッパー | 6080 |
| 500 | Flask API | Pythonアプリ | 5000 |

`supervisord.conf:1-44`

### ボリュームマウント
```
./api_server.py     → /app/api_server.py  (ホットリロード)
./lib               → /app/lib
./commands          → /app/commands
./data              → /app/data           (CSV/JSON永続化)
./artifacts         → /app/artifacts      (スクショ)
./logs              → /app/logs
./state.json        → /app/state.json     (セッション)
./.env              → /app/.env
./credentials       → /app/credentials
./config            → /app/config
```

### ネットワーク
- 5000: Flask API（内部/CLI）
- 8443: Flask API（Cloudflare HTTPS）
- 6080: noVNC WebSocket
- shm_size: 2GB（Playwright用）

### 認証情報（.env）
```
KAIPOKE_CORP_ID=252650
KAIPOKE_USER_ID=sYgRH
KAIPOKE_PASSWORD=[MASKED]
```

state.json 永続化により Cookie/セッション再利用 (`lib/common.py:31-61`)、`normalize_text()` で全角→半角正規化 (`lib/common.py:21-28`)。

---

## 2. Flask API 全エンドポイント

### 認証不要

| パス | メソッド | 用途 | レスポンス |
|---|---|---|---|
| /api/status | GET | サーバー状態 | `{status, current_task, job, timestamp}` |
| /api/test | GET/POST | 接続テスト | `{message, server_time, api_version}` |
| /api/stop | POST | 非常停止 | `{success, message}` |
| /api/expand | POST | 月間展開（同期） | `{success, result}` |
| /api/export | POST | CSV出力（同期/非同期） | `{success, result}` or `{async}` |
| /api/export/result | GET | CSV出力結果ポーリング | `{status, result}` |
| /api/apply | POST | 差分適用（非同期） | `{async, message}` |
| /api/apply/result | GET | 適用結果ポーリング | `{status, result, progress}` |
| /api/diff | POST | 差分確認（Drive/CSV/file対応） | `{total_corrections, summary, corrections, csv_content}` |
| /api/diff/validate | POST | 差分データ検証 | `{valid, total_corrections}` |
| /api/config | GET/POST | Drive設定 | `{config}` |
| /api/drive/files | GET | Driveファイル一覧 | `{files, folder_id}` |
| /api/allocate | POST | 配置エンジン実行 | `{assignment_results, unassigned, summary}` |
| /api/allocate/debug | POST | 配置デバッグ | `{counts, samples}` |

`api_server.py:203-1080`

### Bearer 認証必須
認証: `Authorization: Bearer <KAIPOKE_API_TOKEN>` (環境変数、デフォルト "default-dev-token")

| パス | メソッド | 用途 |
|---|---|---|
| /api/kaipoke/run | POST | Playwright実行開始 |
| /api/kaipoke/stop | POST | Playwright停止 |
| /api/kaipoke/status | GET | ジョブ状態（VNC URL含む） |
| /api/kaipoke/logs | GET | ログ取得（tail対応） |
| /api/kaipoke/vnc-url | GET | VNC URL生成 |
| /novnc/verify | GET | noVNCトークン検証（認証なし） |

`api_server.py:1411-1641`

### 非同期ジョブ管理
グローバル状態 (`api_server.py:88-106`):
```python
job_state = {"id", "state", "progress", "started_at", "ended_at", "last_error", "mode"}
apply_result_store = {"result", "completed_at", "error"}
apply_progress = {"processed", "total", "phase", ...}
export_result_store = {...}
```
`threading.Lock` で `job_state_lock` 排他制御。

---

## 3. Playwright 自動操作の詳細

### ログインフロー (`lib/common.py:64-189`)
1. .env から認証情報読込
2. state.json 有効チェック（Cookie 存在 + 非空）
3. Cookie 有効なら復元、無ければ新規 context
4. ログインURL → ページ読込待機
5. エラーページチェック（"トップへ戻る"クリック）
6. ログインフォーム表示確認
7. フォーム入力（法人ID/ユーザーID/パスワード）
8. ログインボタンクリック
9. 待機中ポップアップクローズ
10. storage_state 保存

リトライ戦略 (`api_server.py:575-619`): `setup_yoriyori_page()` 最大3回再試行、SSO エラー時「トップへ戻る」後リトライ。

### expand（月間展開）(`commands/expand.py:180-310`)
1. setup_monthly_schedule_page() でセットアップ
2. ダイアログハンドラ登録（自動 OK）
3. 利用者ループ (MAX_USERS=200):
   - get_current_user_info(): ドロップダウン or タイトルから取得
   - has_next_user(): "次へ" 確認
   - expand_weekly_pattern(): "週間訪問パターンから展開" クリック
   - ダイアログ判定 → success/overwritten/skipped 記録
   - click_next_user(): networkidle 待機
4. 非常停止チェック

戻り値: `{success, skipped, failed, total, details: {new, overwritten}, users[]}`

### export（CSV出力）(`commands/export.py:174-276`)
1. setup_yoriyori_page() → 訪問看護画面
2. goto_export_page(): 上部ナビ → 出力対象選択
3. click_schedule_table(): スケジュール表クリック
4. set_export_month(): セレクト3つ（令和/年/月）
5. click_csv_export_button(): CSV出力 → expect_download()
6. ファイル保存
7. Drive 自動アップロード

戻り値: `{success, file_path, drive_file_id, csv_content, row_count}`

### auto_apply（差分適用）(`commands/auto_apply.py:2284-2700`)

**Phase 1: スケジュール修正（利用者別タブ）** (line 2430-2545)
- 修正シートを利用者でグループ化
- 各利用者:
  1. select_user(): ドロップダウン直接選択 O(1) or "次へ" フォールバック
  2. 日付ループ:
     - click_schedule_entry(): 日付・開始時間でエントリ特定
     - edit_schedule_time(): 6つのセレクト（hour/min）
     - edit_schedule_staff(): 異体字マッチング
     - edit_schedule_santei(): 算定時間（介護保険）
  3. 保存ボタン
  4. _check_form_errors() → スクショ保存

**Phase 2: イベント追加（職員別タブ）** (line 2547-2693)
- action="add" でフィルタ
- 職員別グループ化
- モーダル内フォーム入力
- _set_add_modal_date()（jQuery UI Datepicker）
- _set_event_time()
- 保存

エラーハンドリング:
- _remove_floating_overlays(): KARTE/ASP/チャット強制除去
- _safe_click(): タイムアウト → force+noWaitAfter → JS の3段階
- _click_with_scroll(): スクロール → click → force → JS onclick
- _save_debug_on_failure(): スクショ + HTML 保存

異体字マップ (`commands/auto_apply.py:45-66`):
```python
{"栁":"柳","﨑":"崎","髙":"高","濵":"浜","邊":"辺","廣":"広",
 "齋":"斎","澤":"沢","櫻":"桜","渡邉":"渡辺"}
```

---

## 4. noVNC ライブビュー

### VNCトークン管理 (`api_server.py:102-137`)
```python
vnc_tokens = {}  # {token: expiry_time}

def generate_vnc_token(ttl_minutes: int = 30) -> str:
    token = secrets.token_urlsafe(16)
    expiry = time.time() + (ttl_minutes * 60)
    vnc_tokens[token] = expiry
    return token

def validate_vnc_token(token: str) -> bool:
    if token not in vnc_tokens: return False
    if vnc_tokens[token] < time.time():
        del vnc_tokens[token]
        return False
    return True
```

### CSP ヘッダ (`api_server.py:153-159`)
```python
response.headers["Content-Security-Policy"] = (
    "frame-ancestors https://script.google.com https://*.googleusercontent.com"
)
```

iframe は GAS 側から埋込可能（X-Frame-Options 設定なし）

### VNC URL 発行フロー
1. POST /api/kaipoke/run（Bearer認証）→ job_state を running、Playwright バックグラウンド起動、VNCトークン生成 → URL 返却
2. noVNC 接続時: ?token=... で /novnc/verify GET → validate_vnc_token() で検証
3. WebSocket: noVNC port 6080 で受付 → port 5901 (x11vnc) フォワード

---

## 5. supervisord と Docker 起動順序

優先度順起動: Xvfb → Fluxbox → x11vnc → noVNC → Flask API

ヘルスチェック (`docker-compose.yml:38-43`):
```yaml
test: ["CMD", "curl", "-f", "http://localhost:5000/api/status"]
interval: 30s, timeout: 10s, retries: 3, start_period: 15s
```

ホットリロード: api_server.py + lib/ + commands/ ボリュームマウント → コンテナ外編集 → `supervisorctl restart api`

---

## 6. 「保護対象」境界

### ① 触ってはいけない（コア・機密）
- lib/common.py: ログイン/ナビ/月設定/state.json 管理
- commands/expand.py, export.py, auto_apply.py: Playwright スクリプト本体
- .env: 認証情報
- state.json: Cookie/セッション

### ② 触ってよい（周辺・拡張性）
- api_server.py エンドポイント定義（リクエスト/レスポンス形式の強化）
- lib/diff_engine.py（CareLink で再利用可能）
- lib/allocation_engine.py（独立モジュール化済み）
- lib/google_drive.py（認証情報別管理）
- action_filter, business_type_filter, target_users, limit パラメータ実装済み

### ③ リファクタ可能候補
- _safe_click(), _click_with_scroll() を共通ユーティリティに抽出
- _check_form_errors() を汎用化
- 異体字マッチング normalize_name() をキャッシュ化

---

## 7. CareLink 連携設計への影響

### ① 既存 API 仕様の流用

**そのまま使える**:
- /api/diff (CareLink最適化CSV → カイポケ現在CSV)
- /api/allocate (json input/output で独立)
- /api/export/result (ポーリング)

**要修正**:
- /api/apply: CareLink の操作対象が異なる可能性
- 認証: GAS 側 → CareLink ではユーザー識別追加要

### ② VNC iframe 埋込

**現状で可能**:
- /api/kaipoke/vnc-url（Bearer認証）で URL 発行
- CSP `frame-ancestors: https://script.google.com` 許可済

**CareLink 統合時**:
- frame-ancestors を CareLink ドメインに追加（環境変数化推奨）
```python
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "https://script.google.com")
CSP = f"frame-ancestors {ALLOWED_ORIGINS}"
```
- iframe src: `https://{VPS_HOST}/novnc/vnc.html?token={token}`

### ③ 同時実行制御の現状とリスク

現状 (`api_server.py:106, 252-258`):
```python
job_state_lock = threading.Lock()
with job_state_lock:
    if current_task["running"]:
        return jsonify({...}), 409  # Conflict
```

問題:
- 1 VPS インスタンスで 1 タスクのみ実行可能（複数は Queue 待ち）
- GAS + CareLink 同時リクエスト → 一方が 409 Conflict

対策:
1. **キュー実装**: Redis / RabbitMQ
2. **スケーリング**: replicas: 3 + ロードバランサ
3. **リソース管理**: 非同期ジョブ監視

---

## 総括

PlaywrightTest1 は **完全に自動化されたカイポケ RPA サーバ**として実装されており、強み：

| 項目 | 強度 |
|---|---|
| セッション管理（state.json） | 高（永続化 + リトライ） |
| エラーハンドリング | 高（3段階フォールバック + スクショ） |
| API 非同期対応 | 高（Cloudflare 524 対策済） |
| Drive 連携 | 高（CSV 自動アップロード） |
| 配置エンジン | 高（独立モジュール化） |
| VNC ライブビュー | 中（トークン認証 + CSP） |

**CareLink 統合時の最大課題**: 同時実行制御。現状 1 プロセス排他なので、GAS + CareLink 両用するならキュー + 複数インスタンス展開が必須。
