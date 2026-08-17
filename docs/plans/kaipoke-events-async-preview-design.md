# イベント取り込みプレビューの非同期化 + RPA networkidle 根治（設計）

作成: 2026-08-17。発端 = お客様報告「カイポケ取り込みで API 524
(`/api/v1/integrations/events-inbound-preview`)」（`Sampledata/kaipoke取り込みエラー.png`）。

## 0. 調査で確定した事実（本番証拠）

1. **直接原因（フレーク）**: RPA `commands/individual_tasks.py` の
   `page.wait_for_load_state("networkidle")`（裸・既定30s）が Playwright TimeoutError
   → API 500。同日 8/17 に今週分で1回・来週分で2回発生（過去ログでは初発）。
   失敗箇所は週切替より前のため「未来週だから」ではない。
2. **構造原因（524）**: FE(180s)→Cloudflare(**~100sで固定切断**)→BE(150s)→RPA(実測24〜138s)。
   RPA が 100s を超えると**成功していてもユーザーには 524**。実測で 106s 成功例あり。
   524 直後の再実行は RPA 単一スロットにより 409 busy になる二重の罠。
3. kaipoke_client は 5xx を1回リトライするため、RPA 失敗時は全体所要が約2倍化する。

## 1. 対策1 — RPA の networkidle 待ちをガード付きへ（フレーク根治）

対象: `PlaywrightTest1/commands/individual_tasks.py`（44行/62行の2箇所のみ。
他ファイルは `lib/common.py` の「try networkidle → 失敗時 domcontentloaded で続行」
パターン適用済みで、この2箇所だけが裸だった）。

- `_wait_reload(page)` ヘルパーを追加: networkidle 15s → 例外時 domcontentloaded 15s
  で続行 + 1500ms 静定待ち。2箇所を置換。
- 「続行」で壊れない根拠: 正しさは後続の値検証
  （currentDate 検証 / 「－」再検証 / ヘッダ日付レンジ検証 / 0件×btnIndividual 検証）が担保。
  networkidle は再読込ペーシングにすぎない。

## 2. 対策2 — プレビューの「起動→ポーリング」化（524 根治）

既存の型を複製する（新規発明しない）:
- RPA 側の型 = `/api/export` の `async:true` + `/api/export/result`（スレッド + result store）。
- BE/FE 側の型 = expand の「202 即返し + 後から回収」。

### 2-a. RPA (`api_server.py`)

- `individual_tasks_result_store = {result, completed_at, error, job_id}` を新設。
- `POST /api/individual-tasks` に `async: true` + `job_id`（呼び出し側発行の相関ID）を追加。
  async 時: store を **リクエストハンドラ内で** `{job_id}` 初期化（ポーリング競合の防止）
  → daemon スレッドで `run_individual_tasks` → store へ結果/エラー格納 → 即
  `{success, async: true, job_id}` を返す。**同期モードは無変更**（旧 BE 互換）。
- `GET /api/individual-tasks/result` 新設（export/result の複製）:
  running / completed / error / no_result + `job_id` エコー。
  running 判定 = `current_task.command == "individual_tasks"`。

### 2-b. CareFlow BE

- `kaipoke_client.py`: `individual_tasks_result()`（GET）を追加。
- スキーマ: `EventsInboundStartRead {jobId, status}` /
  `EventsInboundStatusRead {status: running|completed|failed, error?, preview?: EventsInboundPreviewRead}`。
- `POST /integrations/events-inbound-preview/start`（202・admin）:
  週バリデーション+eligibility は同期版と同一 → `KaipokeJob(op="events-preview",
  params.async=true, status=running)` 作成 → RPA async 起動（timeout 30s・busy は 409）
  → `{jobId}` 返却。
- `GET /integrations/events-inbound-preview/status/{job_id}`（admin・軽量 <1s）:
  - job.status=completed → `result_summary.preview` から返す（再取得不要・冪等）。
  - job.status=failed → failed + error。
  - job.status=running → RPA `/result` を照会:
    - running（job_id 一致 or 不明）→ running。
    - completed + job_id 一致 → `build_events_plan()` でプラン構築 → job を completed 化し
      **プラン全体を result_summary.preview に永続化**（後続ポーリングと監査のため。
      実測 130件 ≒ 26KB JSONB で許容）→ completed + preview。
    - error + job_id 一致 → job failed 化 → failed。
    - no_result / job_id 不一致（= RPA 再起動や別ジョブによる喪失）→ job failed 化
      → failed（「RPA側で結果が失われました。再実行してください」）。
- **旧 `POST /events-inbound-preview`（同期）は残す**: PWA の旧チャンク互換
  （デプロイ跨ぎで旧 FE が動く期間の後方互換。docstring に deprecated 明記）。

### 2-c. FE

- `lib/schemas/integration.ts`: Start/Status の zod スキーマ追加。
- `lib/queries/integrations.ts` `useEventsInboundPreview` の **mutationFn 内部だけ**を
  「start POST → 4s 間隔で status GET ポーリング（上限8分・一時的な通信失敗は
  連続3回まで許容）」へ差し替え。**フックの外部インターフェースは不変**
  （mutateAsync が EventsInboundPreview を返す）→ `useInbound.ts` と既存テストは無改修。

### 2-d. 範囲外（フォローアップ）

- smart-preview（訪問・実測45〜73s）の async 化は今回スコープ外。100s に迫った実測が
  出たら同じ型で対応（RPA 側は `/api/diff` 相当の async 化が必要）。

## 3. デプロイ順序（両方向安全）

1. **RPA 先行**: async は追加パラメータのみ → 現行 BE（同期呼び出し）に無影響。
2. **CareFlow BE+FE 同時**（通常デプロイ・migration なし）:
   - 新 FE + 旧 BE は起こらない（同一 compose）。
   - 旧 FE（PWA 旧チャンク）+ 新 BE = 旧同期エンドポイント存続で動作。

## 4. 検証

- RPA: 本番で `python main.py individual-tasks --date 2026-08-24`（read-only）を直接実行
  し 500 フレーク解消を確認。async 起動 + result ポーリングを curl で疎通確認。
- BE: `tests/test_kaipoke_events_inbound.py` に start/status の
  正常系・busy 409・RPA error・no_result 喪失・非月曜 422・完了後の冪等再取得を追加。
- FE: `pnpm tsc --noEmit` + 既存 KaipokeConsole テスト（クエリ層モックのため無改修で通る想定）。
- 本番: デプロイ後に連携ページの①取得を実行し、イベント差分が表示されることを確認。
