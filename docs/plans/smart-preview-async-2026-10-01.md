# 取込プレビュー（訪問の読み込み）のバックグラウンド化 — 2026-10-01

## 1. 背景

- 月を跨ぐ週（次は 10/26 の週）は、統合プレビュー（smart-inbound-preview）がカイポケの export を 10 月・11 月の 2 本、直列に回します。1 本およそ 50 秒なので、リクエスト全体でおよそ 100 秒かかり、Cloudflare の約 100 秒の制限で 524 になります。
- らく助側の処理は 1.1 秒ほどで、時間のほとんどは RPA の待ちです。
- PO の承認を得て、イベント取込（2026-08-17）と予実比較で使っている「ジョブを立てて 202 を返し、画面はポーリングする」形に揃えました。

## 2. やったこと

### バックエンド（`backend/app/api/v1/integrations.py`）

| エンドポイント | 役割 |
|---|---|
| `POST /integrations/smart-inbound-preview/start` | ジョブを `running` で立てて、すぐ 202 + `jobId` を返します。本体は `BackgroundTasks` で実行します。 |
| `GET /integrations/smart-inbound-preview/status/{job_id}` | 状況（running / completed / failed）を返します。completed のときは `preview` に同期版と同じ `SmartInboundPreviewRead` が入ります。RPA には触りません。 |
| `GET /integrations/smart-inbound-preview/active?weekStart=` | その週で動いているジョブがあれば返し、なければ null を返します（画面へ戻ったときの再開用）。 |
| `POST /integrations/smart-inbound-preview`（従来） | 旧画面のために残しています。挙動は変えていません。 |

- 本体の処理（export → 差分シート作成 → 置換の dry-run）は `_compute_smart_preview` に切り出し、同期版とバックグラウンド版が同じ処理を通ります。中身（日の分類・跨ぎ date_change の見送り・置換の対象日）は変えていません。
- バックグラウンド版（`_run_smart_preview_job`）は自前のセッションで動き、どの経路で失敗してもジョブを `failed` にして、`result_summary.error` に日本語の理由を残します。
- 完了時の `result_summary` は、同期版と同じ要約に `preview`（画面に返す形そのもの）を加えたものです。画面は `preview.sheetId` を持って ❸取り込む（`smart-inbound-apply`）へ進みます。**apply の入力と挙動は変えていません**（apply は実行時に分類と取得をやり直す作りのままです）。
- ジョブの `params` は `op: "smart-preview"` に `async: true` を加えたものです。履歴の表示名（「取込プレビュー」）は同じです。
- 守り（予実比較と同じ）
  - 月曜日でない・取り込みゲートが閉じている → 202 の前に 422 を返します。
  - RPA が実行中 → 409「kaipoke busy」。
  - 統合プレビューが既に動いている → 409（どの週のものかと、取消の案内を文言に入れています）。
  - 10 分を超えて `running` のもの（プロセスの再起動で残った残骸）→ 開始時・status・active のいずれでも `failed` に倒します。
  - 実行中に取消されたジョブは、作った差分シートを残さずに終わります（`cancelled` を上書きしません）。
- 汎用の後始末（`_reconcile_latest_job`）の除外に `smart-preview` を加えました。月を跨ぐ週は export 2 本の合間に RPA が空いて見えるため、除外しないと結果の無い completed で先に閉じられてしまいます。
- migration はありません（`KaipokeJob.result_summary` に入れています）。

### フロントエンド

- `useSmartInboundPreview`（`frontend/lib/queries/integrations.ts`）: start → status を 3 秒ごとに確認します。**全ての週**で使います。`mutateAsync` が完成したプレビューを返す形は従来どおりなので、連携画面と運転席（突合）の両方がそのまま非同期になります。
  - 一時的なポーリングの失敗は 2 回まで許します。待ち受けの上限は 12 分です（サーバーは 10 分で残骸として失敗にします）。
  - `resumeJobId` を渡すと、新しく開始せずにそのジョブを待ち受けます。
  - 画面を離れる（アンマウント）と待ち受けをやめ、`SmartPreviewDetached` の印で終わります。ジョブはサーバーで続きます。呼び出し側はこれを失敗として出さず、後続のイベント取得も始めません。
- `useActiveSmartInboundPreview`: 画面を開いたとき・週を切り替えたときに、その週の実行中ジョブを確かめます。
- 連携画面（`useInbound` / `InboundControls`）
  - 読み込み中は「カイポケから予定を読み込んでいます（1〜2 分かかることがあります）。この画面を離れても読み込みは続きます。」を出します。
  - 画面へ戻ったときに実行中ジョブがあれば待ち受けを再開し、結果を今までどおり表示します（再開時は「前回の読み込みを再開しました。」を添えます）。再開は訪問だけで、イベントの取得は始めません。
  - 失敗したら BE の理由（`API 409 …` ではなく detail）を出し、「もう一度読み込む」で訪問だけを読み込み直せます。
  - プレビューの表示（SmartPlanPanel・チップ類）は変えていません。

## 3. テスト

- `backend/tests/test_smart_preview_async.py`（16 件）: 202 → 完了（同期版と同じ形・sheetId で apply できる）、月曜日以外の 422、export 失敗・0 件・途中の busy・想定外の例外での failed、RPA 実行中の 409、二重起動の 409、残骸の掃除（開始時・status）、取消済みの扱い、他の op の 404、active（再開用）、admin 以外の 403、月跨ぎ週の計測。
- 計測（`test_month_crossing_week_returns_immediately`）: 10/26 の週で、スタブの export に 1 本 2 秒の遅延を入れても、start は 1 秒未満で 202 を返し、リクエスト中は export を 1 本も撃ちません。本体を後から実行すると、10 月 → 11 月の 2 本を直列に撃って完了します。
- `frontend/lib/queries/__tests__/smartInboundPreviewAsync.test.tsx`（6 件）: ポーリング間隔、完了、失敗、start の 409、再開、一時的な失敗の許容、離脱。
- `frontend/app/(app)/integrations/kaipoke/__tests__/InboundSmartPreviewAsync.test.tsx`（4 件）: 読み込み中の文言、再開と二重待ち受けの防止、ジョブが無いときは何もしない、失敗の理由と「もう一度読み込む」。

## 4. 残っていること

- 本番での確認（10/26 の週の読み込みが 524 にならないこと）は未実施です。デプロイ後に一度お試しください。
- 画面を離れている間にジョブが完了した場合、戻ったときには結果は出ません（active は実行中のジョブだけを返すため）。もう一度 ❶ を押してください。完了済みの結果まで復元するかどうかは、必要になれば PO 判断で決めます。
- 既存の失敗（この変更とは無関係・変更前から失敗）: `backend/tests/test_integration_kaipoke.py` の 8 件と `test_kaipoke_credentials.py` の 1 件（manager ロール関連・SQLite の UUID 変換）、`frontend/__tests__/middleware.test.ts` の 1 件（manager ロール）。
