# 調査報告: プール／特別訪問枠の患者を「必ず」盤面へ入れる経路

作成 2026-08-31 / READ-ONLY 調査（本ファイル以外は一切変更していない）
対象課題 = `docs/plans/session-2026-08-31-HANDOFF.md` §3-c 残タスク⑤

---

## 1. 結論

### 1-1. 通常プール患者には「必ず入れられる」経路が **すでに存在する**（が誰も辿れない）

プールの患者カードを **盤面の列へ直接ドラッグ** する経路は、
`POST /api/v1/schedule/place-and-fix`（`fix_pattern=false`）を素で叩くだけで、
**空きギャップ (`no_gap`) も定員 (`capacity_full`) も昼休みも時刻重なりも一切検査しない**。
つまり本日 API 直叩きでやった「強制配置」と **同一の書き込み経路が UI に開いている**。
M（担当なし）列も dnd-kit の droppable であり、除外フィルタは存在しない。

にもかかわらず行き止まりになったのは、次の 3 点が重なったため:

| # | 障害 | 種別 |
|---|---|---|
| ① | 「配置先を探す」（propose-slots）で 0 件になったとき、**その画面から強制配置への導線がゼロ**。除外理由を並べて終わる | 導線 (FE) |
| ② | 都賀患者 → 稲毛 M は `place-and-fix` の **cross-office 422**（唯一のハードブロック） | BE ガード |
| ③ | M コースが候補に一度も出ないのは「M が除外されているから」ではなく、**提案の母集合が実 Visit からしか作られない**ため（訪問 0 件のコースはそもそも存在しないことになる） | BE 設計 |

### 1-2. ⭐特別訪問枠チケットは **本当に行き止まり**

- チケットカードは **設計上ドラッグ不可**（`SpecialTicketPlacePanel.tsx:26`）。
- 唯一の投入口 = 候補提案 UI（propose-slots）→ `POST /special-visit-marks/{id}/place`。
- その `place` は **既存の Course 実体を SELECT するだけで作らない**（`special_visits.py:983-993` → `Course not found` 404）。`place-and-fix` の `_get_or_create_course_for_template_week` に相当する救済がない。
- 候補 0 件時、通常プールにはある 2 つの逃げ道（方式b の定員超過 callout / 詰まり解消相談）が **`specialTicket` フラグで両方とも抑止**されている（`PoolCandidateList.tsx:1159, 1588`）。
- チケットの曜日は不変（`weekday` の PATCH は存在しない）。「曜日を変える」= 特別訪問週間カレンダーで ○ を消して立て直す運用。

### 1-3. `include_overcapacity` は救済にならない

定員 +1 で緩めるのは **容量判定だけ**。しかも「現在人数 ≥ base 定員」のバケットに絞り込むため、
`no_gap` で落ちたコースは +1 にしても **一件も出てこない**。
唐鎌様のケース（`no_gap`×3 + `capacity_full`×1）で `include_overcapacity=true` が 0 件だったのは仕様どおり。

### 1-4. 残すべきガードと外してよいガードの線引き（先行決定と整合）

| 残す | 外す／警告化 |
|---|---|
| 主担当拠点 NULL の 422（週生成に出ない「ねじれ」を作る） | `no_gap` / `capacity_full` / 昼休み（そもそも place-and-fix は見ていない） |
| 青ピン `week_pinned`（＝蓋） | cross-office（患者自身の主担当拠点の M/臨 に限り ack で通す案） |
| 過去日ガード（`staff-off-week` 型） | 「担当なし列は置き場」= すでに PO 公認（`unassigned-suggestions-design.md`） |
| NG スタッフ／性別制限 = 422 → ack 再送 → 管理者通知（人手経路の型） | ⭐チケットの「候補経由でしか置けない」制約 |

---

## 2. 現状の投入経路一覧

盤面 UI の実体は `frontend/components/schedule/v2/CourseDayTablePanel.tsx`（6779 行）。
DndContext（`:5020`）が **タイムライン盤面と右のプールペインの両方を包む** ので、プール↔盤面は 1 つのドラッグ空間。

| # | 経路 | 入口 (file:line) | API | 拒否条件 | M(担当なし)列 |
|---|---|---|---|---|---|
| **A** | **プールカードを列へドラッグ**（1名体制） | `CourseDayTablePanel.tsx:2318-2392` → `applyPoolDrop:2399` | `POST /schedule/place-and-fix` (`staff_count:1, fix_pattern:false`) | FE: 9:00–18:00 の範囲外のみ。BE: 主担当拠点NULL / **cross-office** / `requires_multiple_staff`↔`staff_count` / NG・性別 (422→ack) | **可**（droppable。除外なし） |
| A' | 同・2名体制 | `:2352-2385` 相方コース選択ダイアログ → `applyPartnerPlace:2455` | 同上 (`course_template_ids` 2件, `staff_count:2`) | 相方候補は `effectiveCapacity>0` の同一拠点テンプレのみ | 可（M も候補に入る） |
| **B** | **「配置先を探す」候補一覧 → 採用** | `PoolCandidateList.tsx:1187` `handleRun` → 採用 `:1334`(型) / `:1397`(今週) | 検索 `POST /schedule/v2/propose-slots`。採用 = `PUT /patients/{id}/fixed-visits` または `POST /schedule/place-and-fix` | 候補列挙側で `capacity_full` / `travel_shortage` / `lunch_window` / `no_gap` / `pair_blocked` / `no_pair_slot` / `course_closed` / NG（ハード除外） | **不可**（＝候補に出ない。理由は §3-2） |
| **C** | **空き枠クリック → 空き枠登録** | `TimelineDayBoard.tsx:1726` 「＋ここに追加」 → `SlotRegisterDialog` → `CourseDayTablePanel.tsx:1785` | `POST /schedule/place-and-fix` (`fix_pattern:false`) | **表示側ゲート**: 空き帯は 60 分未満だと出さない（`lib/scheduling/freeGaps.ts:61` `MIN_FREE_GAP_MIN=60`）＋ 頭数ゲート `remaining<=0` で非表示。2名体制患者は対象外 | **可**（`canRegisterEvent` だけが担当必須。訪問登録は担当なしでも可） |
| **D** | 一括投入 | `PoolOverviewPane.tsx:273` → `BulkPoolInsertDialog.tsx:506` | `POST /schedule/v2/pool-bulk-simulate` → `.../pool-bulk-apply` | B と同一ソルバ（`load_week_course_buckets` を共有）。定員超過は **設計上扱わない**（`pool-bulk-insert-design.md:81-83` A案） | 不可（B と同じ理由） |
| **E** | 運転席 「👤＋訪問」 | `cockpit/StaffTimelineView.tsx:1229` → `AddVisitDialog` → `CourseDayTablePanel.tsx:4507` | `POST /visits` (`source:'manual_week'`) | NG・性別は **確認なしの単純 422**（`visits.py:92-146`）。`course_id` を渡さないと盤面のコース列に出ない | ― |
| **F** | ⭐チケット採用 | `SpecialTicketPlacePanel.tsx:112` カードクリック → `PoolCandidateList` specialTicket モード → `:1289` | `POST /special-visit-marks/{id}/place` | 候補 0 件で完全な行き止まり（§3-1）。`place` は Course 実体必須・作らない | 不可 |
| G | 詰まり解消相談 | `PoolCandidateList.tsx:1588` `UnblockConsult` | `POST /schedule/v2/propose-unblock[/apply]` | 既存を 1〜2 手ずらす救急経路。**specialTicket では非表示**。B と同じ母集合 | 不可 |
| H | 盤面→プール（逆方向） | `CourseDayTablePanel.tsx:2079-2132` | `DELETE /visits/{id}` | `visit_group_id` ペアはブロック | ― |

補足:
- `VisitActionMenu.tsx:7` は **API を呼ばない**（親へコールバックするだけ）。配置経路ではない。
- 運転席の職員週盤面（`StaffWeekBoard` / `courseDnd.ts`）は HTML5 ネイティブ DnD で、**プールのペイロード型を持たない** → プールカードは落とせない。

### M 列が盤面に出る条件

`CourseDayTablePanel.tsx:754-802`（PO 2026-07-09 決定の和集合）:

```tsx
//   ① スタッフ数連動 effectiveCapacity>0 (A-E は staff_count, M系は静的 capacity)。
//   ② PFV presence: 固定訪問 (PFV) にこのテンプレ×曜日のコースが含まれる (= 正)。
//   ③ 当該曜日にこのテンプレのコースへ実在する visit がある。
if (!capacityOpen && !pfvOpen && !visitOpen) continue;
```

`effectiveCapacity`（`frontend/lib/schemas/v2/course_template.ts:132-145`）は M 系だけ静的マスタ値を使う:

```ts
const idx = courseCodeIndex(tpl.label);
if (idx === null) {
  // M系: 静的マスタ値をそのまま使う.
  return capacityForWeekday(tpl, weekday);
}
```

M テンプレの seed は **月7/火7/水7/木7/金7/土5/日0**（`alembic/versions/0033_v2_seed_m_template_all_offices.py:28`）。
→ **平日は M 列が常に表示され、常に droppable**。つまり経路 A は本日も使えた（＝行き止まりの主因は導線）。
（もし現地でマスタの M capacity を 0 に編集していれば列ごと消えるので、要実機確認。）

---

## 3. 本日のケースが行き止まりになった正確な理由

### 3-1. ⭐唐鎌様（木・時間帯 15:45–17:30・35分・稲毛・2026-W36）

**(a) propose-slots が 0 件を返した理由**

`excluded_summary = [{no_gap, 3, sample B}, {capacity_full, 1, A}]` の出所:

`backend/app/services/scheduling/proposal_solver.py:588-625`

```python
    capacity_ok = (
        used_count < _cfg_max_patients(config)
        and used_minutes + int(candidate.service_minutes) <= COURSE_MAX_MINUTES
    )
    ...
        if capacity_ok:
            slots.extend(_scan_block(sv_aug, candidate, lunch_window, "am", ...))
            slots.extend(_scan_block(sv_aug, candidate, lunch_window, "pm", ...))
        elif exclusion_sink is not None:
            # 容量上限 (件数 or 分) で am/pm 走査自体をスキップした = capacity_full.
            exclusion_sink.append("capacity_full")
```

→ A コースは 6 名到達 or 合計 480 分超で **走査自体をスキップ**。

`proposal_solver.py:798-805`（B ほか 3 コース）

```python
        if nxt is not None:
            gap_after = _travel_buffer_between(candidate.lat, candidate.lng, nxt.lat, nxt.lng, config=config)
            latest_end = _add_minutes(nxt.start_time, -gap_after)
            if _time_to_min(end) > _time_to_min(latest_end):
                if exclusion_sink is not None:
                    exclusion_sink.append("no_gap")
                continue
```

ギャップ計算は「前訪問の占有終端（同住所ペアなら 90 分底上げ・`_existing_occupancy_end:659`）＋ 移動＋バッファ → 5 分切上げ」を起点に、
「候補 end ＋ 候補→次訪問の移動＋バッファ ≤ 次訪問 start」まで要求する。
さらに `時間帯` は **start が [15:45, 17:30] に入る**ことが必要（`proposal_solver.py:374-391`）で、
`slot_feasible:400-426` が営業枠（PM 13:00–`business_end`）・18:00 超過・昼休み重複も見る。
木曜だけ 15:45 以降のギャップが全滅していた、というのが素直な読み。

**(b) `include_overcapacity=true` でも 0 件だった理由**

`propose_slots_service.py:1741-1812` `compute_overcapacity_slots`:

```python
    over_config = replace(base_config, max_patients_per_course=base_max + 1)
    ...
    for ps, _eff in enumerated:
        bucket = buckets.get((ps.office_id, ps.weekday, ps.course_code))
        if bucket is None:
            continue
        if len(bucket.visits) < base_max:
            continue  # base 定員未満 = 通常候補で既に出る枠 (超過ではない).
```

- 緩めるのは `max_patients_per_course` のみ。**時間系制約は通常とまったく同一**（docstring 明記）。
- かつ `len(bucket.visits) < base_max` のバケットを捨てる。
  → `no_gap` で落ちた B ほか 3 コースは（定員未満なので）そもそも対象外、
     `capacity_full` の A コースは +1 になっても時間走査で `no_gap` に落ちる。
  → **合計 0 件**。これは仕様どおりで、バグではない。

**(c) UI が完全に行き止まりになった理由**

`frontend/components/schedule/v2/PoolCandidateList.tsx:1159`

```tsx
  const showOvercapacityCallout =
    !specialTicket && !overcapacityRequested && (result?.overcapacity_available_count ?? 0) >= 1;
```

`:1588`

```tsx
          {hasTimeBlocker && !specialTicket ? (
            ...<UnblockConsult ... />
```

⭐モードでは **方式b callout も 詰まり解消相談も出ない**（`:115` の設計コメント「特別モードはサブフローを出さない（シンプル優先）」）。
残るのは `:1535-1546` の除外理由リストだけ = 出口ゼロ。

**(d) そしてチケットはドラッグできない**

`frontend/components/schedule/v2/SpecialTicketPlacePanel.tsx:25-26`

```
 * カードに `PatientCard` を直接使わないのは、`PatientCard` が dnd-kit の draggable
 * である一方、チケットはドラッグ配置に対応していないため (見た目だけを合わせる)。
```

**(e) 仮に配置先を指定できても、コースが無ければ 404**

`backend/app/api/v1/special_visits.py:976-993`

```python
    else:
        # propose-slots の候補は course_id を持たないため (office_id, course_code) と
        # mark 側の週・曜日で当該週のコース実体を解決する。
        course = await db.scalar(select(Course).where(
            Course.office_id == payload.office_id,
            Course.code == payload.course_code,
            Course.iso_year == mark.iso_year, Course.iso_week == mark.iso_week,
            Course.weekday == mark.weekday, Course.deleted_at.is_(None)))
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
```

`place-and-fix` 側にある `_get_or_create_course_for_template_week`（`schedule.py:659`）のような
**「無ければ proposed で作る」救済が無い**。`PlaceRequest`（`schemas/special_visit.py:127-148`）にも
`course_template_id` も `override_reason` も無い。

### 3-2. M（担当なし）コースが提案候補に一度も出なかった理由

**M が除外されているのではない。母集合に存在しない。**

`backend/app/services/scheduling/propose_slots_service.py:251-320`（`load_week_course_buckets`）は
`Visit JOIN Course` の **行を回してバケットを作る**:

```python
    stmt = (select(Visit, Course, Staff)
        .join(Course, Course.id == Visit.course_id)
        .outerjoin(Staff, Staff.id == Course.assigned_staff_id)
        .where(Visit.deleted_at.is_(None), Visit.status == VISIT_STATUS_PLANNED, ...))
    rows = (await db.execute(stmt)).all()
    ...
    for v, course, staff in rows:            # :321
        ...
        bucket = buckets.get(key)
        if bucket is None:
            bucket = _CourseBucket(...)      # :329 — バケットは visit 行からしか生まれない
```

→ **訪問 0 件のコース（今回の 稲毛M / 都賀M）は buckets に 1 件も入らない**。
`_enumerate_candidate_slots:912` は `for (office_id, weekday, course_code), bucket in buckets.items():` で回すので、
走査対象にすらならない。M 特有の除外ではなく、**空コース全般**が不可視。

その結果、`_aggregate_exclusions:1459-1477` は「その曜日にバケットが 1 つも無い」場合のみ
`course_closed` を補完するが、他コースにバケットがある曜日では **M の不在は会計にも出ない**（黙って消える）。

一方 **担当未割当そのものは除外条件ではない**。`propose_slots_service.py:756-820` `_slot_reasons_and_warnings`:

```python
    # N-3: 割付スタッフ実態の警告 (除外はせず注意喚起のみ). FE が日本語ラベル化する.
    if staff_unassigned:
        warnings.append(_WARN_STAFF_UNASSIGNED)
```

→ M に訪問が 1 件でもあれば、警告付きで候補に出る設計になっている。
コード上の M 名指しフィルタは `propose_slots_service.py` / `pool_bulk_inserter.py` / `schedule_v2.py` に **存在しない**（grep 済み）。

なお `pool_bulk_inserter.py:460` と `unblock_search.py`（`compute_all_proposed_slots` 経由）も
**同じ `load_week_course_buckets` を共有**しているので、一括投入も詰まり解消相談も同じ盲点を持つ。

### 3-3. 都賀患者 → 稲毛M が 422 になった理由

`backend/app/api/v1/schedule.py:955-971`

```python
        if patient.primary_office_id is None:
            raise HTTPException(422, detail="主担当拠点が未設定のため配置できません。...")
        for tpl_id, tpl in templates_by_id.items():
            if tpl.office_id != patient.primary_office_id:
                raise HTTPException(422, detail=(
                    "patient.primary_office_id と course_template.office_id "
                    f"が一致しません (template={tpl_id}, cross-office drop is not allowed)"))
```

W18 Codex-fix 中-4 のガード。FE 側に事前判定は無いため、**ドラッグしてから赤トーストで初めて分かる**
（`applyPoolDrop:2436` `toast.error('配置に失敗しました: ...')`）。

### 3-4. `place-and-fix` には時間・定員の検査が一切ない（＝すでに「強制配置」）

`schedule.py:833-1160` `place_and_fix` の全ガードは以下だけ:

1. `require_role("admin")`
2. ISO 週の妥当性（`:886`）
3. `start_time + duration_min < 24:00`（`:897`）
4. patient 存在（`:917`）
5. `requires_multiple_staff` ↔ `staff_count=2`（`:926`）
6. 主担当拠点 NULL（`:955`）／ cross-office（`:963`）
7. NG スタッフ・性別制限 → 422 → `acknowledge_constraint_warnings=true` 再送 + 管理者通知（`:1005-1013`）

**`no_gap` / `capacity_full` / `lunch_window` / 時刻重なり / 営業時間 / 同住所 90 分占有 / 青ピン / 過去日 の検査は無い。**
`capacity_override_reason` も検証には使われず、`logger.info` に落とすだけ（`:876-881`）:

```python
    if body.capacity_override_reason:
        logger.info("capacity_override on place-and-fix: patient_id=%s reason=%s", ...)
```

`_get_or_create_course_for_template_week`（`:659-730`）は Course が無ければ
`course_status='proposed'` で作り、`template.label` が A–E/M 以外なら **code を 'M' に丸める**。
→ 「置き場が無ければ作る」も既に備わっている。

また `courses.py` モデルの定義上、M は元々そのための枠:

`backend/app/models/course.py:64-66`
```
# A/B/C/D/E = 通常コース, M / M2-M9 = マネージャー枠 (通常コース外, overflow 用に分散).
```
`backend/app/services/scheduling/auto_allocator_v2.py:242-248` も M/M2..M9 を overflow set と定義している。

---

## 4. 「必ず入れられる」ための設計案

方針: **新しい強制ロジックを作らない。既に強制である `place-and-fix` へ「見える出口」を接続する。**
警告は G-84 の既存語彙（赤=理由必須で強行 / 黄=承知チェック）を流用し、`propose-slots` のアルゴリズムには手を入れない。

### F-1【最優先・S】候補 0 件の空状態に「出口」を出す（BE 変更ゼロ）

**やること**: `PoolCandidateList` の empty state（`:1533-1609`）に、除外理由リストの直下へ 2 つの導線を足す。

1. 「**担当なし（M）枠に入れる**」ボタン — 患者の `primary_office_id` の M テンプレ × 希望曜日 × 希望時刻（`preferred_start` or 帯の頭）を既定値にしたミニダイアログ → 既存 `usePlaceAndFix`（`fix_pattern:false`）。
2. 「盤面の列へ直接ドラッグすれば、枠が無くても置けます」の 1 行ヒント（既存経路 A の可視化）。

**変更ファイル**
- `frontend/components/schedule/v2/PoolCandidateList.tsx`（empty state に分岐追加）
- `frontend/components/schedule/v2/PatientScheduleDetailDialog.tsx`（M テンプレ解決のため `templates` を渡す）
- 既存 `frontend/lib/queries/place_and_fix.ts` を再利用（新 API 不要）

**残すガード**: cross-office はそのまま（患者自身の拠点の M を選ぶので抵触しない）。NG/性別は既存の 422→ack ダイアログ（`placementConstraintConfirm`）に乗る。
**規模 S**（FE のみ・~150行）。**リスク**: ほぼ無し。ただし「担当なし」のまま残るとカイポケへ送れない（§4 リスク欄）。

### F-2【最優先・S/M】⭐チケットの行き止まりを塞ぐ

**BE**
- `PlaceRequest`（`backend/app/schemas/special_visit.py:127`）に `course_template_id: UUID | None` と `override_reason: str | None` を追加。
- `place_mark`（`backend/app/api/v1/special_visits.py:955`）で `course_template_id` 指定時は
  `_get_or_create_course_for_template_week`（`schedule.py:659`）を共有ヘルパへ切り出して呼び、**Course が無ければ作る**。
  現行の `course_id` / `(office_id, course_code)` 経路は不変（後方互換）。
- cross-office は place-and-fix と同じ規則を追加（現在チェック無し＝むしろ緩い。**片方だけ厳しい非対称を解消**）。

**FE**
- `PoolCandidateList.tsx:1588` の `!specialTicket` ガードを外し、⭐でも「担当なし（M）枠に入れる」ボタン（F-1 と同じ部品）を出す。
  詰まり解消相談・方式b の抑止（シンプル優先の設計判断）はそのまま維持してよい。
- `SpecialTicketPlacePanel.tsx` のカードに「カレンダーで曜日を変える」既存ボタンの説明を添える（`weekday` PATCH は作らない）。

**残すガード**: `course.iso_year/iso_week/weekday == mark.*` の一致（`:997-1003`）、NG/性別 ack、`MARK_STATUS_PLACED` の二重配置 409。
**規模 M**（BE ~120行 + FE ~80行）。
**リスク**: 設計書 §7 スコープ外の「手動盤面配置と○の自動リンクなし」は据え置き → 強制配置してもチケットは自動で消える（`place` 経由なので `MARK_STATUS_PLACED` になる。DnD 案 F-6 と違ってここは安全）。

### F-3【M】propose-slots に「空きコース」を候補として載せる

**やること**: `load_week_course_buckets`（`propose_slots_service.py:251`）に、
当該週 × 拠点 × 曜日の `courses` 行（訪問 0 件も含む）と、`course_templates` × 開講曜日から
**空バケット**を補完する（`_CourseBucket(visits=[])`）。ソルバは既存訪問 0 のギャップ走査を素直に通す。

これで M も「担当未定枠」として候補に出る（`staff_unassigned` 警告付き・スコアは `_balance_ratio` で自然に下位）。

**変更ファイル**
- `backend/app/services/scheduling/propose_slots_service.py`（ローダのみ。列挙・スコアは不変）
- 影響先は `schedule_v2.py` propose-slots / pool-overview / pool-bulk / unblock（全部同じローダを共有）

**注意（先行事故の再発）**: `docs/plans/session-2026-07-10-HANDOFF.md:112` に
「受け入れ枠の母集合に **空の M コース（course_fixed・患者0）が含まれ** `remaining = 6 - 0 = 6`」という
**バグ事例**がある。受入枠マトリクス（`acceptance_matrix_service.py`）の母集合には波及させないこと。
安全策として `include_empty_courses: bool = False` のオプトインにし、まず PoolCandidateList だけ true で送る。

**残すガード**: NG ハード除外、`course_closed` 会計。
**規模 M**（BE ~150行 + テスト）。**リスク**: 中。提案の順位が変わる可能性（M が下位に大量に出て埋もれる）→ `limit` 内の表示バランスを要確認。

### F-4【M】強制配置の契約を明文化する（`override_reason` + 再判定 warnings）

現状 `capacity_override_reason` はログ止まり。G-84（`docs/plans/phase-g84-slot-direct-placement.md:204-214`）の
契約に揃える:

| level | code | 扱い |
|---|---|---|
| 🔴 red | `capacity_full` / `time_overlap` | 理由必須・二段確認・強行可 |
| 🟡 yellow | `travel_shortage` / `outside_hours` / `sex_restriction` / `multi_staff` | 承知チェック |
| 🟡 yellow（**新設**） | `no_gap` | 承知チェック（G-84 の語彙に無い。今回の主因なので追加が要る） |

**やること**
- `PlaceAndFixRequest` に `override_reason`（`capacity_override_reason` を包含する新名・旧名は alias で維持）。
- `place_and_fix` で **配置後に** 軽量な再判定（同コース同日の時刻重なり／`len(visits) >= max_patients`）を行い、
  赤があるのに理由が空なら 422、あれば `AuditLog` + 管理者お知らせ（既存 `notify_constraint_override_for_course` と同じ型）。
  ※ **判定を新規ソルバで作らない**（`_course_total_minutes_from_existing` などの既存部品だけ）。
- FE: 強制配置ボタン／ドラッグ時に赤が予測されるなら理由入力を求める。

**残すガード**: 全部（これは追加のみ）。
**規模 M**。**リスク**: 低。ただし「1人ずつ・理由必須」の原則（`pool-bulk-insert-design.md:81-83`）に従い、
**一括投入には絶対に組み込まない**。

### F-5【M・要 PO 判断】cross-office の緩和

- 案a: **患者の主担当拠点の M/臨 に限り**、他拠点コースへの配置を明示 ack で許可。
- 案b: cross-office を 422 → 警告（`cross_office` code）に降格し、Layer3 の「拠点跨ぎ救援は警告で通す」
  （`docs/plans/layer3-staged-mobilization-design.md:382,390` `cross_office_notices`）と対称にする。

**制約**: 他拠点テンプレを指す PFV が **24 行/18 名**存在し「意図しているか」の
**お客様確認が保留中**（`docs/plans/session-2026-08-17-HANDOFF.md:61-65`）。既成事実を作る前に PO 確認が必須。
**規模 M**。**リスク**: 高（データ品質・週生成の帰属）。→ 本件は F-1/F-2 で「自拠点の M へ入れる」を先に用意すれば **急がない**。

### F-6【L】⭐チケットの DnD 化

`SpecialTicketPlacePanel` のカードを `PatientCard` 相当の `useDraggable` にし、
`pool-patient:` とは別の名前空間（例 `special-ticket:{markId}`）で `tl-col:*` に落とせるようにする。
`handleDragEnd`（`CourseDayTablePanel.tsx:2049`）に分岐を足し、`place-and-fix` ではなく
`POST /special-visit-marks/{id}/place`（F-2 で `course_template_id` 対応済）を呼ぶ。
落とせる曜日は `mark.weekday` に限定（BE が 422 で守る）。

**規模 L**（FE ~250行 + テスト）。**リスク**: 中（曜日不一致 UX、`kind='displaced'` の退避マークとの混線）。

### テスト（追加すべきもの）

| 対象 | ファイル | 内容 |
|---|---|---|
| BE | `backend/tests/test_place_and_fix.py` | 満員コース（6名）・ギャップ無しの時刻へ配置 → **200**（回帰防止＝強制であることを固定する） |
| BE | 同上 | 担当なし（`assigned_staff_id=None`）M テンプレへの配置 → 200 |
| BE | `backend/tests/test_special_visit_week.py` | (1) 当該週の Course 実体が無い `course_template_id` 指定 → Course が作られて 201 (2) `weekday` 不一致 → 422 (3) cross-office → 422 |
| BE | 新規 `backend/tests/test_propose_slots_empty_course.py` | 訪問 0 件の M コースが `include_empty_courses=true` で候補に出て `staff_unassigned` 警告が付く／既定 false では従来どおり出ない |
| BE | 新規 `backend/tests/test_place_and_fix_override.py` | 赤警告あり + `override_reason` 空 → 422 / 理由あり → 200 + AuditLog + 通知 |
| FE | 新規 `frontend/components/schedule/v2/__tests__/PoolCandidateList-empty-exit.test.tsx` | 候補 0 件のとき「担当なし（M）枠に入れる」が出る／⭐モードでも出る |
| FE | 既存 `SlotRegisterDialog.test.tsx` に追記 | 担当なし列でも訪問登録できる（イベント登録は不可） |

### リスク一覧（設計時に必ず潰すもの）

| リスク | 内容 | 対応 |
|---|---|---|
| **カイポケ送信不可** | 担当なしのまま置くと RPA `edit_staff` が `-` を扱えない（`docs/plans/incident-2026-08-31-kaipoke-expand-wrong-month.md:84`、`session-2026-08-31-HANDOFF.md:39` で `after.staff1=='-'` は除外） | 強制配置した訪問に「担当未定」バッジ＋送信前チェックの警告項目を追加。`assign-candidates`（担当なし→人を探す）で回収する既存導線へ誘導 |
| **警告が鳴り続ける** | 定員超過の**承認記憶が未実装**（`advisor-consult-refinement-HANDOFF.md:116` ほか 3 ファイルでバックログ）。強制配置分を診断・改善提案・実現性チェックが毎回蒸し返す | F-4 の `AuditLog` を将来の承認記憶の種にしておく。当面は既知の負債として明記 |
| **同住所ペア 90 分占有の破壊** | `SAME_ADDRESS_PAIR_MIN_OCCUPANCY=90` の占有中に強制で差し込むとペア表示・ペア移動が壊れる | 強行時に `time_overlap` 赤として検出（F-4）。ペア枠への差し込みは黄→赤に格上げ検討 |
| **受入枠マトリクスの定員会計** | F-3 で空コースを母集合に足すと `remaining` が水増しされる（2026-07-10 の実バグ） | `acceptance_matrix_service.py` には波及させない。オプトインで分離 |
| **エンジンの前提** | 強制配置は `source='manual_week'`（`fix_pattern=false`）なので週生成・固定枠戻から保護される（`VISIT_SOURCE_MANUAL_WEEK`）。型は変わらない | 意図どおり。ただし「翌週は消える」ことをトーストで明示（既存 `promoteToastAction` が昇格導線を持つ） |

---

## 5. 先行決定との整合

| 先行決定 | 出典 | 本提案との関係 |
|---|---|---|
| **余白の原則**（システムが患者の予定を動かすとき、使ってよいのは希望の余白だけ） | `docs/plans/schedule-advisor-design.md:96-108` / `advisor-consult-refinement-HANDOFF.md:12-13` | **抵触しない**。強制配置は**対象患者 1 件を人が置くだけで、他患者を一切動かさない**。射程外。ただし歯止めの様式「発動条件＝候補0件＋明示操作のみ」は踏襲し、**常時表示のボタンにしない** |
| **エンジンだけ縛り、人手は自由（ただし見える化）** | `docs/plans/patient-ng-staff-design.md:46-57` / `pin-and-movability-spec.md:41-57`（2026-08-09 に place-and-fix の赤 422 を撤廃した実績） | **最も直接的な支持根拠**。強制配置は人手経路。ブロックせず、確認 UI + 監査 + 管理者通知をセットにする（F-4） |
| **N-6「黙って消さない／諦めない」** | `docs/plans/scheduling-logic-normalization.md:190` / `pool-unification-design.md:34-39` | 除外理由は既に FE まで届いている。**理由を出したところで止まっているのが今の行き止まり**。そこに出口を足す F-1 は N-6 の自然な延長 |
| **赤警告は理由付きで強行可（二段確認）** | `docs/plans/phase-g84-slot-direct-placement.md:18-19, 204-214` | `capacity_full` は既に「ブロックではなく理由必須で強行」と PO 合意済み。F-4 は語彙・契約をそのまま流用。**`no_gap` だけ語彙に無いので新設** |
| **超過は 1 人ずつ・理由必須（一括禁止）** | `docs/plans/pool-bulk-insert-design.md:81-83`（A案・PO 承認 2026-07-04） | **制約**。強制配置を一括投入に組み込まない。個別フローのみ |
| **詰まり解消相談の適用範囲は閉じた**（拡張は 2 件のみ・これ以上広げない） | `docs/plans/advisor-consult-refinement-HANDOFF.md:16-17` / `schedule-advisor-design.md:120-126` | **制約**。強制配置を unblock の第 3 拡張として実装してはいけない。**人手の直接配置経路として別に設計**し、`propose-unblock` / `propose-slots` のアルゴリズムには手を入れない（F-3 はローダの母集合追加であって探索ロジック変更ではない、という線引きを守る） |
| **「担当なし」は置き場＋戻し先**（PO 判断 2026-08-22） | `docs/plans/session-2026-08-22-HANDOFF.md:34` / `unassigned-suggestions-design.md:3-4,24` | **強い支持**。「まず担当なしへ入れる → `assign-candidates` で人を探して戻す」の往復が既に本番稼働。F-1/F-2 はこの型に乗るだけ |
| **M はマネージャー枠であって置き場ではない** | `docs/plans/v2-allocation-redesign.md:1680,1690-1696` / `backend/app/models/course.py:64-66` | **要注意**。M は overflow 用ではあるが「マネージャー 1 名 = 1 コース」の割当先でもある。長期的には **臨時コース「臨」**（`kaipoke-reverse-sync-design.md:238,246-253`・PO 発案・本番稼働）を強制配置の受け皿にする方が整合的。ただし残骸掃除の運用負債あり |
| **拠点跨ぎ**: 患者側は 422・未対応／スタッフ側の応援は警告で通す（PO 承認） | `pool-bulk-insert-HANDOFF.md:95` / `layer3-staged-mobilization-design.md:297-302,382` | **非対称**。患者側の緩和（F-5）は他拠点 PFV 24 行の保留案件に既成事実を作るため **PO 確認が先** |
| **⭐チケット: 手動盤面配置と○の自動リンクなし（スコープ外）** | `docs/plans/special-visit-week-design.md:142-147` / `session-2026-07-29-HANDOFF.md:83-85` | F-2 は `place` API を通すのでリンクは保たれる（○が自動で placed になる）。DnD 化 F-6 も同 API を呼ぶ限り安全 |

---

## 6. 段階案（S から）

### Step 0（0.5h・コード変更なし）— 実機で前提を確認する

1. 盤面（スケジュール画面・日タイムライン）に **稲毛M / 都賀M の列が実際に表示されているか**（木曜）。
   → 表示されていれば、**通常プール患者は今日この瞬間もドラッグで入れられる**。PO へその場で共有できる。
2. M テンプレのマスタ定員（`capacity_thu` 等）が seed の 7 のままか（誰かが 0 にしていないか）。
3. 木曜 M 列の空き帯（60 分以上）に「＋ここに追加」ボタンが出ているか（経路 C の生存確認）。

### Step 1【S / 半日】F-1 — 候補 0 件の出口（FE のみ・BE 変更ゼロ）

- 通常プール患者の行き止まりが即日解消。
- 「盤面へドラッグでも置けます」ヒントで既存経路 A を可視化。
- リリース単位が小さく、既存 API 契約を一切変えないので回帰リスクが最小。

### Step 2【S〜M / 1日】F-2 — ⭐チケットの出口

- `PlaceRequest` に `course_template_id` を足し、Course を作れるようにする。
- specialTicket でも F-1 の出口ボタンを出す。
- これで **プールも特別枠も「必ず入れられる」が UI 上で成立**（＝ PO 要件の充足ライン）。

### Step 3【M / 2日】F-4 — 強制配置の契約化（`override_reason` + 赤/黄再判定 + 監査 + 通知）

- 「入れられる」ようになった後で「誰が・なぜ無理に入れたか」を残す。順序はこちらが後で正しい
  （先に契約を作ると Step 1/2 が重くなる）。
- `no_gap` を黄警告語彙に新設。

### Step 4【M / 2〜3日】F-3 — 空きコースを提案候補に載せる

- 「担当未定枠」として M が候補一覧に自然に出るようになり、Step 1 の出口ボタンが
  「最後の手段」から「正規候補の 1 つ」へ格上げされる。
- `include_empty_courses` オプトインで開始し、受入枠マトリクスには波及させない。

### Step 5【要 PO 判断】F-5（cross-office）／ F-6（チケット DnD）

- F-5 は他拠点 PFV 24 行の確認待ち案件と抱き合わせで PO へ。
- F-6 は体験の磨き込み。Step 2 が済んでいれば緊急性は無い。

### PO に確認すべきこと（実装前）

1. 強制配置の受け皿は **M（マネージャー枠）** でよいか、**臨時コース「臨」** を使うか。
   （M はマネージャー割当先でもあるため、長期的には「臨」の方が整合的）
2. 強制配置に **理由入力を必須**にするか（G-84 の赤契約に揃えるか、まず理由なしで通すか）。
3. 拠点跨ぎ（都賀患者を稲毛コースへ）を **通すか、自拠点 M への誘導で足りるか**。
4. 担当なしのまま残った訪問を **カイポケ送信前にブロックするか、警告に留めるか**。

---

## 付録: 主要ファイル

### バックエンド
- `backend/app/api/v1/schedule.py:485-660`（`PlaceAndFixRequest` / `_get_or_create_course_for_template_week`）、`:823-1160`（`place_and_fix`）
- `backend/app/api/v1/special_visits.py:950-1057`（`place_mark`）、`:561-610`（`create_extra_mark`）、`:863-916`（`list_pool`）
- `backend/app/schemas/special_visit.py:127-154`（`PlaceRequest`）
- `backend/app/schemas/v2/propose_slots.py:70-125`（`ProposeSlotsRequest` / `include_overcapacity`）
- `backend/app/api/v1/schedule_v2.py:3510-3712`（propose-slots 本体）、`:3811`（pool-overview）、`:3934/4045`（pool-bulk）、`:6043`（propose-unblock）
- `backend/app/services/scheduling/propose_slots_service.py:251-433`（バケットローダ＝本件の核心）、`:756-820`（警告）、`:912-1123`（列挙）、`:1459-1508`（除外会計）、`:1741-1812`（定員+1）
- `backend/app/services/scheduling/proposal_solver.py:350-426`（time_type / slot_feasible）、`:533-660`（容量＋2段イベント）、`:659-698`（同住所 90 分占有）、`:698-830`（ギャップ走査＝`no_gap`）
- `backend/app/services/scheduling/pool_bulk_inserter.py:460`、`backend/app/services/scheduling/unblock_search.py`（同じローダを共有）
- `backend/app/models/course.py:57-70`（M = overflow）、`backend/app/models/course_template.py:52-60`（`capacity_*`）
- `backend/app/services/scheduling/constants.py:26,31`（`MAX_PATIENTS_PER_COURSE=6` / `COURSE_MAX_MINUTES=480`。**`course_templates.capacity_*` はソルバに使われていない**）
- `backend/alembic/versions/0033_v2_seed_m_template_all_offices.py:28`（M 定員 月7/火7/水7/木7/金7/土5/日0）

### フロントエンド
- `frontend/components/schedule/v2/CourseDayTablePanel.tsx:754-802`（列表示条件）、`:2049`（`handleDragEnd`）、`:2318-2392`（プール drop）、`:2399-2442`（`applyPoolDrop`）、`:2455-2501`（2名体制）、`:1743-1841`（空き枠登録）、`:5020`（DndContext）
- `frontend/components/schedule/v2/PoolCandidateList.tsx:1150-1160`（`hasTimeBlocker` / `showOvercapacityCallout`）、`:1187-1233`（propose 実行）、`:1289-1326`（⭐採用）、`:1533-1609`（空状態＝**改修の主戦場**）
- `frontend/components/schedule/v2/SpecialTicketPlacePanel.tsx:25-26`（DnD 不可の理由）、`:90-91`（0件で非表示）、`:112-186`
- `frontend/components/schedule/v2/PoolPanel.tsx:45-96`（draggable id 規約）、`PatientCard.tsx`
- `frontend/components/schedule/timeline/TimelineDayBoard.tsx:87-119`（`tl-col:` droppable id）、`:979-983`（`ColumnDropLayer`）、`:1692`、`:1726-1740`（空き帯ボタン）、`:1871`（`（未割当）`）
- `frontend/components/schedule/timeline/SlotRegisterDialog.tsx:1-70`
- `frontend/lib/scheduling/freeGaps.ts:61`（`MIN_FREE_GAP_MIN=60`）、`:122-197`
- `frontend/lib/schemas/v2/course_template.ts:110-145`（`courseCodeIndex` / `effectiveCapacity`）
- `frontend/lib/queries/place_and_fix.ts:23`、`frontend/lib/queries/specialVisitWeek.ts:360`、`frontend/lib/queries/unblock.ts`

### 設計書
- `docs/plans/phase-g84-slot-direct-placement.md:18-19, 204-214`（赤/黄警告契約 — **流用の型**）
- `docs/plans/patient-ng-staff-design.md:36-57`（エンジンだけ縛り、人手は自由）
- `docs/plans/pin-and-movability-spec.md:41-57`（人手経路の 422 撤廃実績）
- `docs/plans/schedule-advisor-design.md:96-126`（余白の原則・拡張禁止）
- `docs/plans/pool-unification-design.md:34-39`（P-1b 除外理由）
- `docs/plans/pool-bulk-insert-design.md:81-83`（超過は 1 人ずつ・理由必須）
- `docs/plans/special-visit-week-design.md:97-99, 131-147`（⭐の配置 API とスコープ外）
- `docs/plans/unassigned-suggestions-design.md:3-24`（担当なし = 置き場＋戻し先）
- `docs/plans/kaipoke-reverse-sync-design.md:238, 246-253`（臨時コース「臨」）
- `docs/plans/session-2026-07-10-HANDOFF.md:112`（**空 M コースを母集合に入れた過去バグ** — F-3 の注意点）
- `docs/plans/session-2026-08-17-HANDOFF.md:61-65`（他拠点 PFV 24 行・PO 確認保留）

---

## PO 決定（2026-08-31 22:40 追記）
- 受け皿は **M コース（担当なし）**。拠点跨ぎは通さず、**患者の自拠点の M へ誘導**する（F-5 は不採用）。
- 実装順: F-1（候補 0 件の画面に「担当なし(M)へ入れる」・FE のみ）→ F-2（⭐チケットにも同じ出口・`place` で M コースを自動生成）。
- 未決: 強行時の理由入力の要否（F-4）／担当なし訪問のカイポケ送信を止めるか警告か。
