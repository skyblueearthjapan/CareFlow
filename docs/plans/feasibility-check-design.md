# 実現性チェック（移動・重なり・バッファ・同住所ルール）— 設計メモ (2026-08-31)

**状態: 実装済み・レビュー中（未デプロイ）。** PO 要望「手で並べ替えた週の予定が物理的に成立するか、
システム外ではなく らく助のボタンで判定して A4 レポートにしたい」。当日はスクリプト
（`Documents\kaipoke-2026-08-backup\tools\run_feasibility.sh`）で 4 回回した結果を、そのまま機能に移植した。

## 1. 何をするか（read-only）
- 対象: ISO 週の月〜土（`days` で 1〜7 日）。訪問 (`status<>'cancelled'`・未削除) と拘束イベント
  (`staff_events.blocking`・取消なし・0 分メモは除外)。同行（職員 2 / 指導者）はその人の時間軸にも載せる。
  担当未定は「担当未定」の仮想スタッフにまとめる。
- 判定（スタッフ×日の時間軸を時刻順に走査）:

| 種別 | 判定 | 区分 |
|---|---|---|
| 重なり | 次の開始 < 前の終了 | ❗ |
| 移動不可 | 間隔 < 移動分（直線距離 ÷ `travel_speed_kmh`・最低 1 分・同住所 0） | ❗ |
| バッファ不足 | 間隔 < 移動分 + `visit_buffer_min` | △ |
| 要注意(実走行) | 間隔 < 直線×1.3 の移動分 + バッファ | △ |
| 同住所ペア90分未確保 | 同住所ペア（同時刻 or 端点連続・最大 2 名）の占有 max(合計, 90) の終了前に次が始まる | △ |
| 同住所ペア:同時刻でない | ペアが連続配置（ルールは同時刻スタート） | △ |
| 同住所3名以上 | 同住所同時刻に 3 名以上 | ❗ |
| 昼休みなし | `lunch_window` 内に `lunch_duration_min` の連続空きがない | 参考 |

- 前提はすべて既存の単一ソース: `scheduling/config.py`（設定行 or 既定 20km/h・8 分・11:30〜13:30・60 分）、
  `constants.SAME_ADDRESS_TOLERANCE`、`auto_allocator.SAME_ADDRESS_MAX`、`auto_allocator_v2.SAME_ADDRESS_PAIR_MIN_OCCUPANCY / haversine_minutes`。
  独自値は道路係数 1.3（`ROAD_FACTOR`）と朝会の出発地扱い（`OFFICE_EVENT_TITLES`）だけ。
- 担当の解決は **`visits.primary_staff_id`（訪問自身の担当・手動変更を正）→ コース担当フォールバック**
  （2026-09-01 是正。csv_builder と同じ。旧仕様「コース担当優先」は盤面手直し週に実担当と食い違うため撤回）。同行は旧列
  （secondary/mentor）＋ `accompaniments`（visit 単位）。2 名体制の 2 行や相互参照で同じ職員に同じ訪問が
  2 回来る場合は 1 つにまとめる。同住所ペアは **別患者** のみ（同一患者の分割訪問はペアにしない）、
  同住所 3 名以上は **同時刻** のみ ❗（連続で 3 名を回るのは成立する）。座標のない患者は「座標なし」として
  参考扱いで明示（黙って OK にしない）。時間軸のキーは staff.id（同名スタッフの混同防止）。
- ペア占有は max(サービス合計, 90) で、エンジン（max(自分の終了, 起点+90)）より物理的に厳しい側に倒している。
  エンジン側をこれに合わせて変えないこと（レビュー LOW-5）。
- 出力: JSON（件数・指摘一覧・前提）＋自己完結 HTML（外部リソースなし・A4 縦 `@page`・印刷ボタン）。

## 2. 実装
- `backend/app/services/scheduling/feasibility_check.py` — 純粋関数 `evaluate_day / evaluate_week`（テスト対象）と
  DB ローダー `load_week_items / build_feasibility_report`。StaffEvent の時刻は `staff_event_defaults.content_key` と同じ
  「aware→UTC→naive」で壁時計化。
- `backend/app/services/scheduling/feasibility_report_html.py` — レポート HTML（`html.escape` 済み）。
- `backend/app/api/v1/feasibility.py` — `GET /api/v1/schedule/v2/feasibility-report?iso_year&iso_week[&office_id][&days][&format=json|html][&include_html]`
  admin 限定。`__init__.py` で `/schedule` 配下に登録。
- `frontend/components/schedule/FeasibilityCheckButton.tsx` — 週セレクタのカード右側（拠点フィルタの左）。押すたび再計算、
  click 直下で空タブを確保 → 応答の HTML を `document.write`。結果件数（❗/△）をバッジとトーストで表示。admin のみ表示。
- テスト: `backend/tests/test_feasibility_check.py`（判定 9 ケース + API 認可/JSON/HTML）、
  `frontend/components/schedule/__tests__/FeasibilityCheckButton.test.tsx`（非 admin 非表示 / 正常 / 失敗）。

## 2-b. レビュー（code-reviewer/opus・3 巡）で確定した実装上の約束
- `window.open` の features に `noopener` を付けない（仕様上 null が返り、レポートが開かない）。
  タブは blob: URL へ静的遷移し、遷移後に `opener = null`。blob: はアプリと同一オリジンなので、
  backend の `html.escape` が安全境界（renderer にエスケープ無しの補間を足さない）。
- コース結合は盤面と同じガード（未削除・同 ISO 週）。コースは担当フォールバックと拠点フィルタに使う。
- 同行は accompaniments の visit 単位とコース単位（週の既定展開）を両方拾う。
- 拠点フィルタ: 訪問はコース拠点（コース無しは患者拠点）、イベントは職員の所属拠点（受容した差）。
- 日曜は既定で判定しない（`days=6`・FE は送らない）。必要なら `days=7`。

## 3. 限界・今後
- 直線距離ベース（川・線路の迂回は未考慮）。1〜2 分差の「移動不可」は実務上許容の可能性 → しきい値/許容差の設定化が候補。
- 未判定: 患者の受け入れ時間帯・スタッフ勤務時間（休み/早退）・駐車/入館の手間・同行者側の移動。
- 発展: 盤面カードに ❗/△ を直接表示、区間ごとの実走行時間の手入力上書き、`scheduling_settings` UI にしきい値追加。
- 当日の運用結果（9/1〜9/5・4 回の判定）は `incident-2026-08-31-kaipoke-expand-wrong-month.md` の日付と
  `Documents\kaipoke-2026-08-backup\実現性チェック_*` を参照。
