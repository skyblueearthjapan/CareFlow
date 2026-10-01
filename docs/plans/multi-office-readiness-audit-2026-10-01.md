# 別の事業所へ提供する前提での点検 — お客様固有の内容がコードに書かれている箇所

作成: 2026-10-01 ／ ステータス: **#1〜#4・#7・#9 実装済み（mig 0089・本番未反映）／ #5・#6・#8・#10 は PO 判断待ち**
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

## 対応状況（2026-10-01・ブランチ worktree-agent-afd492f19f7edcfa5・本番未反映）

migration **`0089_multi_office_settings`**（1 本）。データの手順で今までコードにあった値をそのまま入れるので、**よりより様の QR カード・Excel・略称・上限は変わらない**（旧実装の値と新しい実装の出力を突き合わせるテストあり: `backend/tests/test_multi_office_settings.py`・`test_migration_0089.py`、FE `qr-print/__tests__/page.test.tsx` の C・`ActualTimeSheet.test.tsx`）。

| # | 状況 | やったこと |
|---|---|---|
| 1 | **済** | 新テーブル `business_profile`（事業所名・電話・対応時間・対応日・ロゴのパス/URL）と `GET/PUT /api/v1/business-profile`、設定画面 `/settings/business`（QR 印刷の画面の「お問い合わせ先の設定」から入る）。`lib/qr-print-contact.ts` は削除。0089 は **拠点コード INAGE / TSUGA の拠点がある DB（よりより様）のときだけ** 今の値を入れる（別の事業所の新しい DB にはよりより様の電話番号を入れない。未設定の項目とロゴはカードに載らない）。ロゴのアップロードは作っていない（パスか https の URL を入れる） |
| 2 | **済** | カルテの拠点プルダウン・拠点名 → office_code を `offices`（コードのある拠点・並び順）から作る。新しい拠点も拠点名で解決する。カルテのコース欄の略称も拠点マスタから |
| 3 | **済** | 患者 Excel・スタッフ Excel の拠点コードの選択肢を `offices` から作る（並び順どおり） |
| 4 | **済** | 略称を `offices.short_label`（未設定なら拠点名の 1 文字目）に一本化: `services/office_labels.py`（モニターの `office_short` を移設）。ボード・提案・スケジュールの健康診断・特別訪問・患者 Excel（`build_office_code_short_maps` から稲毛・都賀の決め打ちを撤去）・`CourseDayTablePanel`。拠点の編集画面に「略称」「並び順」「カイポケ上の事業所名」を追加（v1 の拠点 API に `kaipoke_name` が無かったので足した）。0089 で INAGE / TSUGA の略称・並び順が空なら 稲/1・津/2 を入れる |
| 7 | **済** | `checkin_settings` に 4 列（到着をさかのぼれる分・退出を後ろへ動かせる分・スタッフが合わせられる日数・予定外訪問の仮の所要時間）。0089 は値を入れない（NULL = コード既定 90 / 30 / 7 / 60 = 今までの定数。画面では「既定」と出る。他の列と同じ扱い）。`/settings/checkin` に「実績の時刻を合わせるルール」を追加。スマホのシートと打刻履歴の案内は `GET /checkin-settings/public` の値を使う |
| 9 | **済** | Excel の集計・プルダウンの拠点順を `sort_order` → 名前に。住所の分解は今までの千葉の 2 つの正規表現をそのまま使い、別の都道府県向けの「都道府県＋市＋区」「東京 23 区」「郡の町村」は **千葉県以外の都道府県名から始まる住所だけ** に使う（県名の無い「若葉区都賀…」や千葉県の郡の住所など、今まで空欄だったものも空欄のまま） |
| 5・6・8・10 | 未着手 | PO 判断待ち（今回の範囲外） |

### 残ること・気をつけること
- **本番反映の前に**、本番の `offices` の `short_label` / `sort_order` が INAGE=稲/1・TSUGA=津/2 であることを確かめる（0059 は拠点名で入れたので通常は入っている。0089 は空のときだけ埋める。違う値が入っていたら、その値が表示に出る）。
- FE には、設定を取れないとき（取得前・古い BE）の目安として既定の 90 / 30 / 7 が `ACTUAL_TIME_LIMITS_FALLBACK` に残る（`CHECKIN_PUBLIC_FALLBACK` と同じ作り。正はサーバの検証）。
- **PO 決定（2026-10-01・案 f）**: 患者カルテ Excel の取り込みで、拠点セルの拠点名・コードが有効な拠点（`offices`・削除済みを除く）に当たらないときは、住所からの自動割当に回さず **行エラー**「拠点名『◯◯』が拠点マスタにありません（行 6）」にする（行 6 = カルテの拠点セル B6 の行）。扱いは通常の患者 Excel の取り込みの行エラーと同じで、**その行（= その患者）だけ止まり、ファイル全体は止めない**（カルテは 1 ファイル 1 患者なので、実際にはその患者が取り込まれない。プレビューにエラーとして出る）。拠点セルが **空欄なら今までどおり住所からの自動割当**。既知の拠点名・「（自動）」付き・拠点コードは今までどおり。実装 `karte.parse_karte_workbook(unknown_office_labels_out=…)`・`api/v1/patients_excel.import_karte`、テスト `test_patient_karte_api.py` の末尾 3 件。
- `propose_slots_service` などの `office_code_by_id` 引数は略称に使わなくなった（呼び出し元が多いので引数は残した）。
- 監査に無かった決め打ちで直したもの: `schedule_health.py`（コースの原因内訳の見出し）、`special_visits.py`（legacy 既定へのフォールバック）、`karte.py` のコース欄（略称の対応を渡していなかった）。
- まだ確かめていないもの（今回の範囲外）: カイポケ RPA 側（別リポジトリ）。`layer2_clustering.py` の「千葉エリア」はコメントだけ（処理は変わらない）。
- QR 印刷: 事業所の情報を読み込み中・読み込めないときは印刷ボタンを止める。未設定なら「お問い合わせ先が未設定です（設定 → 事業所の情報）」を出す（印刷はできる）。
- 拠点の編集画面: 使っている略称を変える・消すときは、札・コース表・Excel が変わり前の Excel が取り込めなくなることを確認してから保存する。
- 0089 の downgrade は PG16 で確認済み（列の CHECK は列と一緒に消す。`op.drop_constraint` は命名規約で名前が変わるので使わない）。

### 既知の失敗（このブランチの原因ではない）
- `backend/tests/test_patients_excel_replace_all.py::test_replace_all_manager_forbidden` と `backend/tests/test_staff_excel_replace_all.py::test_staff_replace_all_manager_forbidden` は **b70f7ab の時点で失敗している**（ロール二軸 mig 0069 以降 `manager` は admin の別名なので 403 にならず 200）。テストを今のロールに合わせる必要がある（別途）。

### 残っている INAGE / TSUGA / 稲毛 / 都賀 の記述（今回触っていないもの・処理には効いていない）
2026-10-01 の grep で、処理を分ける決め打ちは残っていない。残りはコメント・docstring・API の説明文・入力例だけ。拠点名で処理を分けていた固定割当は mig 0051 で `office_feature_flags` に移し済み。
- コメント・docstring: `backend/app/services/scheduling/layer3_assignment.py`（都賀 A / manager M の固定割当の説明。#8）、`backend/app/services/scheduling/auto_allocator_v2.py`（クロス拠点の例）、`backend/app/services/scheduling/course_staff_mirror.py`、`backend/app/services/kaipoke/replace_inbound.py`、`backend/app/services/accompaniment.py`、`backend/app/services/patient_excel/exporter.py`（グリッドの説明）、`backend/app/api/v1/schedule.py`、`backend/app/api/v1/schedule_v2.py`、`backend/app/models/office.py`
- API の説明文 (`description=` / 例): `backend/app/schemas/staff.py`、`backend/app/schemas/v2/{office,patient,staff,board,acceptance_matrix,auto_schedule_v2}.py`、`backend/app/schemas/office_feature_flag.py`、`backend/app/schemas/integrations.py`、`backend/app/schemas/visit_monitor.py`
- カイポケ: `backend/app/services/kaipoke/csv_builder.py` の「よりより」前提の既定値（#5・PO 判断待ち）
- FE のコメント・入力例: `frontend/components/field/FieldSheets.tsx`（住所の入力例「千葉市稲毛区…」）、`frontend/components/field/FieldBoard.tsx`、`frontend/components/schedule/**`（`WeekTimelineBoard`・`StaffWeekBoard`・`AccompanimentBar` ほかのコメント）、`frontend/app/(app)/monitor/page.tsx`、`frontend/app/(app)/patients/_components/PatientFixedVisitsPanel.tsx`、`frontend/lib/schemas/v2/{office,staff}.ts`、`frontend/lib/schemas/{integration,trainee_accompaniment}.ts`

## 設定で変えられるもの（問題なし）
- 移動時速・訪問間の余裕・営業時間・1 コースの人数（`scheduling_settings`・`/settings/scheduling`。6 の例外を除く）
- 未訪問の猶予・遅刻・位置の距離・退出忘れ（`checkin_settings`・`/settings/checkin`）
- 拠点の営業曜日・略称・並び順・カイポケ上の事業所名（拠点の画面。略称ほか 3 項目は 0089 のブランチで入力欄を追加）

## アプリ自体のブランド（お客様固有ではない）
「らく助」の名前・マスコット・色。`frontend/public/brand/` のうち `yoriyori-logo-h.svg` だけはお客様のもの（#1）。

## 未確認
- カイポケ RPA 側（別リポジトリ PlaywrightTest1）の決め打ち。
- 略称を `short_label` で見る画面と決め打ちで見る画面が、実際にどの API を使っているか（新しい拠点で表記が画面ごとに食い違う可能性）。
