# カイポケRPA連携 復活プロジェクト — 徹底調査レポート

作成 2026-07-05 / 調査対象: PlaywrightTest1 リポジトリ・careflow-scheduler リポジトリ・CareFlow02 本体・VPS実態
（レガシー2リポジトリの clone は scratchpad 配下。恒久参照は GitHub: skyblueearthjapan/PlaywrightTest1, skyblueearthjapan/careflow-scheduler）

---

## 0. 結論（最重要）

**「ゼロから作る」プロジェクトではない。** CareFlow02 には Phase 5-1 W4-A（commit `d424b8f`, 2026-05-05）で
カイポケ連携の中継層が既に実装済み（14 endpoints・KaipokeJob/CorrectionSheet モデル・差分エンジン移植・
integrations 画面・Docker network 接続）。VPS 上には旧 RPA 実行エンジン `kaipoke-api` コンテナ
（Flask + Playwright + noVNC）が存在し、backend は同一 Docker network（alias `kaipoke`）に接続済み。

したがって本プロジェクトの実体は:
1. **W4-A の再起動と実態確認**（2ヶ月放置分の生存確認）
2. **データギャップ4点の解消**（職種・サービス内容・保険種別・ID紐付け）
3. **「CareFlow DB → カイポケ18列CSV」生成の新規実装**（最大の欠落）
4. **3点モニタリングの CareFlow UI 再構築**（VNC埋込は旧GASでも未完 — 初めて完成させる好機）
5. **適用後検証と残件ワークリスト**（人間補完のタスク化）

---

## 1. 旧システム全体像（3層構成）

```
[GAS スプレッドシート]                [VPS: kaipoke-api コンテナ]           [カイポケ]
 UnifiedInput/Output UI    ──HTTPS──▶  Flask api_server.py      ──Playwright──▶ r.kaipoke.biz
 KaipokeRpaSidebar         Cloudflare  ├ Xvfb:99 + x11vnc:5901              (Firefox・JSF UI)
 監査ビュー(Audit*)         Tunnel      ├ websockify:6080 (noVNC)
 InteractiveWeekView                   └ Playwright headed実行
        │                                      │
        └────── Google Drive CSV 共有 ─────────┘
        (kaipoke_current / gas_optimized / diff_result)
```

- 呼び出し規約: GAS `UrlFetchApp` + `muteHttpExceptions`。長時間ジョブは HTML 側 `setInterval` ポーリング
  （GAS 6分制限・Cloudflare 524 回避の苦肉の策）
- ジョブ状態は **Flask プロセス内グローバル変数のみ**（`job_state` / `apply_progress` / `*_result_store`）。
  永続化なし・同時実行1つ（409 Busy）
- 認証は `/api/kaipoke/*` の5本のみ Bearer。中核 API（apply/diff/export）は**無認証**（Tunnel 前提）
- セッション: `state.json`（カイポケ cookie）。切れたら**ローカルでログイン→scp→restart** の手動運用

### 運用フロー（人間の動線）
1. 管理者が入力管理（UnifiedInput）で入力 → **InputAudit**（入力データ監査）で NG/WARN を補正
2. Python 割当エンジン実行 → 割当結果シート生成
3. **Audit 監査ビュー**（○△×）で品質確認 → NG は InteractiveWeekView でドラッグ手修正
4. RPA サイドバー: 展開 → CSV出力 → **差分確認**（`diff_verified` フラグが立たないと適用不可）→ **差分適用**
5. **適用後検証**: カイポケCSV再エクスポート → 修正1件ずつ照合 → FAIL 行 = 人間がカイポケで手作業補完

---

## 2. 「3点モニタリング」の実装実態（旧システム）

### ① リアルタイムモニタリング = 3系統併用
| 系統 | 実装 | 状態 |
|---|---|---|
| **VNC 実画面視聴** | Xvfb→x11vnc→websockify(6080)→noVNC。`/api/kaipoke/vnc-url` がトークン付きURL発行（TTL 30分・`secrets.token_urlsafe`）。CSP `frame-ancestors script.google.com` | **サーバ側完成・GAS UI 未接続**（サイドバーから呼ばれていない） |
| 進捗ポーリング | `/api/apply/result` → `apply_progress{processed, total, phase, current_name, success, failed, skipped}`。エンジン側は `progress_callback` で抽象化済み | 稼働していた |
| ログポーリング | `add_log()` → `deque(maxlen=5000)` → `/api/kaipoke/logs?tail=N` | 稼働していた |

サイドバー側には停滞検知（10分無進捗でタイムアウト）・通信エラー30連続で中断・60分ハードタイムアウトあり。

### ② 最終実行結果の提示
`run_auto_apply` の result dict（フィールド安定・そのまま Pydantic 化可能）:
```
{ total, schedule_total, event_total, success, failed, skipped,
  warnings[]（unassigned_staff 等）, details[]（1件ごと user/staff, date, action,
  business_type, status: success|failed|skipped|error, reason）,
  execution_time_sec, completed_at, stopped }
```
GAS は「適用結果」シートに9列で全件書込・色分け（緑/赤/黄）＋アラート表示。
reason コード: `user_not_found` / `staff_not_found` / `staff_tab_navigation_failed` / 例外文字列。

### ③ 差分確認と人間補完 = 二段構え
- **適用前**: `verifyDiffResult`（8チェック: 行数一致・追加利用者の実在・アクション合計・業務種別整合等）が
  NG なら適用ブロック。`staff1_to="未割当"` は事前警告（「職員未選択'-'で登録されます」）
- **適用後**: 再エクスポート照合で 1 correction ごとに OK/FAIL/skipped 判定
  （add=存在確認・delete=不在確認・edit=変更後値で存在・date_change=移動先存在＋移動元不在）。
  **FAIL 行 = 人間がカイポケ画面で手作業補完する残件リスト**
- 失敗時の証跡: `artifacts/` にスクリーンショット＋HTML 自動保存

### 実行エンジンの挙動（auto_apply.py）
- 2フェーズ: Phase1 利用者別タブ（delete→date_change→edit→add の順で処理）/ Phase2 職員別タブ（イベント）
- 1件失敗しても継続。Phase1 のみリトライ1回（セッション復旧つき）
- 非常停止: `/api/stop` → `.stop_requested` ファイル + threading.Event → 処理中の1件完了後に
  グレースフル停止（`result.stopped=true`）。GAS 側は独立モードレスの巨大赤ボタンダイアログ
- 氏名マッチング: `normalize_name()`（栁→柳・髙→高等の漢字ゆれ + 全半角スペース正規化）+ 部分一致

---

## 3. CareFlow02 の受け皿（既にあるもの）

| # | 資産 | 場所 |
|---|---|---|
| 1 | カイポケ中継 API 14本（status/expand/export/diff/apply/stop/jobs/correction-sheets、全 admin 限定） | `backend/app/api/v1/integrations.py` |
| 2 | httpx クライアント（Bearer 自動付与・30s timeout・5xx retry 1回・409→KaipokeBusyError・test seam） | `backend/app/services/kaipoke_client.py` |
| 3 | ジョブ DB モデル（status 遷移・result_summary・upstream jobId） | `backend/app/models/kaipoke_job.py` |
| 4 | 差分シート DB モデル + coalesce（delete+add ±1日→companion_change） | `backend/app/models/correction_sheet.py` |
| 5 | 18列CSV 差分エンジン（旧 lib/diff_engine.py 移植済・business_type/service_type/職種を内部表現に保持） | `backend/app/services/diff/engine.py` |
| 6 | visits に `kaipoke_id` 列（訪問単位の突合キー） | `backend/app/models/visit.py:118` |
| 7 | integrations 画面（ジョブ一覧・差分プレビュー・apply モーダル） + React Query ポーリング | `frontend/app/(app)/integrations/kaipoke/` |
| 8 | プレビュー→検証→apply の確立 UI パターン（5段ステート・必須確認・409再計算・state_token） | `BulkPoolInsertDialog.tsx` ほか |
| 9 | admin RBAC ガード・監査ミドルウェア（全 mutation 自動記録）・通知/トースト基盤 | `deps.py` / `middleware/audit.py` |
| 10 | Docker external network 接続（backend ⇄ kaipoke-api、alias `kaipoke`） | `docker-compose.production.yml:85-152` |
| 11 | 旧システム全仕様の一次資料（監査レポート群 INV-1〜5 + MASTER） | `docs/audit/` |
| 12 | 氏名正規化の萌芽（全半角スペース正規化） | `backend/scripts/convert_schedule_frame_to_pfv.py:259` |

VPS 実態（2026-05-05 snapshot）: `kaipoke-api` コンテナが port 5000/6080/8443 で稼働。
cloudflared に `kaipoke-api.net`（API）と `novnc.kaipoke-api.net`（VNC）の hostname が既存。
W4-A は本番で expand→export→diff→apply のフルフロー dry-run を1回通過済み。

---

## 4. ギャップ分析（新規に作る・直すもの）

### A. データモデルのギャップ（18列CSVを「書く」側の欠落）
差分エンジンは18列を読めるが、**CareFlow DB から18列CSVを生成する元データが不足**:
1. **スタッフの介護職種**（サ責/ヘルパー/看護師等）— `staff.role` はシステムロールのみ
2. **サービス内容マスタ** — 該当モデルなし
3. **visit 粒度の業務種別**（医療保険/介護保険/イベント）— `patient.insurance` はあるが visit 単位で未確定
4. **patient/staff のカイポケ側ID紐付け** — visit.kaipoke_id のみ。恒久名寄せ（漢字ゆれ含む）が必要
5. **DB→18列CSV 生成ロジックそのもの**（旧 KaipokeExport.js 相当）— 未実装。
   旧фローの「最適化CSV」は GAS 割当結果由来 → 今は **CareFlow visits が正典**。ここが本プロジェクトの心臓部

### B. モニタリング UI のギャップ
6. **VNC 埋込ビュー** — サーバ側は完成済みだが UI は旧GASでも未接続。CareFlow で初めて完成させる
7. **進捗表示** — kaipoke-api の `apply_progress` を中継してポーリング表示（既存 refetchInterval に相乗り可。
   SSE 化は任意）
8. **適用後検証フロー** — W4-A 未実装。再エクスポート照合 → OK/FAIL 一覧
9. **残件ワークリスト** — failed/skipped/FAIL/未割当を「人間が補完すべきタスク」として永続化・完了チェック
   （旧: スプレッドシートの赤黄行。新: DB テーブル + 通知）

### C. 実行エンジン側の技術負債（kaipoke-api 側）
10. ジョブ状態がプロセス内メモリのみ・ジョブID体系不統一 → 再起動で消失。CareFlow 側 KaipokeJob が
    真実源になるよう突合を固める（または将来エンジンごと CareFlow 管理下に）
11. 中核 API 無認証の非対称 → Bearer を全エンドポイントに統一
12. `state.json` 手動 scp 運用 → ログイン切れ検知と再ログイン導線の設計
13. Python 3.14 等の環境更新・Playwright セレクタの経年劣化（カイポケ UI 変更リスク）

### D. 思想の正典との整合（schedule-advisor-design.md §6）
- カイポケ転記は「予防/保全/救急」のどれでもない**第4の役割 = 事務代行（転記）**。患者の予定を
  動かす機能ではなく、CareFlow 内で確定済みのスケジュールを外部システムへ写す作業 → 余白の原則と衝突しない
- ただし **apply は外部システムへの不可逆書込**。既存の「プレビュー→明示確認→apply→検証」パターンと
  「Ctrl+Z 対象外の明示」を必ず踏襲。dry-run を第一級市民として UI に残す
- 詰まり解消の適用範囲は閉じたまま（本プロジェクトはそこに触れない）

---

## 5. 未知数（Phase 0 で実地確認すべきこと）

1. **kaipoke-api コンテナは今も生きているか**（snapshot は 2026-05-05。`GET /api/status` 疎通確認）
2. **state.json のセッションは生存しているか**（カイポケ側パスワード変更・セッション失効の可能性）
3. **カイポケ UI は変わっていないか**（JSF セレクタ・ナビゲーション経路。dry-run で確認）
4. W4-A の integrations 画面は現 develop で正常動作するか（2ヶ月分の変更との噛み合わせ）
5. 旧 GAS 運用は現在も動いているか・並行稼働の必要はあるか（PO 確認）
6. `/api/diff` の「最適化CSV」入力を W4-A が何から作っているか（現状はおそらく手動アップロード or
   kaipoke-api 側 Drive 依存 — CareFlow visits 由来に置き換える設計判断が必要）

---

## 6. 推奨ロードマップ（Wave 案）

| Phase | 内容 | 種別 |
|---|---|---|
| **K-0** | 実地確認: kaipoke-api 疎通・state.json 生存・dry-run 1周・W4-A 画面動作・PO へ運用ヒアリング | 調査 |
| **K-1** | データギャップ解消: 職種/サービス内容/保険種別/カイポケID の migration + マスタ画面 + 名寄せ | 基盤 |
| **K-2** | **CareFlow visits → 18列CSV 生成**（export 側）+ 差分の物差しを CareFlow 正典に統一 | 心臓部 |
| **K-3** | ジョブセンター UI: VNC 埋込 + 進捗 + ログ + 非常停止（3点モニタリングの①） | UI |
| **K-4** | 結果提示 + 適用後検証 + 残件ワークリスト（3点モニタリングの②③ — 人間補完のタスク化） | UI/運用 |
| **K-5** | 運用固め: 認証統一・セッション管理・監視 cron・ロールバック方針・マニュアル | 固め |

設計判断（PO 相談ポイント）:
- 差分の「正」は CareFlow visits とする（旧: GAS 割当結果）— 転記方向の一方向化
- apply の単位（週次か月次か）と実行タイミング（毎週の型に組み込むか）
- VNC 画面の公開範囲（admin のみ想定）
- 旧 GAS との並行期間の有無・切替条件

---

## 7. K-0 疎通確認 実測結果（2026-07-05・全て非破壊 = 読み取り専用コマンドと副作用なし GET のみ）

### 生存確認 — ✅ インフラは全て生きている
| 項目 | 結果 |
|---|---|
| kaipoke-api コンテナ | **Up 2ヶ月（2026-04-17 起動）・healthy・RestartCount 0**。supervisord 5プロセス（xvfb/fluxbox/x11vnc/novnc/api）全て RUNNING |
| `GET /api/status`（コンテナ内） | 200。`job.state=idle`・`current_task.running=false`・`stop_requested=false` |
| noVNC（:6080/vnc.html） | 200 |
| Cloudflare Tunnel 外部経路 | `https://kaipoke-api.net/api/status` = 200 / `https://novnc.kaipoke-api.net/vnc.html` = 200 |
| backend → kaipoke-api 内部経路 | carelink-backend コンテナから `http://kaipoke-api:5000/api/status` = **200**（network 接続健在） |
| CareFlow 設定 | `/opt/carelink/.env` に KAIPOKE_API_BASE_URL=`http://kaipoke-api:5000`・TOKEN・EXPORT_DIR/TTL 設定済み |
| CareFlow 中継ルート | `GET /api/v1/integrations/status` = **401**（未認証拒否 = ルート配線済み・admin ガード動作） |
| W4-A テーブル | 本番 DB に kaipoke_jobs / kaipoke_job_items / correction_sheets / correction_sheet_items **実在・全て 0 行** |
| 本番フロント | `/integrations/kaipoke` = 307（ログインリダイレクト = ページ存在） |
| ホスト名の正 | compose の `kaipoke` は **network 名**。コンテナ到達は `kaipoke-api:5000`（設定値と一致） |

### 休眠の実態 — RPA 本体は約4ヶ月動いていない
- `state.json`（カイポケセッション）は **2026-03-08 11:02 保存が最後**。カイポケ認証系
  （JSESSIONID/SSOID/memberInfo）は session cookie でサーバ側失効はほぼ確実。
  ただし `.env` に KAIPOKE_CORP_ID/USER_ID/PASSWORD が存在し、エンジンには自動再ログイン機構
  （`lib/common.py` login/ensure_session）があるため、**次回実行時に再ログインを試みる設計**
- api.log（3月下旬〜現在の全期間・46MB）に **POST /api/apply・/expand・/export・/diff・/kaipoke/run が 0 件**。
  実際に使われていたのは `/api/allocate`（GAS 割当エンジン）のみで **2026-04-18 01:28 が最後**。
  その後は 2026-05-04 21:50 の `POST /api/test`（W4-A 接続テスト）1回と healthcheck だけ
- **W4-A 記録との齟齬**: W4-A 文書は「本番で dry-run フルフローを1回通した」と記すが、
  本ログ期間に該当 POST が皆無・W4-A テーブルも 0 行。**中継フローは実弾の RPA エンジンに対して
  実質未検証**とみなして進めるべき

### ⚠️ セキュリティ発見（復活着手前に是正すべき）
1. **kaipoke-api のポート 5000/6080/8443 がホストの 0.0.0.0 に公開**されている
   （carelink 系は全て 127.0.0.1 bind なのと対照的）。中核 API（apply/stop/allocate 等）は**無認証**のため、
   インターネットから `72.60.211.213:5000` へ直接叩ける状態。x11vnc は `-nopw`（websockify:6080 も公開）
2. x11vnc_err.log（6/23 更新）・novnc_err.log（7/5 更新）に接続痕跡 — 外部スキャナの可能性
3. compose の `KAIPOKE_API_TOKEN=your-secure-token-here`（プレースホルダのまま）

**→ K-0b 是正実施（2026-07-05）**: `/root/PlaywrightTest1/docker-compose.yml` の 5000/8443/6080 を
127.0.0.1 bind に変更しコンテナ再生成（バックアップ = `docker-compose.yml.bak-20260705`）。
検証済み: 外部直叩き3ポートとも遮断・Tunnel 経由 200・backend 内部経路 200・state.json 無傷・healthy。
**→ K-0b 認証統一 実施済み（2026-07-05・PlaywrightTest1 commit `6685d9e`）**:
- `api_server.py` に before_request フックで **全 /api/* に Bearer 必須化**（公開維持は healthcheck 用
  `/api/status` のみ）＋プレースホルダトークン検出時の起動警告
- compose: ports 127.0.0.1 bind をリポジトリに正式反映・`KAIPOKE_API_TOKEN=${...:?}` で .env 注入必須化
- トークンを `openssl rand -hex 32` でローテーションし `/root/PlaywrightTest1/.env` と
  `/opt/carelink/.env` に同期（値は画面非表示のまま書込）。kaipoke-api・carelink-backend 両方再生成
- 検証済み: 無認証 /api/test・/api/apply = **401**（ローカル・Tunnel 経由とも）／トークン付き = 200／
  /api/status = 200（healthy 維持）／backend→kaipoke-api 認証済み経路 = 200／healthz 内外 = ok／
  pg_dump バックアップ = `pre-deploy-20260705-0602.sql.gz`
- **Cloudflare Access 付与済み（2026-07-05・ユーザー実施・検証済み）**: `kaipoke-api.net` と
  `novnc.kaipoke-api.net` の両宛先が Self-hosted アプリとして保護され、未ログインアクセスは
  `skyblue2025.cloudflareaccess.com` へ 302（外部から実測）。許可 = thousands.jp の管理者4名（メール OTP）。
  内部経路（backend→kaipoke-api 200）・healthz 無影響を確認。
  **これで露出は完全閉鎖**: 直IP=遮断 / Tunnel=Access ログイン必須 / API=Bearer 必須 の3層

### 追加発見（K-1 で修正すべき契約不一致）
`KaipokeClient` は `GET /api/jobs/{id}`・`POST /api/jobs/{id}/stop` を呼ぶが、**Flask 側に該当
エンドポイントが存在しない**（実際は `/api/apply/result`・`/api/export/result`・`/api/stop`）。
W4-A 中継が実弾未検証である具体的証拠。K-1 でクライアント側をポーリング API の実パスに合わせて修正する。

### 監督付き dry-run 実施結果（2026-07-05 15:25 — ✅ K-0 完全クローズ）
読み取り専用 `/api/export`（month=2026-07・async・headed）を1回実行。**ユーザーが noVNC でリアルタイム視聴**
（3点モニタリング①の初稼働 — 旧 GAS では未実装だった導線）。
- **52秒で成功**（15:25:24→15:26:16）。`data/current_202607.csv` = 92,328 bytes・578行
- **自動再ログイン成功** — 4ヶ月失効セッションから .env 認証情報で再確立。CAPTCHA/2FA の障壁なし。
  state.json が 2026-07-05 15:25 に更新（新セッション保存）
- **カイポケ UI/セレクタの経年変化なし** — ログイン→レセプト→訪問看護→出力ページ→令和年月設定→CSV DL の
  全経路が無修正で通った
- **18列CSVフォーマット不変**（cp932・職員名1..職種..事業所名..業務種別..の既知ヘッダを確認）
- 未解決の残件: 旧 GAS 運用の現状ヒアリング（4/18 以降 allocate も停止 — 完全移行済みか PO 確認）のみ

## 8. 参照資料

- 旧仕様一次資料: `docs/audit/INV-1-gas-app-audit.md`（GAS/RPA 全仕様）・`INV-2〜5`・`MASTER-AUDIT-REPORT.md`
- W4-A 実装記録: `docs/plans/wave/W4-A-kaipoke-integration.md`
- VPS 実態: `docs/deployment/vps-state-snapshot.md`
- レガシーコード: GitHub skyblueearthjapan/PlaywrightTest1（RPA エンジン）・careflow-scheduler（GAS 側。
  Audit*/InputAudit* の監査設計は docs/AUDIT_VIEW_*.md 5部作が仕様書）
- 思想の正典: `docs/plans/schedule-advisor-design.md` §6
