# 別の事業所へ提供する前提での点検 — お客様固有の内容がコードに書かれている箇所

作成: 2026-10-01 ／ ステータス: **調査のみ（未着手）**
発端: PO「他のまったく違う訪問看護のクライアントに出す場合、中の実装コードに紐づいているとまずい」（2026-10-01）

## 結論
- 「よりより」固有の値がコードに直接書かれていて、処理や帳票を左右している箇所は**約 12 件**。
- 拠点名「都賀」で処理を分けていた箇所は、`office_feature_flags`（mig 0051）への置き換えが済んでいて残っていない（`office.name == "都賀"` のような分岐は 0 件）。
- 職員・患者の個人名やコードで処理を分けている箇所は **0 件**（コメントとテストだけ）。
- 札の略称（訪問モニター・2026-10-01）は `offices.short_label` から取る。ただし**拠点の編集画面に略称・並び順・カイポケ上の名前の入力欄が無い**（API では変更可）。

## 直すべきもの（重要度順）

| # | 内容 | 場所 | 直し方 |
|---|---|---|---|
| 1 | QR カードの事業所名・電話・受付時間・ロゴ | `frontend/lib/qr-print-contact.ts`、`qr-print/page.tsx`（`yoriyori-logo-h.svg`） | 事業所の設定テーブルと設定画面から取る |
| 2 | 患者カルテ Excel の拠点プルダウンと取り込み（稲毛・都賀の決め打ち） | `backend/app/services/patient_excel/karte.py` | `offices` マスタから選択肢を作る。今のままだと新しい拠点が黙って住所からの自動割当に回る |
| 3 | Excel の拠点コード入力規則（INAGE・TSUGA） | `patient_excel/schema.py`、`staff_excel/schema.py` | DB の拠点コードから動的に作る |
| 4 | 拠点の略称の決め打ち（INAGE→稲・TSUGA→津） | `board_service.py`、`propose_slots_service.py`、`CourseDayTablePanel.tsx`、`patient_excel/schema.py` | `short_label` に一本化。拠点の編集画面に `short_label`・`sort_order`・`kaipoke_name` を足す |
| 5 | サービス内容の規則（精神科前提の 4 通り）と RPA の「正看」固定、訪問看護区分の既定値（精神科） | `kaipoke/csv_builder.py`、`rpa_capability.py`、`models/patient.py` | 事業所ごとの対応表・設定にする |
| 6 | 設定画面があるのに効いていない値（移動見積もりの余裕・人数上限、1 人 6 名、表示の時間帯と昼休み） | `schedule_v2.py`、`layer2_clustering.py`、FE の `courseGrid.ts` ほか | `scheduling_settings` から取る |
| 7 | 時刻を合わせる上限（90 分・30 分・7 日）、予定外訪問の既定 60 分 | `checkin/adjust.py`、FE `ActualTimeSheet.tsx`、`visits.py` | `checkin_settings` に列を足す。FE の重複した値を消す |
| 8 | 管理者→M コース、先頭の職員→A コースの固定割当 | `layer3_assignment.py` | M もフラグ化し、固定する人を職員ごとに指定できるようにする |
| 9 | Excel 集計の拠点順、千葉専用の住所の正規表現 | `patient_excel/schema.py`・`exporter.py`、`auto_allocator_v2.py` | `sort_order` を使う。住所の分解を汎用にする |
| 10 | コースコードの固定（A〜E・M・臨。DB の CHECK 制約）、距離の既定 12km | `models/course.py` ほか、`layer3_assignment.py` | 規模によってはコード数を設定化（migration が必要） |

1〜3 は、2 拠点目以降を持つ別の事業所で確実に壊れるか、中身が誤る。5 は、相手が精神科中心かどうかで影響が変わる。

## 設定で変えられるもの（問題なし）
- 移動時速・訪問間の余裕・営業時間・1 コースの人数（`scheduling_settings`・`/settings/scheduling`。6 の例外を除く）
- 未訪問の猶予・遅刻・位置の距離・退出忘れ（`checkin_settings`・`/settings/checkin`）
- 拠点の営業曜日（拠点の画面）、カイポケ上の事業所名（`offices.kaipoke_name`。画面の入力欄は無い）

## アプリ自体のブランド（お客様固有ではない）
「らく助」の名前・マスコット・色。`frontend/public/brand/` のうち `yoriyori-logo-h.svg` だけはお客様のもの（#1）。

## 未確認
- カイポケ RPA 側（別リポジトリ PlaywrightTest1）の決め打ち。
- 略称を `short_label` で見る画面と決め打ちで見る画面が、実際にどの API を使っているか（新しい拠点で表記が画面ごとに食い違う可能性）。
