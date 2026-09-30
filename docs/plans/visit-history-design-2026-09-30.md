# 打刻履歴（訪問記録の新タブ）— 設計と契約

作成: 2026-09-30 ／ ステータス: **Phase 1 実装中**（一覧・Excel・A4）。Phase 2（到着・退出時刻の補正）は別途。

## 0. 背景

- お客様（松岡様 2026-09-30）: 「何月何日の何時何分に、どの看護師が訪問したかを閲覧したい。請求時に時間確認表と照らし合わせる」。10 月は紙と QR の併用、11 月めどに QR へ完全移行。
- 今は月単位で打刻を一覧する画面が無い（訪問モニターは 1 日単位）。
- PO 決定（2026-09-30）: **`/records`（訪問記録）を「打刻履歴」「音声記録」の 2 タブにする**。見た目の正典は `docs/mockups/visit-history-tab-mock.html`。
- つなぎの月次レポート（本番 DB から読み取りで抽出）は `docs/tools/visit-history/`（`extract.sql` + `build_report.py`）。行の範囲・備考の語彙・Excel のシート構成は、この道具と画面機能で揃える。

## 1. Phase 1 の範囲

入れるもの: 期間指定の一覧、絞り込み、並び替え、集計帯、詳細ダイアログ（読み取り専用）、Excel 出力、A4 出力。

**入れないもの（Phase 2）**: 時刻の補正。モックにある「補正」バッジ・「時刻の補正」KPI・「時刻の補正あり」フィルタ・詳細の「到着時刻を直す」枠・「読取」の併記は Phase 1 では**出さない**。

## 2. 行の定義（API・Excel・A4 共通）

1 行 = 1 訪問（`visits` 1 件）。期間は `visit_date`（JST の日付）で切る。

載せる訪問:
1. 有効な訪問: `deleted_at IS NULL` かつ `status <> 'cancelled'`
2. **打刻（arrival / departure）が 1 件でもある訪問は、取消・削除済みでも載せる**（取込が打刻済みの予定を取り消した実例がある。訪問の事実を落とさない）。備考に「取消済みの予定に記録」。

実績時刻: `visit_checkins` の kind ごとの最新（`scanned_at DESC, id DESC`）の `scanned_at`。`no_show` は実績に数えない。
この「最新の到着・退出を訪問ごとに引く」処理は **`backend/app/services/checkin/actuals.py` に新設する 1 つの関数**に置き、本機能はそれを使う（既存の `visits.py` / `monitor.py` / `notify.py` の 3 箇所をここへ寄せるのは Phase 2。Phase 1 では既存 3 箇所に触れない）。

行の項目:

| 項目 | 内容 |
|---|---|
| `visit_id`, `visit_date` | |
| `office_id`, `office_name` | 患者の主担当拠点（`patients.primary_office_id`） |
| `patient_id`, `patient_name` | |
| `start_time`, `end_time` | 予定。**`is_unplanned` の訪問は null**（打刻時刻が予定欄に入っているだけなので予定として見せない） |
| `planned_staff_id`, `planned_staff_name` | `visits.primary_staff_id`。予定外は null |
| `actual_staff_id`, `actual_staff_name` | 最新 arrival の打刻者（無ければ最新 departure の打刻者） |
| `arrival_at`, `departure_at` | UTC の ISO 文字列（`VisitRead.actual_*` と同じ形）。無ければ null |
| `stay_minutes` | 到着・退出を JST の分に切り捨ててからの差。片方でも無ければ null |
| `checkin_source` | 最新 arrival の `qr` / `manual` |
| `match_status` | 最新 arrival の位置判定 |
| `is_substitute` | 到着の打刻者が担当集合（primary / secondary / mentor / `visit_staff_assignments` / `accompaniments`）の外 |
| `is_unplanned`, `is_cancelled` | `is_cancelled` = 取消または削除済み |
| `state` | 下表 |
| `remarks` | 備考ラベルの配列（下記の語彙・順序） |

`state`:

| 値 | 条件 |
|---|---|
| `done` | 到着・退出とも有り |
| `in_progress` | 到着のみ、かつ `visit_date` が今日（JST） |
| `no_departure` | 到着のみ、かつ `visit_date` が今日より前 |
| `none` | 打刻なし、かつ予定開始が現在（JST）より前 |
| `future` | 打刻なし、かつ予定開始が現在以降 |

`remarks` の語彙（この順）: `退出なし`（state=no_departure）／`予定外の訪問`／`代行（予定: ○○）`／`QRなし`（checkin_source=manual）／`場所 要確認`（match_status が review・mismatch・no_gps）／`取消済みの予定に記録`／`到着と退出が近い`（滞在 5 分未満）。

## 3. API（すべて `/api/v1/visit-history`・`require_role("admin","staff")`）

共通クエリ:

| 名前 | 内容 |
|---|---|
| `from`, `to` | `YYYY-MM-DD`・必須・`from <= to`・最大 92 日（超えたら 422） |
| `patient_id`, `office_id` | 任意 |
| `staff_id` | 予定の担当**または**実際の打刻者が一致 |
| `state` | `in`（到着あり）/ `nodep`（no_departure）/ `none` / `special`（代行または予定外） |
| `q` | 患者名・予定担当名・打刻者名の部分一致。2 文字未満は無視 |
| `sort` | `date`（既定: 日付→予定開始→患者名）/ `staff`（看護師名→日付）/ `patient`（患者名→日付）。看護師名は `actual_staff_name ?? planned_staff_name` |

権限: **staff ロールは自分の分だけ**（自分が担当集合に入る訪問、または自分が打刻した訪問）。`staff_id` を指定しても BE が自分に固定する。staff 未紐付けの staff は空。admin は全件。`/visit-recordings` の一覧と同じ流儀にする。

1. `GET /visit-history` — 追加クエリ `limit`（既定 50・最大 200）`offset`。
   応答 `{ items: [...], total, summary: { visits, with_arrival, with_departure, no_departure, none } }`。
   `summary` は**ページングする前の絞り込み結果全体**から、`future` を除いて数える。
2. `GET /visit-history/export` — `.xlsx` を返す（`Content-Disposition: attachment`、ファイル名 `visit-history_{from}_{to}.xlsx`、`Cache-Control: no-store`）。シートは `QR読み取りあり`／`全予定`／`看護師別`／`読み方`（`docs/tools/visit-history/build_report.py` と同じ構成・列）。`future` の行は載せない。
3. `GET /visit-history/report` — A4 縦の HTML を返す（`Cache-Control: no-store`）。追加クエリ `group`（`staff` 既定 / `date` / `patient`）、`include_none`（既定 false = 到着のある訪問だけ）、`page_break`（既定 false。true で看護師・患者ごとに改ページ）。
   **体裁は 2026-09-30 にお客様へ渡した月次レポート（`docs/tools/visit-history/build_report.py` の HTML）と同じにする**（PO 確認 2026-09-30）: 画面上で A4 の用紙が 1 枚ずつ並び、各用紙に見出し・出力日時・フッターの注記・「1 / 5」のページ番号が付き、上部のバーに枚数と「印刷 / PDF に保存」ボタンがある。1 枚目に注意書き・件数・「読み方」・看護師別の件数表、続いて明細。
   改ページは同じ方式（行を 1 つずつ流し込み、用紙からあふれたら次の用紙へ送る。固定行数で切らない・あふれた行が消えない）。ただし **JavaScript が動かなくても読めて印刷できる**ようにする: サーバは表を含む完全な HTML を返し（CSS の印刷フロー `thead{display:table-header-group}` `tr{break-inside:avoid}` で改ページできる状態）、埋め込みスクリプトがそれを用紙に組み直す。レポートは FE が認証付きで取得して blob URL で新しいタブに開く（`RecordReportButton` と同じ）ので、インラインスクリプトはその文書内で動く。
   PDF は専用の出力を作らない（ブラウザの印刷から保存する）。

実装方針: 1 か月で約 600 訪問なので、期間内の訪問と打刻を読み込んでから Python 側で行を組み、絞り込み・並び替え・ページングをする（SQL で state を組み立てない）。N+1 にしない（訪問・打刻・担当割当・同行・患者・スタッフ・拠点をまとめて引く）。

PHI を返すので 3 つとも `Cache-Control: no-store`。

## 4. 画面（`/records`）

- ページ見出しの下に**ページタブ**「打刻履歴」「音声記録」。URL は `?tab=history|voice`。
  - 既定は `history`。ただし `?patient=` `?staff=` `?visit=` の付いた既存の導線（患者詳細・スタッフ詳細の「すべて見る」、モニターの「🎙 記録を見る」）は**従来どおり音声記録**を開く。
  - 音声記録タブの中身は今の一覧をそのまま（挙動を変えない）。
- 打刻履歴タブ（モックどおり）:
  - 期間: 今週／先週／今月／先月／期間指定。‹ › で週・月を前後に送る。既定は今月。
  - 絞り込み: 患者・スタッフ・拠点・打刻（すべて／打刻あり／退出なし／打刻なし／代行・予定外）・検索。staff ロールはスタッフと拠点のセレクトを無効化（既存の `staffScoped` と同じ見せ方）。
  - 並び: 日付順／看護師別／患者別。看護師別・患者別は見出し行（件数・打刻あり件数）を挟む。
  - 集計帯: 訪問／打刻あり（率）／退出なし／打刻なし。
  - 表: 日付・患者・予定・訪問した看護師・到着・退出・滞在・備考。1 ページ 50 件。
  - 行クリックで詳細ダイアログ（読み取り専用: 到着・退出・滞在のタイル、訪問した看護師、予定の担当、記録の方法）。
  - 「Excel で出力」「A4 で印刷」。A4 は並び・改ページ・打刻なしを含めるかを選んでから開く。認証付きで取得して開く方法は既存の `components/records/RecordReportButton.tsx` に合わせる。
  - 時刻の表示は `frontend/lib/format/actualTime.ts`（JST 変換の単一ソース）を使う。
- サイドバーの「訪問記録」のアイコンはマイクから書類系へ変える（音声専用ではなくなるため）。ラベルは変えない。

## 5. テスト

- BE: 行の範囲（取消済み＋打刻ありが載る／取消済み＋打刻なしは載らない／【検証】等の特別扱いはしない）、state 5 種、備考の語彙、予定外は予定欄 null、代行判定、staff ロールの絞り（他人の分が見えない・`staff_id` 指定でも自分に固定）、期間 92 日超は 422、summary がページングに左右されない、xlsx が開けてシート 4 枚、report が `no-store`。
- FE: タブの既定と `?tab=`、既存ディープリンクで音声記録が開く、期間の切り替えでクエリが変わる、看護師別の見出し行、staff ロールでセレクトが無効、空状態。

## 6. Phase 2（予告・この文書では未確定）

実績の時刻を合わせる機能（スマホでその場・後から、PC は打刻履歴の詳細から）。PO 決定（2026-09-30）: 操作はホイール式（10 分目盛り・最終 1 分単位）。**文言は「合わせる」「調整」に統一し、「直す」「修正」「補正」は使わない**（遅れて記録されるのは看護師の誤りではないため）。実績時刻の算出を `actuals.py` に一本化し、`visits.py` / `monitor.py` / `notify.py` をそこへ寄せる。スマホのモックは `docs/mockups/mobile-actual-time-fix-mock.html`。
