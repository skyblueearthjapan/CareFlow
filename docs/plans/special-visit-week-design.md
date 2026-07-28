# 特別訪問週間 (Special Visit Week) 設計書 — PO確定仕様 2026-07-29

## 0. 一言で

患者ごとに期間（例: 3週間）と目標（週N回以上・既定5）を設定し、基本の固定訪問は
**そのまま生かしたまま**、カレンダーの曜日セルに○を付けて「追加の訪問枠」を週ごとに
プールへ積み、毎週、提案または手動で空きに配置する。追加分は固定化しない（恒久
パターン=PFVには一切触れない）。期間が終われば自然に元へ戻る。

**既存の `special_weekly_pattern` / `special_week_active` / PFV mode='special'（置換型）は
使わない・触らない（据え置き）。** 本機能は「上乗せ型+都度配置」で別物。

## 1. データモデル (migration 0064)

```
special_visit_periods
  id UUID PK
  patient_id UUID FK patients ON DELETE CASCADE, NOT NULL
  start_date DATE NOT NULL        -- 任意起点 (今日から等)。週判定はISO週単位
  end_date DATE NOT NULL          -- 含む
  weekly_target INT NOT NULL DEFAULT 5   -- 「週N回以上」。期間で一律・週別調整なし
  note TEXT NULL
  status VARCHAR(16) NOT NULL DEFAULT 'active'   -- 'active' | 'ended' | 'cancelled'
  created_at / updated_at
  索引: (patient_id, status)
  制約: 同一患者で status='active' の期間は同時に1本のみ (アプリ層で担保・422)

special_visit_marks
  id UUID PK
  period_id UUID FK special_visit_periods ON DELETE CASCADE, NOT NULL
  patient_id UUID NOT NULL        -- 非正規化 (プール一覧の join 削減)
  iso_year INT NOT NULL
  iso_week INT NOT NULL
  weekday INT NOT NULL            -- 0=Mon..5=Sat (日曜は対象外)
  kind VARCHAR(16) NOT NULL       -- 'extra'(○追加枠) | 'displaced'(固定退避・日単位)
  status VARCHAR(16) NOT NULL DEFAULT 'pool'  -- 'pool' | 'placed' | 'cancelled'
  placed_visit_id UUID NULL FK visits ON DELETE SET NULL
  displaced_snapshot JSONB NULL   -- kind='displaced' のみ。復元用スナップショット:
                                  -- {"visits":[{"visit_id","start_time","end_time",
                                  --   "course_id","course_label","primary_staff_id"}]}
                                  -- 未生成週は {"pfv": true} (復元=何もしない。生成が正)
  created_at / updated_at
  部分ユニーク索引: (period_id, iso_year, iso_week, weekday, kind)
    WHERE status != 'cancelled' AND kind='extra'   -- ○は1セル1個
  索引: (iso_year, iso_week, status), (patient_id)
```

- 「配置済みだが訪問が消えた」自己回復: status='placed' かつ (placed_visit_id IS NULL
  または訪問が deleted_at 済み) はプール一覧で 'pool' 扱いに読み替える（書き戻し不要）。

## 2. 退避 (kind='displaced') のセマンティクス — 日単位

- カレンダーの固定訪問セル（週×曜日）ごとにトグル:「固定どおり」⇄「この日はプールへ退避」
- 退避ON:
  - **生成済み週**: その日のその患者の planned 訪問(全件・通常は1件)を soft-delete
    (deleted_at) し、snapshot に記録。displaced マーク(status='pool')を1行作成
  - **未生成週**: マークのみ作成 (snapshot={"pfv": true})。週生成 (Layer1) が
    displaced マークのある (patient, iso_year, iso_week, weekday) の PFV 展開を skip する
- 退避OFF (restore):
  - 未配置(pool): 生成済み週なら snapshot から訪問を復元 (deleted_at解除 or 再作成)。
    未生成週ならマーク cancel のみ
  - 配置済み(placed): `?force=true` 必須。配置先訪問を soft-delete してから復元。
    force 無しは 409 を返し FE が確認ダイアログを出す
- 恒久パターン (patient_fixed_visits) は**一切変更しない**

## 3. 週合計とカウント (PO確定)

週合計 = その週の固定訪問の残数 + extra ○ (pool/placed 両方) + displaced チケット数
(= 退避しても合計不変。○は未配置でもカウント)。
目標達成 = 週合計 >= weekly_target。「以上」判定。未達の週はカレンダーで赤系表示。

## 4. API (すべて require_role admin/manager・prefix /api/v1)

- POST /special-visit-periods {patient_id, start_date, end_date, weekly_target, note?}
  → PeriodRead。active重複は422
- GET /special-visit-periods?patient_id=…&include_inactive=false → list[PeriodRead]
- PATCH /special-visit-periods/{id} {end_date?, weekly_target?, note?, status?} → PeriodRead
- GET /special-visit-periods/{id}/calendar → CalendarRead:
  {period: PeriodRead, weeks: [{iso_year, iso_week, week_monday(date), days: [{
     weekday, date, fixed_visits: [{visit_id|null, start_time, end_time, course_label,
       staff_name|null, generated(bool)}],   -- 未生成週は PFV 投影 (visit_id=null)
     extra_mark: MarkRead|null, displaced_mark: MarkRead|null,
     preferred: [{start,end}]  -- 希望訪問カレンダー(あれば)の当該曜日の希望時間帯
  }], total: int, target_met: bool}]}
- POST /special-visit-periods/{id}/marks {iso_year, iso_week, weekday} → MarkRead
  (kind='extra'。既に extra ありは 409)
- DELETE /special-visit-marks/{id} → 204 (extra の取消。placed は ?force=true で配置先
  訪問も soft-delete。displaced はこの API ではなく restore を使う)
- POST /special-visit-periods/{id}/displace {iso_year, iso_week, weekday} → MarkRead
- POST /special-visit-marks/{id}/restore (?force=true) → 200 (§2 のとおり)
- GET /special-visit-marks/pool?iso_year&iso_week&office_id? → list[PoolTicketRead]:
  {mark: MarkRead, patient: {id, name, code, sex, sex_restriction,
   requires_multiple_staff, lat, lng, primary_office_id},
   period: {id, weekly_target, end_date},
   last_placement: {weekday, start_time, course_label, staff_name}|null}
  -- last_placement = 同一期間内の直近の placed マークの配置先 (参考ヒント・強制しない)
- POST /special-visit-marks/{id}/place {course_id?, office_id?, course_code?, start_time}
  → {mark, visit_id}  -- コース指定は course_id 直指定 or (office_id+course_code)。
  後者は mark の週・曜日で当該週の Course 実体を解決 (propose-slots 候補が持つ情報で足りる)
  -- その週の該当コース実体へ planned 訪問を作成 (source='manual_week'・PFVは作らない)。
  -- 対象週・時刻の妥当性(コース実在・時間衝突は既存の visit 作成規約に従う)を検証
  MarkRead = {id, period_id, patient_id, iso_year, iso_week, weekday, kind, status,
              placed_visit_id, placed_summary: {start_time, course_label}|null}

## 5. Layer1 (週生成) 統合

`layer1_expander` の PFV 展開時に、(patient_id, iso_year, iso_week, weekday) に
status!='cancelled' の displaced マークがあれば当該曜日の entries を skip。
extra マークは生成に影響しない（プール専用・配置されたら通常の visit 行になるだけ）。

## 6. FE

### 6-1. 設定モーダル `SpecialVisitWeekDialog` (大型・共通コンポーネント)
- 入り口2箇所: ①患者マスタ編集 (PatientForm) に「特別訪問週間」ボタン
  ②スケジュール画面の患者タップ (PatientScheduleDetailDialog) に同ボタン
- 期間未設定時: 開始日(既定=今日)・期間クイック選択チップ(1週間/2週間/3週間/4週間/
  1ヶ月/2ヶ月)・目標回数(数値・既定5)・メモ → 作成
- 期間設定済み: カレンダー表示
  - 行=週(期間内の全ISO週)・列=月〜土。期間外の日はグレーアウト
  - 各セル: 固定訪問カード(小・既存の性別カードUI流用)・希望訪問時間帯の薄敷き表示
  - 空きセルクリック → ○追加 / ○クリック → 取消(配置済みは確認)
  - 固定訪問カードのトグル:「固定どおり」⇄「この日はプールへ退避」(退避中は
    カードを打ち消し表示+チケットバッジ。配置済み退避の解除は確認ダイアログ→force)
  - 行末(右端)に週合計「5回 ✓」/ 未達は赤系「4回 (目標5)」
  - ○は未配置=○・配置済み=●(配置先時刻をツールチップ/小書き)
  - 期間の延長(end_date変更)・終了(status='ended')・目標変更
- zod スキーマは §4 と 1:1 (lib/schemas/specialVisitWeek.ts)・TanStack Query hooks
  (lib/queries/specialVisitWeek.ts)。invalidate: visits/board/pool 系

### 6-2. プール統合 (PoolPanel)
- 最上段に「⭐特別訪問週間」専用セクション (強調枠線・バッジ)。表示中の週の
  pool チケット (GET /special-visit-marks/pool) を「◯◯様・木曜・残りN」形式で表示
- チケットクリック → 既存 PoolCandidateList と同じ候補提案 UI を、対象週+
  preferred_weekdays=[チケット曜日] で呼び出し (propose-slots 再利用)。
  last_placement があれば候補リストの先頭に「前回はここでした: 火曜14:00 稲毛A」
  の参考カード (クリックでその枠を優先表示・強制しない)
- 採用 = POST /special-visit-marks/{id}/place (**PFVは作らない・この週のみ**)。
  盤面への手動配置(既存DnD等)で同等の訪問を作った場合は… (P2では place API 経由のみ
  リンク。手動配置の自動検出はスコープ外・手動なら○がプールに残るのでユーザーが
  ○を消す運用でも成立)

## 7. スコープ外 (今回やらない)

- 置換型 special_weekly_pattern の UI 整備・撤去 (据え置き)
- 未達週の通知/アラート配信 (表示のみ)
- 手動盤面配置とチケットの自動リンク
- カイポケ連携との特別扱い (配置済み訪問は普通の訪問行なので既存連携にそのまま乗る)

## 8. 実装フェーズ

- P1 (並行): BE=migration+モデル+§4 API(place以外)+Layer1統合+テスト /
  FE=設定モーダル+入り口2箇所+テスト
- P2 (並行): BE=pool/place/last_placement API+テスト / FE=プール統合+配置フロー+テスト
- P3: 統合レビュー・磨き込み・デプロイ (migration → build --no-cache)
