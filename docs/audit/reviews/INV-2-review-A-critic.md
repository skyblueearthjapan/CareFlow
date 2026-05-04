# INV-2 Python API 監査 クロスレビュー A（技術観点）

**VERDICT: ACCEPT-WITH-RESERVATIONS**

## 総評

INV-2 監査は PlaywrightTest1 コードベースの**徹底的かつ概ね正確な技術インベントリ**。エンドポイントリスト、supervisord 設定、Docker、VNC token 管理、Playwright 自動操作フローはすべてソースコードと照合済み。ただし、**いくつかの行参照誤りと関数名誤り**、加えて **重要なセキュリティ・アーキテクチャ懸念を識別はしているが過小評価**している。

## 重大な指摘

### [MAJOR-1] `edit_schedule_santei()` が名前付き関数として存在しない
- 監査: 「`edit_schedule_santei(): 算定時間（介護保険）`」
- 実際: `auto_apply.py` で `edit_schedule_santei` は grep で 0件
- 「算定時間」処理は大関数内のインラインロジック（line 1636-1670 周辺）、独立 callable 関数ではない
- `edit_schedule_staff()` も同様に存在しない
- **Fix**: 「staff selection は `apply_correction()` 内インライン、算定時間は `select#inPopupEstimate4` インタラクションで設定（line ~1636-1670）」と修正

### [MAJOR-2] エンドポイント line range `api_server.py:203-1080` が誤り
- 「認証不要」エンドポイントとして 203-1080 と記載
- 実際: `/api/test` は line 1080-1108、`/api/allocate` は 1113、`/api/allocate/debug` は 1365 で、すべて認証不要、line 1408 まで
- 認証ありの VNC ブロックは line 1411 から
- **Fix**: 認証不要エンドポイントの範囲を `203-1408` に訂正

### [MAJOR-3] `setup_yoriyori_page()` リトライ参照 `api_server.py:575-619` が誤り
- 監査: 「リトライ戦略 (`api_server.py:575-619`): `setup_yoriyori_page()` 最大3回再試行」
- 実際: line 575-619 は `run_apply_async()` の本体、`setup_yoriyori_page` ではない
- 実際のリトライロジックは `lib/common.py:557-619`
- **Fix**: 参照を `lib/common.py:557-619` に変更

## Minor Findings

1. `/novnc/verify` を「Bearer 認証必須」テーブルに「（認証なし）」注記つきで配置 → 認証なしテーブルに移すべき
2. `_click_with_scroll` は実際 4 段階（onclick 属性なし時の JS `.click()` フォールバックあり）
3. 異体字マップの実体は12エントリ（監査は10エントリのみ表示、`齊→斎` `渡邊→渡辺` 欠落）
4. auto_apply Phase 1 line 範囲が「2430-2545」と記載、実際 2429-2544（off by one）

## 不足項目（重要なセキュリティ観点）

- **x11vnc -nopw のセキュリティリスクが未言及** — `supervisord.conf:23` の `x11vnc -nopw -shared` を記録するのみ。VNC は password 保護ゼロ。port 5901（or 6080 via websockify）に到達できれば Flask token 検証を**完全にバイパス**。`/novnc/verify` の token check は purely advisory、underlying websocket は enforce しない
- **CORS `origins: "*"` が flagged されていない** — `api_server.py:76` で全 `/api/*` route に対して `*` 許可。認証なし `/api/apply` 等と組み合わせて重大な攻撃面
- **レート制限・abuse 防止が皆無** — `/api/expand`, `/api/export`, `/api/apply` は重い Playwright セッションをトリガ。単一悪意リクエストで `dry_run: false` の本番データ改変可能
- **`vnc_tokens` dict の thread safety** — `api_server.py:103` で lock なしで mutate。GIL は dict 操作を保護するが、cleanup loop（line 122-125）の iterate-and-delete は別スレッド read 中に行われる
- **VNC token URL 漏洩リスク** — query parameter に token、ブラウザ履歴・サーバログ・referrer に残留。CSP `frame-ancestors` だけでは保護不能

## Multi-Perspective

- **Executor**: §6 「保護対象/触ってよい」が粒度不足。`api_server.py` 全体を「触ってよい」とすると、セキュリティロジック（require_auth、validate_vnc_token）を破壊しうる
- **Stakeholder**: CareLink 統合策（Redis queue、replicas、LB）は significant infra work、bullet list で軽く扱われている。「current state」と「CareLink-ready」の差は文書が示唆するより大きい
- **Skeptic**: 「最大課題: 同時実行制御」は正しいが不完全。実は最大課題は **認証なし・無制限のデータ改変エンドポイント + CORS \*** の組合せ。CareLink 追加で攻撃面が倍

## Open Questions

- CareLink デプロイ計画は Cloudflare Tunnel が唯一のネットワーク層アクセス制御である前提か？ → そうなら VNC token system は websocket path に対して security theater
- `apply_progress` 変数 `api_server.py:592` は意図的にローカル変数か？ `global` 宣言なしで再代入されているように見え、ポーリングエンドポイント `/api/apply/result` が stale progress を読む可能性
