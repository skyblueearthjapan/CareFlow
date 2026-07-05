# 引き継ぎ書：カイポケRPA連携 復活プロジェクト（K-0〜通知まで）

作成 2026-07-05 / **本番 HEAD = `d9d3ab0`** / DB = **migration 0054** / healthz 正常。
**次のエージェントはまずこのファイルを読む。**

関連正典（順に読む）:
- `docs/plans/kaipoke-rpa-revival-survey.md` — K-0/K-0b 調査・疎通・セキュリティの一次記録
- `docs/plans/kaipoke-csv-generation-design.md` — K-1 の設計（18列CSV・データギャップ）
- 自動メモリ `careflow-kaipoke-rpa-revival`（索引先頭・最新状態を1行で追える）
- 思想の正典: `docs/plans/schedule-advisor-design.md` §6（余白の原則・予防/保全/救急）

---

## 0. TL;DR — このプロジェクトで何を成し遂げたか

**旧システム**（GitHub `skyblueearthjapan/PlaywrightTest1` = Python+Playwright RPA、`careflow-scheduler` = GAS）が
カイポケ（`r.kaipoke.biz`）へ自動入力していた仕組みを、**CareFlow02 に統合復活**させた。1セッションで
**構想 → セキュリティ是正 → 全パイプライン実装 → 実データ完走 → 現場向けUI → 見逃し防止通知**まで到達し、
すべて本番稼働中。

**完成した全チェーン（本番で実データ完走済み）**:
```
CareFlow で週生成＋スタッフ割当
  → ①カイポケ月間展開（月1回）
  → ②現況エクスポート＋週スコープ差分（diff-local）
  → ③コース別週ビューで確認
  → ④dry-run → 本番 apply（カイポケへ書込）
  → 失敗/スキップは管理者へ通知（見逃し防止）
すべてライブモニター(noVNC ページ内埋込)で目視しながら実行
```

「**アドバイザー＋一流の事務スタッフ**」の事務スタッフ側（カイポケ転記の自動化）が形になった。

---

## 1. 前提となる旧システムの構造（3層）

```
[GAS スプレッドシート]        [VPS: kaipoke-api コンテナ]         [カイポケ]
 UnifiedInput/Output   ─HTTPS─▶ Flask api_server.py   ─Playwright─▶ r.kaipoke.biz
 KaipokeRpaサイドバー   Tunnel   ├ Xvfb:99 + x11vnc:5901          (Firefox・JSF UI)
 監査ビュー(Audit*)              ├ websockify:6080 (noVNC)
                                └ Playwright headed実行
```
- `kaipoke-api` コンテナは **VPS上に今も存在**（`playwrighttest1-kaipoke-api` イメージ・2ヶ月無停止）。CareFlow02 の
  backend は Docker network `playwrighttest1_default`(alias `kaipoke-api`) で直結。
- 中核契約: **単一スロット**(同時実行1)・ジョブ状態はプロセス内グローバル変数。
  - `GET /api/status`（running/command）/ `POST /api/export`（async:true で即返→`/api/export/result`）/
    `POST /api/apply`（常に非同期→`/api/apply/result` に progress+details）/ `POST /api/expand`（**同期15-20分ブロック**）/
    `POST /api/stop`（引数なし・グレースフル）/ `GET /api/kaipoke/logs?tail=N`
- **展開(expand)は破壊的**: 旧 `expand.py:121` で「既に展開済みなら上書き確認ダイアログ→RPAが自動OK」。
  = 月2回目の展開は入力済みデータを消す。**月1回のみ**が鉄則。

---

## 2. 実施した全工程（時系列）

### K-0 疎通確認（非破壊）
- kaipoke-api 生存確認（healthy/idle）・state.json は3/8保存（セッション失効濃厚だが自動再ログイン機構あり）・
  api.log 全期間に apply/export の POST 0件＝**W4-A中継は実弾未検証**と判明。
- **監督付き dry-run（noVNC視聴）**: 読取専用 export 1回 → 52秒成功・**自動再ログイン成功**・
  カイポケUI/セレクタ経年変化なし・18列CSVフォーマット不変を実証。

### K-0b セキュリティ是正（重大）
発見: kaipoke-api の 5000/6080/8443 が **0.0.0.0 公開＋中核API無認証＋x11vnc -nopw** = インターネットから誰でも
無認証でカイポケ書込APIを叩けた。3層で封鎖:
1. **127.0.0.1 bind 化**（`/root/PlaywrightTest1/docker-compose.yml`）
2. **全 /api/* に Bearer 必須化**（PlaywrightTest1 commit `6685d9e`・`/api/status` のみ公開）＋トークンを
   `openssl rand -hex 32` でローテーション（`/root/PlaywrightTest1/.env` と `/opt/carelink/.env` 同期）
3. **Cloudflare Access**（ユーザー実施）: `kaipoke-api.net`/`novnc.kaipoke-api.net` を thousands.jp 4名のメールOTPで保護
→ 直IP遮断/Access/Bearer の3層。

### K-1a ジョブセンター＋ライブモニタリング
- `KaipokeClient` を実契約へ修正（存在しない /api/jobs/{id} 除去・単一スロット前提で
  export_result/apply_result/logs/stop 追加）
- `GET /integrations/live` — status+result+logs 統合スナップショット＋**遅延reconcile**（idle観測でrunning ジョブを
  completed/failed 確定・csv_content除去）。**MissingGreenlet バグを本番実弾で検出→commit後 re-select で修正**
- `GET /integrations/monitor-url`（正しい noVNC URL=novnc.kaipoke-api.net:6080）
- FE: ジョブセンター画面・適応ポーリング(実行中2s/待機15s)

### K-1b/c/d 18列CSV生成の基盤
実カイポケCSV577行分析で**ギャップは想定より小さい**と判明（サービス内容=定数「精神基本療養費Ⅰ・正看」・
職種=看護師/准看護師2値・業務種別=医療保険のみ）。
- **migration 0054**: `staff.qualification`(職種) / `office.kaipoke_name`(正式事業所名) /
  `patient.kaipoke_service_content`(サービス内容) — 全nullable
- `name_match.py`（NFKC＋異体字マップ髙→高・曖昧一致はNone）
- `csv_builder.py`（visits→18列・純関数＋DBオーケストレータ・diff/engine と厳密一致・cp932・ゴールデンテスト）
- `backfill_kaipoke_fields.py`（dry-run既定）→ **本番投入済**: office 2件正式名＋staff 5名職種。
  **要手当**: 髙梨 桂子（CareFlow未登録）
- **備考列に visit.note の内部メタデータ混入バグを本番検証で検出→修正**（remarks="")

### K-2a〜2d CareFlow内で差分完結＋apply書込
- **K-2a** `GET /integrations/generated-csv` — visits→18列CSV を API化
- **K-2b** `POST /integrations/diff-local` — 現況(kaipoke同期export)＋最適化(CareFlow生成)を
  `diff/engine.compare_schedules_from_content` で突合→CorrectionSheet化。差分の正が CareFlow visits に一本化
- **K-2c 週スコープ化（安全の要）**: 現況と最適化の**両方**を対象週の日(1-31)で絞る→**対象週外を削除しない**
  （旧GAS徹底調査で判明した核心。diff/engine に両側週フィルタは既存・パラメータ渡してなかっただけ）。
  本番実証: 月590(delete507)→週99(delete16)
- **K-2d apply書込解禁**: `item_to_kaipoke_correction` で CorrectionSheetItem(before/after)→カイポケ平坦
  correction_data(Correction(**item)復元の厳密キー)橋渡し。**安全弁**: dry_run既定True・applied済み再apply409・
  非同期のためsheet.status="applying"→reconcileでapplied/failed確定・未割当数を監査記録
- **dry-run apply 本番完走**（noVNC監視・10月サンドボックス）: 128件→成功125/失敗0/スキップ3(槇恵=未登録)・42分・
  **失敗0=形式橋渡し100%正確**・カイポケ書込ゼロ

### K-3 Step2 iframe モニター埋込
- LiveMonitorCard に noVNC を aspect-video で直接埋込（表示/隠すトグル・実行中バッジ・初回CFログイン用別窓）。
  novnc/carelink は同一サイト(kaipoke-api.net)でAccess Cookie有効・noVNC側frame制限無し・CF変更不要

### 週次反映UI＋集約再設計（現場フィードバック）
- **カイポケ連携画面を1枚のワークフローに集約**（旧ProcedureGuide/OperationMenuCard/CorrectionSheetView削除）。
  番号付き **①展開→②差分→③確認→④反映** を縦一列。週セレクタは上部・週ビューはボタンの下・実行ゲージは
  モニター直下（スクロール不要でモニターと一緒に見える）
- **コース別週ビュー**: 「行=コース(A/B/C/D)×列=曜日(月〜土)」（既存 CourseWeekOverview 踏襲）。BE week-schedule に
  visit.course_id→courses.code＋office＋weekday を join。週切替で連動
- **展開ガード**: `GET /integrations/expand-status?month`（KaipokeJob履歴で展開済み判定・pending含む）→
  展開済みは再展開ボタン無効・例外時のみ二重確認（取り消し/リセット用途）。差分が追加ばかりなら「展開まだかも」警告。
  取得失敗時は操作ブロック
- 改名「**カイポケ連携**」。サイドバー「連携」→ジョブセンター直結。サイドバー並び=拠点/スタッフ/患者

### apply失敗/スキップ通知（見逃し防止）
- 既存アプリ内通知基盤（Notification冪等・PC/モバイルのベル+未読バッジ）に **apply結果 producer** を追加:
  reconcile で本番apply が失敗/スキップ含み決着→admin/manager全員へ通知1件（type=kaipoke_apply_result・
  reference_id=job.id冪等・全件成功時は作らない）
- **確実性**: `POST /integrations/reconcile-jobs`（cron用）＋VPS cron `*/5 * * * *
  /usr/local/bin/carelink-kaipoke-reconcile.sh`（admin token都度発行・ASGI経由）→ 連携画面を開いてなくても
  5分以内に決着確定＋通知
- 画面: JobResultCard に「要対応-失敗/スキップ明細（利用者/日/操作/状態/理由）」

---

## 3. コード地図（主要ファイル）

**BE**:
- `backend/app/api/v1/integrations.py`（中核・全カイポケ中継＋live/reconcile/diff-local/apply/week-schedule/
  expand-status/reconcile-jobs/通知producer `_notify_apply_result`）
- `backend/app/services/kaipoke/`: `csv_builder.py`（18列生成）・`name_match.py`（名寄せ）・`local_diff.py`
  （現況×生成の差分＋item_to_kaipoke_correction橋渡し）
- `backend/app/services/kaipoke_client.py`（httpx・timeout上書き・export_result/apply_result/logs/stop）
- `backend/app/services/diff/engine.py`（旧移植・compare_schedules_from_content・両側週フィルタ）
- `backend/app/schemas/integrations.py`（LiveSnapshotRead/GeneratedCsvRead/WeekScheduleRow/ExpandStatusRead 他）
- `backend/alembic/versions/0054_kaipoke_csv_fields.py`
- `backend/scripts/backfill_kaipoke_fields.py`（dry-run既定）
- `backend/app/services/checkin/notify.py`（`_create_idempotent`/`_active_admin_manager_users` を通知に流用）

**FE**（`frontend/app/(app)/integrations/kaipoke/`）:
- `page.tsx`（カイポケ連携・集約）/ `_components/WeeklyApplyPanel.tsx`（ワークフロー本体・①〜④・展開ガード・確認ダイアログ）
- `_components/WeekScheduleView.tsx`（コース別週表）/ `WeekDiffView.tsx`（差分詳細）/ `LiveMonitorCard.tsx`（iframe埋込）/
  `JobProgressCard.tsx`（進捗ゲージ）/ `JobResultCard.tsx`（結果＋失敗明細）/ `ExecutionLogViewer.tsx` /
  `EmergencyStopButton.tsx` / `LiveStatusDot.tsx`
- `frontend/lib/queries/integrations.ts`（useKaipokeLive/useStartDiffLocal/useWeekSchedule/useExpandStatus/
  useStartApply/useStartExpand 他）/ `frontend/lib/schemas/integration.ts`
- `frontend/components/Sidebar.tsx`（「連携」→/integrations/kaipoke・並び順）

**旧リポジトリ（参照用・GitHub）**: `skyblueearthjapan/PlaywrightTest1`（RPAエンジン・security commit 6685d9e）/
`careflow-scheduler`（GAS・Audit監査ビュー）

---

## 4. 運用手順（週次反映・現場向け）

**前提**: CareFlow で対象週を生成し、**自動スタッフ割当まで済ませる**（未割当の訪問は csv_builder がスキップ）。

1. サイドバー「連携」→ カイポケ連携画面
2. noVNC ライブモニターを開く（初回は Cloudflare メールOTP・以降24時間有効）
3. 週セレクタで対象週を選ぶ
4. **①スケジュール展開**: その月が未展開なら「展開する」（月1回・約15-20分・noVNC で見守る）。展開済みなら不要
5. **②差分を計算**: 「この週の差分を計算」（現況取得込み・約1分）→ 追加/編集/削除/要確認
6. **③確認**: コース別週ビュー＋変更詳細で内容確認
7. **④反映**: 「dry-run で確認」（書込なし）→ 問題なければ「この週で本番反映」（不可逆・約40分）
8. 失敗/スキップがあればベル通知＋結果カードの明細 → カイポケ画面で手動対応

---

## 5. コミット一覧（本セッション・全て本番反映済み）

| HEAD | 内容 |
|---|---|
| `6685d9e`(PlaywrightTest1) | K-0b: 全/api/* Bearer必須化＋127.0.0.1 bind |
| `eae16c1` | K-1a ジョブセンター＋VNCモニタリング＋UI操作 |
| `7bcef66` | K-1a fix: reconcile後 re-select で MissingGreenlet 回避 |
| `dfee774` | サイドバー「連携」→ジョブセンター直結 |
| `4328a2d` | K-1b/1c/1d 18列CSV生成基盤（migration 0054） |
| `158e9b4` | K-2a generated-csv |
| `a0eda4e` | fix: 備考に visit.note 出さない |
| `63a6ca6` | K-2b diff-local（差分の正をCareFlowへ一本化） |
| `7932679` | K-2c 週スコープ化（対象週外を削除しない） |
| `9416ee8` | K-2d apply書込解禁（形式橋渡し＋安全弁） |
| `6d8a0d0` | fix: expand タイムアウトを running 扱い |
| `ae36bd0` | K-3 Step2 iframe モニター埋込 |
| `c93c5ed` | 週単位反映UI |
| `1f04291` | 週スケジュール表示＋手順ガイド＋改名 |
| `9342cc9` | 集約再設計（番号付き＋コース別＋展開ガード） |
| `f6391fc` | レイアウト調整（ゲージ/ボタンをモニター可視域に） |
| `c0be618` | サイドバー並び 拠点/スタッフ/患者 |
| `d9d3ab0` | apply失敗/スキップ通知（見逃し防止） |

---

## 6. VPS 実態（デプロイ・cron・トークン）

- `kaipoke-api` コンテナ: `/root/PlaywrightTest1`（別 compose project）。ソースはバインドマウント（git pull で反映）。
  ポートは全て 127.0.0.1 bind 済。トークンは `/root/PlaywrightTest1/.env` の `KAIPOKE_API_TOKEN`
- CareFlow: `/opt/carelink`（develop pull）。`.env` に `KAIPOKE_API_BASE_URL=http://kaipoke-api:5000`＋同じ TOKEN
- **cron**: `*/5 * * * * /usr/local/bin/carelink-kaipoke-reconcile.sh`（apply決着確定＋通知・ログ=
  `/var/log/carelink-kaipoke-reconcile.log`）。既存 `*/5 healthcheck-kaipoke.sh` もあり
- Cloudflare Tunnel: `kaipoke-api.net`→5000 / `novnc.kaipoke-api.net`→6080 / `carelink.kaipoke-api.net`→18000。
  Access は kaipoke-api/novnc の2ホストに設定（tunnel はリモート管理＝ダッシュボード操作が必要）
- デプロイ手順は `docs/HANDOFF.md`(旧)/`docs/deployment/runbook.md`。pg_dump→pull→build→(migrate)→recreate→healthz

---

## 7. 残作業（優先度つき）

### A. apply 実戦投入の前提（最優先・現場と）
1. **実 apply（dry_run=false）の初回**は PO/現場監督下・noVNC監視で。10月サンドボックスで一度通し、
   適用後に**再エクスポートで反映を検証**（旧GASの「適用後検証」相当・未実装）
2. **要手当データ**: 髙梨 桂子（staff未登録）・槇 恵（patient未登録）を CareFlow マスタへ登録 or 名寄せ。
   **全看護師の職種 backfill**（backfill は当該月CSVの6名のみ対象だった）

### B. 実装バックログ
3. **適用後検証（post-apply verification）**: apply後にカイポケを再export し、修正が本当に反映されたか1件ずつ照合
   （旧 GAS_APPLY_COMPLETION_SPEC 相当）。FAIL=手動対応リストへ
4. **失敗item のチェックオフ**: KaipokeJobItem に status/error_msg の受け皿はあるが apply は集計のみで item 行を作らない。
   `PATCH /job-items/{id}`(manuallyHandled) の FE 未実装。「未対応を潰し込む」動線（調査 案B）
5. **外部プッシュ通知**（LINE/Slack/メール）: 未実装（案C）。「アプリを開いていない時にも届く」要件が出たら。config に
   キー追加＋送信モジュール新規
6. **週次一括の効率化**: apply 128件で約42分（1件~20s）。週次運用の所要時間として要考慮。限定/並列化の余地
7. **月境界週の展開判定**（LOW）: expand-status は weekStart の月で判定。月末月曜の週は翌月分未展開を見落とす余地
8. **旧・月ベース残置API**（/v2/full-optimize 系）や legacy コンポーネントの掃除（PO確認とセット）

### C. データ基盤の残り（K-1 の未完部分）
9. サービス内容の療養費区分Ⅰ/Ⅱ/Ⅲ 自動判定は**未実装**（A案=患者単位の定数文字列で運用。実データにⅡ/Ⅲ皆無）
10. patient/staff のカイポケ側ID紐付けは**名寄せで代替**（恒久ID列は未追加）

---

## 8. 気になる点（リスク・要注意）

1. **展開の破壊性**: 月2回目の展開は上書き（データ消去）。展開ガード（expand-status＋二重確認）で防いでいるが、
   判定は KaipokeJob 履歴依存。**旧GASや手動で展開した月は記録が無く「未展開」と出る**→初回は「本当に初めてか」の
   確認を挟む設計。ここは運用で最も事故りやすい
2. **実 apply は未実施**: dry-run は完走したが、実書込(dry_run=false)は本番で一度も通していない。初回は必ず監督下で
3. **所要時間**: expand 15-20分・apply 40分（単一スロット・逐次）。長時間ジョブ中は他操作が 409 で弾かれる
4. **state.json セッション**: カイポケのログインセッションは自動再ログインで維持されるが、パスワード変更・
   CAPTCHA/2FA・Incapsula の挙動変化で失敗し得る。失敗時は旧手順（ローカルログイン→state.json を scp→restart）
5. **カイポケUIの経年変化**: JSF セレクタが変わると RPA が壊れる。dry-run で早期検知する運用を
6. **BE 既存 fail 群（環境依存）**: ローカル Python 3.14 で SQLAlchemy/UUID 由来の env fail（§D-19 相当・
   test_patients_v2/test_visit_v2/test_apply_requires 等）。base HEAD でも失敗する既存問題で本作業と無関係。
   **新規 integration/kaipoke テストは本番相当(3.12)で全pass**。切り分け時は `git stash` で base 比較
7. **reconcile-jobs cron の重さ**: 5分毎に `docker compose exec backend python`（app import）で python 起動。
   overhead はあるが許容範囲。頻度上げるなら注意
8. **通知の速報性**: アプリ内通知は 60秒ポーリング＋非アクティブタブで停止。cron で5分以内に生成されるが、
   「即時プッシュ」ではない。帰宅後の即時性が要るなら案C
9. **office名の表記**: week-schedule は `office.name`（稲毛/都賀）を返す。カイポケCSVは `office.kaipoke_name`
   （訪問看護ステーションよりより/都賀支店）。表示は稲毛/都賀（現場の見慣れた形）、CSV生成は正式名で正しく分離済み

---

## 9. プロセス規約（本セッションで踏襲）

- 体制: **実装 → code-reviewer 独立レビュー（自己approve禁止）→ 全指摘反映 → コミット → デプロイ → 本番実弾検証**。
  本セッションのレビューは複数回・すべて APPROVE か REQUEST_CHANGES→反映後承認
- **本番実弾検証がテストをすり抜けたバグを都度検出**（MissingGreenlet・備考混入・KaipokeClient契約不一致）。
  read-only/dry-run で安全に確認する文化
- 日本語ファイルは Edit/Write のみ（PowerShell Get-Content/Set-Content 禁止）
- 実値デザイントークン（`bg-warning-bg`/`text-warning-strong`/`bg-stone-950` 等。`bg-*/alpha` の var() は CSS不生成）
- BE: `python -m pytest -q -p no:warnings`（uv run 不可）・ruff check/format
- FE: `pnpm tsc --noEmit`・`pnpm lint`・`pnpm prettier`
- デプロイ: pg_dump → pull → build →（migrate = 一時コンテナ `run --rm backend alembic upgrade head`）→
  recreate → healthz（内外）。frontend 更新後は現場で Ctrl+Shift+R
- 破壊的/不可逆操作（展開・実apply・トークンローテ）は必ず確認・監督下

---

## 10. 次の候補（推奨順）

1. **要手当データの整備**（髙梨/槇 登録・全職種 backfill）→ apply の未解決を減らす
2. **実 apply 初回**（PO監督下・10月サンドボックス・noVNC監視・適用後検証つき）
3. **適用後検証**の実装（案B・失敗item チェックオフ）
4. 現場フィードバックでUI微調整（コース表の情報量・並び・折りたたみ）
5. 必要なら外部プッシュ通知（案C）
