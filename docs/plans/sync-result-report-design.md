# 連携結果レポート（らく助⇄カイポケ）設計書

**状態: 設計（2026-09-03・PO 構想を受けて起草・未実装）**
PO の要望（2026-09-03）: 連携（らく助→カイポケ／カイポケ→らく助）を行った結果を、A4 縦で印刷できる HTML の報告書として出したい。**完了履歴にボタンを置き、押せばすぐ開く**。ページの途中で表が切れたり、構成が中途半端にならないよう改ページを整える。
様式の手本 = `docs/reports/2026-09-03-w37-kaipoke-sync-report.html`（本日の手作り報告書・PO 確認済み）。

## 0. 結論（先に決めること）
| 論点 | 決定（案） | 理由 |
|---|---|---|
| 生成タイミング | **ボタンで都度生成**（`GET /integrations/kaipoke/jobs/{id}/report?format=html`）。ただし**明細はジョブ完了時に保存**する | 「押せばすぐ開く」は都度生成で 1 秒以内。取込側は行単位の結果が今どこにも残らない（§2）ので、保存しないと後日開けない |
| 明細の保存先 | 既存の **`kaipoke_job_items`**（job_id / seq / status / content JSONB / error_msg・現在どこからも使われていない）| migration 不要・job と 1:N の受け皿が既にある |
| 対象ジョブ | Phase 1 = 訪問の送信 `apply` と取込 `smart-apply` / `apply-inbound` / `replace-inbound`。Phase 2 = イベント `events-outbound` / `apply-events`。プレビュー・export・expand は対象外（ボタンを出さない） | 実害があるのは実書込の 6 op。dry-run はジョブ自体が作られない |
| ボタンの場所 | 連携ページ「ジョブ履歴」の操作列（「詳細」の隣）＋ジョブ詳細ページ＋「直近の取り込み」行 | 調査 §3 の 3 箇所。`status='completed'` かつ対象 op のときだけ表示 |
| 開き方 | 既存 `ReconcileReportButton` と同じ **Blob → 新タブ**（同期 `window.open` → fetch → `location.href`） | 実績あり・ポップアップブロック回避済み |
| 「送信後の確認」 | レポート生成時に、その週の **最新カイポケ控え（snapshot）との突合**を末尾に付ける（取得時刻を明記） | 8/31・9/3 の教訓「RPA の成功報告だけを信じない」を報告書の形に落とす |

## 1. 章構成（A4 縦・両方向共通の骨格）
```
1 ページ目（表紙＋要約・必ず 1 ページに収める）
  ┌ タイトル: 「らく助 → カイポケ 送信結果報告」 or 「カイポケ → らく助 取込結果報告」
  │ 対象週 / 実行日時（開始〜完了・所要）/ 実行者 / ジョブ ID（末尾 8 桁）
  ├ 結論 1 文（緑=全件反映 / 黄=一部要対応 / 赤=失敗あり）
  ├ KPI タイル: 対象 / 成功 / 失敗 / 除外（送らなかった）/ 要確認
  ├ 除外の内訳（理由別件数: 過去日・担当なし・RPA 未対応・利用者未検出 …）
  ├ 要対応一覧（失敗・二重・要確認の行を日付順・最大 15 行。溢れたら「明細参照」）
  └ 送信後の確認（最新控えとの突合: 一致 / 相違 / 片側のみ・取得時刻）
2 ページ目〜（明細・毎ページ改ページ規則あり）
  ├ 日ごとのセクション（9/7（月）… 9/13（日））
  │   表: 時刻 / 利用者 / 操作（追加・変更・削除・日付変更）/ 変更内容（before → after）/ 結果 / 理由
  └ 除外した行（理由付き・日付順）
最終ページ
  └ 補足（用語・理由コードの説明・データ根拠）
```
取込方向の明細列は「時刻 / 利用者 / 操作（取消・更新・追加・置換）/ 変更内容（らく助 before → after）/ 結果 / 理由」。置換取込は「消した N 件・入れた N 件」に加え、スキップ理由（`ReplaceSkip`）と新人単独（`trainee_solo`）を要対応に出す。

## 2. データ設計（今日の欠落を埋める）
調査結果（§5 参照）: 取込側 4 op と送信側の除外行は**件数しか残らない**。レポートの根拠にするため、ジョブ完了時に以下を `kaipoke_job_items` へ書く。

| op | 1 行 = | `content` の主なキー | 書く場所 |
|---|---|---|---|
| `apply`（送信） | 送った修正 1 件 | `action, date, user_name, before{start,end,staff1,staff2,service}, after{…}, outcome(success/failed/skipped), reason, sheet_item_id` | `_reconcile_latest_job` で RPA `result.details[]` を `correction_sheet_items` と (日, 利用者, action) で突き合わせて確定。送信前に除外した行も `outcome='excluded', reason=past/unassigned/rpa_unsupported` で書く（`trigger_apply` 時点） |
| `apply-inbound` / `smart-apply`(diff 側) | 取り込んだ差分 1 件 | `InboundItemResult` 相当 + sheet item の before/after | `apply_inbound` 完了時（`results` は今レスポンスにしか無い） |
| `replace-inbound` / `smart-apply`(replace 側) | 置換した日 1 件 + スキップ 1 件 | `kind=day: date, wiped, inserted` / `kind=skip: reason, user_name, staff_name, date, start` / `kind=trainee_solo: staff, count` | `replace_inbound` 完了時 |
| `apply-events` | イベント 1 件 | `EventApplyResult`（action/external_id/staff_name/date/title/outcome/detail） | 完了時 |
| `events-outbound` | イベント 1 件 | RPA `results[]` + 送った item（既に result_summary にあるので item 化のみ） | 完了時 |

`status` 列 = outcome（success/failed/skipped/excluded）、`error_msg` = reason の人間向け文。`seq` = 日付・時刻順。件数上限なし（週 200 行程度）。古いジョブの items は残す（監査価値）。

追加で `result_summary` に `report_meta = {direction, op_label, executor_name, verified_at?}` を入れる（executor は `users` を join して名前解決。FE 側の op ラベル辞書 2 箇所も BE の辞書へ寄せる）。

**併せて直す既知の穴**: `_reconcile_latest_job` の先取りクローズ除外リストが `"events-outbound-apply"` になっており実 op `"events-outbound"` と不一致（イベント送信が `result_unknown` で閉じられて結果を失う）。

## 3. API
- `GET /api/v1/integrations/kaipoke/jobs/{id}/report?format=json|html`（admin・read-only）
  - json: `{job, report_meta, summary, excluded[], rows[], verification{…}, html}`（`ReconcileReportRead` と同型の作法）
  - html: `HTMLResponse`
  - 対象外 op / 未完了 / items 無し（改修前のジョブ）→ 422 に理由。改修前のジョブは「明細なし版」（result_summary の件数と details だけ）を出せるようにする（今日の 57a70c9c は details があるので出せる）
- 実装: `services/kaipoke/sync_report.py`（`build_sync_report(db, job_id)` = DB 読取・純データ）＋ `sync_report_html.py`（`render_sync_report_html(report)` = 純関数）。`build_reconcile_report` を末尾の突合に再利用。

## 4. 印刷（A4 縦）規則
`feasibility_report_html._CSS` を土台に共通 CSS へ切り出す（`report_css.py`・突合/実現性/本レポートで共有）。
- `@page { size: A4 portrait; margin: 12mm 13mm }`。画面では `.sheet{width:210mm}` で紙面プレビュー、印刷は `width:auto`。
- **1 ページ目は要約だけ**: `.cover{break-after:page}`。要対応一覧は 15 行で打ち切り（溢れ分は「明細参照」）。
- **日ごとのセクション**: `section.day{break-inside:avoid}` を**行数 ≤ 14 のとき**だけ付ける（1 ページに収まる塊は割らない）。それ以上は割れてよいが `thead{display:table-header-group}` で見出し行を各ページに繰り返し、`tr{break-inside:avoid}` で行の途中では切らない。`h2{break-after:avoid}` で見出しの直後で切らない。
- 明細の先頭は `break-before:page`。補足ページも `break-before:page`。
- フッタは**静的**（通常フロー）で、**表紙の末尾と文書の末尾の 2 箇所**に出す: 「らく助×カイポケ 連携結果報告 / 対象週 / 作成日時」。**当初案の `position:fixed; bottom:0`（全ページ複製）は不採用**（2026-09-03 実機 PDF 8 ページで最終行に重なった）。理由: Chrome は fixed 要素を `@page` マージンの**内側**＝本文領域に対して配置するため、本文もフッタも同じ下端に来て必ず重なり、下マージンを広げても両方が一緒に上がるだけで解消しない（CSS のページマージンボックス `@bottom-center` は Chrome 未対応）。手本の手作り報告書も静的フッタ。ページ番号は CSS だけでは全ブラウザ共通に出せないため付けない。
- 文字は 9pt 基準・表は 8.4pt・`font-variant-numeric: tabular-nums`。色は既存トークン（緑=一致/成功、赤=失敗、黄=要確認）。印刷時は背景色を保つ `-webkit-print-color-adjust: exact`。

## 5. FE
- `frontend/components/integrations/SyncReportButton.tsx`（`ReconcileReportButton` の汎用化: `jobId` を受け取り `GET …/report` を開く）。
- 置き場所: `KaipokeJobsList` 操作列 / `kaipoke/[id]/page.tsx` ヘッダ / `InboundControls` 直近の取り込み行。表示条件 = `status === 'completed' && REPORTABLE_OPS.has(params.op)`。
- op ラベル辞書を `lib/kaipokeOps.ts` に一本化（BE `report_meta.op_label` があればそれを優先）。

## 6. テスト
- BE: 純関数 `render_sync_report_html` のスナップショット（章の有無・改ページクラスの付与条件・15 行打ち切り）／`build_sync_report` の API シード（apply 完了→items 生成→report 200、未完了 422、改修前ジョブ＝明細なし版）／`_reconcile_latest_job` の op 名修正の回帰。
- FE: ボタンの表示条件・Blob 新タブ（既存 FeasibilityCheckButton.test の型）。
- 実機: 今日の 57a70c9c（失敗 22）と 47423f5f（成功 24）でレポートを出し、手作り報告書と数字が一致すること。印刷プレビューで 1 ページ目が単独・表が行の途中で切れないこと。

## 7. 段階
1. **Phase 1（訪問・両方向）**: items 保存（apply / apply-inbound / smart-apply / replace-inbound）＋ report API ＋ HTML ＋ 履歴ボタン。op 名不一致の修正も同梱。
2. **Phase 2（イベント）**: events-outbound / apply-events の items ＋ 章。
3. **Phase 3**: 送信直後に自動で「差分最新化」を 1 回回して `verification` を確定させる（今は手動の 🔄 が前提）。通知（完了時のベル）にレポートへのリンクを載せる。

## 8. 未決（PO 確認）
- 患者名は実名で出す（社内・お客様向け前提）。伏字版は要否のみ確認。
- 改修前のジョブ（items 無し）に「明細なし版」を出すか、対象外にするか。
- 置換取込の明細粒度（日単位で足りるか、消した訪問 1 件ずつまで出すか）。
