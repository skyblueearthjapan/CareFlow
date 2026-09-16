# 設計書: スマホ版「職員スケジュール」表示 + 取込ガード/担当 NULL の根治（2026-09-16）

調査正典 = `mobile-staff-schedule-mismatch-investigation-2026-09-16.md`（§5〜§9）。本書はその是正 2〜4 を実装単位に落としたもの。データ修復（§3-A）と W38 方針は PO 判断のため本書の範囲外（SQL 案のみ §6 に置く）。

## 0. 目標と原則
- **目標**: スマホ版（`/m/today`・`/m/this-week`）が、PC「職員スケジュール」タブでそのスタッフの行に出るもの（患者訪問＋職員イベント＋休み/時間変更）と **同じ帰属規則・同じ集合** を映す。
- **原則**: (1) 帰属の規則は BE に 1 箇所（`_staff_visibility_filter`）。FE は描くだけ。(2) 取込は「部分失敗を成功と見せない」。(3) 主担当 NULL を新たに生まない。(4) 既存の挙動（PC・送信 CSV・打刻）は壊さない。非破壊追加が基本。

## 1. レーン A: 取込ガードと取込 UI（BE integrations/replace_inbound + FE useInbound）
### A-1. 置換ガードを「カイポケにまだ残っている取消」だけに限定（BE）
- 対象: `backend/app/services/kaipoke/replace_inbound.py` `replace_week_from_kaipoke` の blocked_days 判定（現在 :195-217）。
- 現状: らく助側 `status='cancelled' and source in VISIT_SOURCES_LOCAL_CANCEL` の行があれば、その日を無条件にブロック（実適用は 422）。
- 変更: ブロックするのは **カイポケ現況 `entries` に、その取消訪問と同じ (患者・日付・開始時刻) の行が存在する場合のみ**。存在しなければ「取消は既にカイポケへ反映済み（または元々無い）」とみなし、置換を許可する（その cancelled 行は従来どおり白紙化対象）。
  - 患者一致は既存の名寄せ（`build_name_index` / `match_name`、同関数内で後段に使っているもの）を使う。時刻一致は `parse_hhmm` の分単位。日付は `day_to_date` で解決した実日付。
  - 名寄せできないカイポケ行は「一致なし」扱い（= ブロックしない）。**非稼働患者（`is_schedulable_status` でない）の行も一致なし扱い**（Phase 1 が `inactive_patient` でスキップし挿入しないため、ブロックしても意味がない。レビュー HIGH-1）。安全側に倒したい場合の議論があるが、PO 運用（事務がカイポケで直接消す）を優先し、ブロックは「復活が実際に起きる時」だけにする。
- 文言: ブロック時の detail は従来のまま。加えて「対象日を外して取り込む」導線が無いので、`ReplaceBlockedError` に `blocked_days` を持たせ、API の 422 detail を `{"message": ..., "blocked_days": [...]}` 形にせず **文字列のまま**（FE 互換）とし、日付は文中に含める（現状どおり）。
- テスト（`backend/tests/test_kaipoke_replace_inbound*.py` に追加）: (a) 取消行がカイポケ現況に残っている → 422 相当の `ReplaceBlockedError`。(b) 取消行がカイポケ現況に無い → ブロックせず置換が進む（dry_run=True で `wiped` に含まれる）。(c) 別患者・同時刻の行があるだけではブロックしない。

### A-2. 取込 apply の失敗をジョブ履歴に残す（BE）
- `backend/app/api/v1/integrations.py` `smart_inbound_apply`（:4773-5067）。現状は成功時のみ `KaipokeJob(job_type="fetch", params={"op":"smart-apply",...})` を作る（:4980 付近）。
- 変更: `ReplaceBlockedError` / 空 CSV / 差分シート不整合 の 422 を返す前に、`KaipokeJob(status="failed", params={"op":"smart-apply", "week_start":..., "error": detail}, result_summary={"error": detail})` を **別トランザクションで**（メインは rollback 済み）commit してから raise。既存の `KaipokeJob` 生成コードと同じ形（`created_by_user_id` 等）に合わせる。
- 履歴一覧（FE の jobs 表示）は既存の status を描くので、失敗行が赤で出る。`result_summary.error` を履歴の詳細に出す既存経路があればそれに載せる（無ければ `error` キーを表示する最小改修）。
- テスト: 空 CSV で 422 → `kaipoke_jobs` に failed 行が 1 件できる。

### A-3. 取込 UI（FE）
- `frontend/app/(app)/integrations/kaipoke/_components/useInbound.ts` `runApply`:
  - 訪問 apply が失敗したら **イベント apply を実行しない**（部分適用を止める）。`failed=true` のまま `parts` は空 → 「取り込み完了」トーストを出さない。
  - 失敗内容は toast ではなく **画面に残る Alert**（`InboundControls.tsx` に `applyError` を props で渡し、既存の `smartPreview.isError` Alert と同じ意匠で表示）。再プレビューまたはモード変更でクリア。
  - 「取り込み完了」トーストは **訪問・イベントとも失敗が無いときだけ**。訪問成功→イベント失敗の順でも出さず、Alert に反映済み分を含める（レビュー HIGH-3）。Alert の文言は FastAPI の detail を出す（`apiErrorMessage`・HIGH-2）。失敗後は ❸ を無効化し「❶ から取り直す」導線に（LOW-1）。
- `InboundControls.tsx`: `applyError` Alert を追加（`data-testid="smart-apply-error"`）。
- テスト（`__tests__` の既存パターンに合わせ vitest）: 訪問 apply reject → events apply が呼ばれない・Alert が出る・成功トーストが出ない。

## 2. レーン B: 主担当 NULL を生まない／検出する（BE schedule/visits/feasibility）
### B-1. `place-and-fix` は担当ありコースなら主担当を埋める
- `backend/app/api/v1/schedule.py` :1033-1044 `Visit(...)`: `primary_staff_id=course.assigned_staff_id`（None ならそのまま None = 担当なし(M) 投入は従来どおり）。`manual_staff_override` は立てない（コース担当のミラーなので）。
- 2 名体制（courses が 2 つ）は各 visit にそれぞれのコース担当。
- テスト: 担当ありコースへ place-and-fix → primary が入る。M（担当なし）へ → NULL。

### B-2. スマホ/打刻の可視性にコース担当フォールバック（BE）
- `backend/app/api/v1/visits.py` `_staff_visibility_filter` に OR 条件を追加:
  `and_(Visit.primary_staff_id.is_(None), Visit.manual_staff_override.is_(False), Visit.course_id.in_(select(Course.id).join(Staff).where(Course.assigned_staff_id == staff_id, Course.deleted_at.is_(None), Staff.deleted_at.is_(None), Staff.status == 'active')))`
  （`manual_staff_override` と退職者除外は csv_builder と同じ規則。レビュー MEDIUM-4/5）
  → 「主担当が空でコース担当が自分」の訪問が自分の一覧に出る（PC 職員スケジュールタブと同じ規則）。
- 同じ規則を **訪問詳細 `get_visit`（:600-615）と打刻 `_load_visit_for_checkin`（:1096-1110）の `visible` 判定** にも追加（一覧に出るのに詳細/打刻が 404 になるのを防ぐ）。共通ヘルパ `_course_fallback_staff_ids(db, visit)` を 1 つ作って両方から使う。
- `_serialize_visit` の `staff_name` は primary が None のときコース担当名を出す（非破壊: `staff_name` の意味は「表示すべき担当名」。`primary_staff_id` は NULL のまま返す）。既存テスト（staff_name が None を期待するもの）があれば更新。
- テスト: primary NULL・コース担当=自分 の visit が `GET /visits?staff_id=me` に出る／`GET /visits/{id}` 200／checkin が通る。コース担当が他人なら出ない。

### B-3. 検出: 実現性チェックに「主担当なし（コース担当あり）」を追加
- `backend/app/services/scheduling/feasibility_check.py` に finding を追加（kind="visit"、既存の warning 形に合わせる）: その週の planned 訪問で `primary_staff_id IS NULL AND course.assigned_staff_id IS NOT NULL`。メッセージ「コース担当（◯◯）が訪問の担当に反映されていません。スマホ盤・送信に出ません」。
- レポート HTML に既存の区分で並ぶだけ（新セクション不要ならそのまま）。
- テスト: 該当訪問 1 件で finding が 1 件。

## 3. レーン C: スマホ表示（FE mobile）
### C-1. hooks（`frontend/lib/queries/me.ts` に追加）
- `useMyStaffEvents({from, to})` → `GET /api/v1/staff/{staffId}/events?from=&to=`（staff 本人は許可済 `staff_events.py:37-41`）。型は既存 `frontend/lib/queries/staff-events.ts` の `StaffEventRead` を再利用（import して良い）。
- `useMyOverrides({from, to})` → `GET /api/v1/staff/{staffId}/overrides?from=&to=`（既存 `useStaffOverrides` があれば再利用し、staffId をセッションから渡すだけの薄いラッパにする）。
- いずれも `enabled: authenticated && !!staffId`。queryKey は `['me','events',…]` / `['me','overrides',…]`。

### C-2. イベントの畳み込み（表示側・純関数）
- `frontend/lib/schedule/foldStaffEvents.ts`（新規・単体テスト付き）: 同一 `staff_id`・同一 `starts_at`・同一 `title.trim()` の複数行を 1 件に畳む。優先順位 `kaipoke` > `manual` > `fixed`（カイポケ由来を残す）。順位は **生きている行（`cancelled_at == null`）を最優先**し、同順位なら kaipoke > manual > fixed。取消済みの複製が生きた行を隠さないための決定（レビュー MEDIUM-8・PO 確認事項）。`cancelled_at` は残した行の値。
- 現場の朝会二重（調査 §8）はこれで 1 件に見える。データ側の解消は運用（§6）。

### C-3. `/m/this-week` と `/m/today` の合成表示
- 日付ごとに 訪問（既存）＋ イベント（C-1/C-2）を **開始時刻順に混ぜて** 描く。イベントのチップは緑系（PC の意匠に合わせ `bg-success/10 text-success` 相当）、`starts_at === ends_at` は 📝 メモ扱い（時刻のみ）、`cancelled_at != null` は打消線＋「今週除外」小バッジ。`source==='fixed'` も出す（スマホは帯分離しない）。
- 休み/時間変更: その日の override があれば日付見出しの右に 🛌休み／⏱HH:MM〜HH:MM バッジ。
- 「今週だけ取消」（`status==='cancelled'`、`classifyVisitDisplay` で hidden にならないもの）: 既存の打消線に加え赤「取消」バッジ。
- `/m/today` の件数表示（subtitle）は訪問件数のまま。イベントは訪問カードの下ではなく時系列に混ぜる（訪問カード `MobileVisitCard` は変更しない。イベントは新規 `MobileEventChip`）。
- 取得窓: this-week = weekStart〜+6、today = 当日。イベント/override も同じ窓。
- 読み込み/エラー: 訪問の isLoading/isError を主とし、イベント取得失敗は Alert を出さず静かに訪問だけ描く（イベントは補助情報）。
- テスト: this-week/today の既存テストを壊さず、イベント 1 件＋訪問 1 件の混在描画・cancelled_at の打消・畳み込みのテストを追加。

## 4. 進め方
- 3 レーン並行（executor・Opus）。**ファイル分離**: A = `replace_inbound.py` / `integrations.py`（smart_inbound_apply 周辺のみ）/ `useInbound.ts` / `InboundControls.tsx`。B = `schedule.py`（place_and_fix のみ）/ `visits.py` / `feasibility_check.py`。C = `lib/queries/me.ts` / `lib/schedule/foldStaffEvents.ts` / `components/mobile/MobileEventChip.tsx` / `app/(mobile)/m/this-week|today/page.tsx`。
- 各レーン: `git` 操作禁止（stash 含む）。テストは `cd backend && python -m pytest <files> -q` / `cd frontend && pnpm vitest run <files>`・`pnpm tsc --noEmit`・`pnpm lint`。
- レビュー: code-reviewer（Opus）で 3 レーン一括 → 是正 → 全体テストを HEAD 4b9dd28 のベースライン（backend fail 30）と比較 → コミット（レーン毎）→ デプロイ（pg_dump → pull → build → recreate → healthz）。migration 無し。
- 本番検証（読み取りのみ）: admin JWT で `GET /visits?staff_id=<高岡>&week_start=2026-09-14&week_end=2026-09-20` → 9/16 に 13 件（A 7 + D 6）出ること。

## 5. 非対象・注意
- ⭐特別訪問・青ピン・DnD・提案バッジ・未送信●はスマホに出さない。
- 同行バッジは既存のまま（スマホの方が厚い）。
- `_staff_visibility_filter` は `staff` ロールの強制絞り込みにも使われる。フォールバック追加で **他人の訪問が見える方向の緩みは無い**（自分がコース担当の訪問だけ増える）。

## 6. 運用（PO 判断・本書の範囲外）
- 主担当 NULL 22 件の修復 SQL（pg_dump 後）:
  `UPDATE visits v SET primary_staff_id = c.assigned_staff_id, updated_at = now() FROM courses c WHERE c.id = v.course_id AND v.primary_staff_id IS NULL AND v.deleted_at IS NULL AND v.status = 'planned' AND v.manual_staff_override = false AND c.assigned_staff_id IS NOT NULL AND EXISTS (SELECT 1 FROM staff s WHERE s.id = c.assigned_staff_id AND s.deleted_at IS NULL AND s.status = 'active') AND v.visit_date BETWEEN '2026-09-14' AND '2026-10-04';`（退職者を担当に書かない。レビュー MEDIUM-6）
  （B-2 のフォールバックが入ればスマホ表示は修復前でも直る。CSV 送信も既にフォールバック済み。修復は整合性のため。）
- 朝会の二重（manual 31 × kaipoke 24）: らく助→カイポケ送信で昇格させるか、manual 行を削除。C-2 の畳み込みで見た目は 1 件。
- W38 方針（らく助正／カイポケ正）。A-1 が入れば小湊様の取消でブロックされなくなる。
