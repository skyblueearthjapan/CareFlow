"""カイポケ置換取り込み — 週を白紙化してカイポケ現況で書き直す (全置換モード).

背景 (2026-07-26 PO確定): カイポケは請求と紐づく最終的な「正」であり、
らく助側がそれを受け入れる。差分突合 (diff-inbound) は同期済み週の追いかけには
適するが、一度も同期していない週では名寄せ差 (氏名の空白違い等) や同時刻衝突で
見かけの差分・失敗が大量に出る。置換モードは対象週のらく助訪問を全削除
(soft delete) し、カイポケ現況CSVの行をそのまま挿入する — 突合が無いので
これらの問題が構造的に発生せず、結果は必ずカイポケの完全なコピーに収束する。

安全装置:
  * ゲート = 差分取り込みと同一 (過去/今週 or 実apply記録・inbound_week_eligible)
  * 実績ガード = 対象週の訪問に打刻/写真/レビューが1件でもあれば置換不可
    (実績記録の紐付け先を消さない。その週は差分モードを使う)
  * dry-run 既定 = 書込なしで計画 (挿入n/削除n/対象外) だけ返す
  * 白紙化と挿入は同一トランザクション (中途半端な白紙状態を残さない)
  * UI 側は「らく助側のこの週の情報はすべて削除される可能性がございます」を
    明記した確認ダイアログを必須とする (PO指示)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.accompaniment import Accompaniment
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.course_template import CourseTemplate
from app.models.patient import Patient
from app.models.visit import (
    VISIT_SOURCE_MANUAL_CANCEL,
    VISIT_STATUS_CANCELLED,
    Visit,
)
from app.models.visit_checkin import VisitCheckin
from app.services.accompaniment import (
    resolve_accompaniment_kind,
    resolve_accompaniment_staff_by_course,
)
from app.services.kaipoke.inbound import (
    _replace_assignments,
    _stamp_note,
    day_to_date,
    ensure_temp_course,
    load_staff_name_index,
    load_week_course_index,
    parse_hhmm,
)
from app.services.kaipoke.name_match import build_name_index, match_name
from app.services.kaipoke.ng_conflicts import NgConflict, NgPair, collect_ng_conflicts

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.diff.engine import ScheduleEntry


class ReplaceBlockedError(RuntimeError):
    """実績ガード: 打刻等が付いた週は置換できない (差分モードを使う)。"""


@dataclass
class _ResolvedRow:
    """Phase 1 で名寄せ解決済みのカイポケ行 (Phase 2/3 の入力)。"""

    entry: ScheduleEntry
    patient: Patient
    d: date
    start_t: time
    end_t: time
    sid: uuid.UUID
    sid2: uuid.UUID | None
    office_id: uuid.UUID


@dataclass
class ReplaceSkip:
    """挿入できなかったカイポケ行 (隠さず可視化する)。"""

    reason: str
    user_name: str
    staff_name: str
    date: str
    start: str


@dataclass
class ReplaceResult:
    week_start: date
    week_end: date  # 月〜土
    dry_run: bool
    wiped: int = 0
    inserted: int = 0
    sunday_skipped: int = 0
    temp_courses: int = 0
    skipped: list[ReplaceSkip] = field(default_factory=list)
    # ⚠警告: 新人 (is_trainee) が担当1の単独訪問 (staff_name → 件数)。
    # カイポケは最終的な「正」のため取り込むが (PO確定 2026-07-26)、新人フラグの
    # 見直し判断材料として可視化する。
    trainee_solo: dict[str, int] = field(default_factory=dict)
    # コース担当をカイポケの現実に合わせて付け替えた数 (2026-07-26 改修)。
    # 「コース丸ごと交代」と同じ考え方で、臨時コース乱立 (→ 表示合算で1枠15件) を防ぐ。
    courses_reassigned: int = 0
    # 未使用の通常テンプレート (例: 稲毛E) からコース行を新設した数 (2026-07-26)。
    # 既存コース行が足りなくても、テンプレートに空きがあれば臨時ではなく正規の
    # 枠を立てる — 臨時の合算表示 (臨+臨2 で 9名/枠) を構造的に減らす。
    courses_created: int = 0
    # ⛔ NG スタッフ (patient_ng_staff) に該当する組み合わせ (dry-run のみ集計)。
    # カイポケが「正」なので取り込みはブロックしない — 警告として可視化するだけ。
    ng_conflicts: list[NgConflict] = field(default_factory=list)
    # 日ごとの内訳 {日付: {"wiped": n, "inserted": n}} — 連携結果レポートの
    # 明細 (kind='day') の材料。置換対象になった日は 0 件でもキーを持つ
    # (「その日は置換した・結果 0 件だった」と「触っていない」を区別する)。
    per_day: dict[date, dict[str, int]] = field(default_factory=dict)


async def _count_week_achievements(db: AsyncSession, visit_ids: list[uuid.UUID]) -> int:
    """対象訪問に紐づく実績 (打刻) の件数。写真/レビューは checkin 起点のため
    checkin の存在確認で実績週を検出できる。"""
    if not visit_ids:
        return 0
    n = await db.scalar(
        select(func.count(VisitCheckin.id)).where(VisitCheckin.visit_id.in_(visit_ids))
    )
    return int(n or 0)


async def week_checkin_days(db: AsyncSession, week_start: date) -> set[date]:
    """対象週 (月〜土) のうち「打刻実績が1件でもある日」の集合を返す。

    smart-inbound (日単位ハイブリッド・2026-07-26) の判別信号:
    打刻あり日 = 差分モード (行を残して直す) / なし日 = 置換モード。
    """
    mon = week_start
    sat = week_start + timedelta(days=5)
    rows = await db.execute(
        select(Visit.visit_date)
        .join(VisitCheckin, VisitCheckin.visit_id == Visit.id)
        .where(
            Visit.deleted_at.is_(None),
            Visit.visit_date >= mon,
            Visit.visit_date <= sat,
        )
        .distinct()
    )
    return {r[0] for r in rows.all()}


async def replace_week_from_kaipoke(
    db: AsyncSession,
    *,
    week_start: date,
    entries: list[ScheduleEntry],
    dry_run: bool,
    now: datetime,
    target_days: set[date] | None = None,
) -> ReplaceResult:
    """対象週 (月〜土) を白紙化し、カイポケ現況 entries で書き直す。

    target_days 指定時 (smart-inbound の日単位ハイブリッド) はその日だけを
    白紙化+挿入する (他の日は差分モードの担当)。None = 週全体 (従来動作)。
    dry_run=True は一切 mutate しない (計画のみ返す)。
    Raises: ReplaceBlockedError (実績ガード) / ValueError (週開始が月曜でない)。
    """
    if week_start.weekday() != 0:
        raise ValueError(f"week_start must be a Monday: {week_start}")
    week_sat = week_start + timedelta(days=5)
    week_sun = week_start + timedelta(days=6)
    today = now.date()

    result = ReplaceResult(week_start=week_start, week_end=week_sat, dry_run=dry_run)

    # --- 白紙化対象 (active な全訪問・キャンセル済み含む) ---------------------
    wipe_stmt = select(Visit).where(
        Visit.deleted_at.is_(None),
        Visit.visit_date >= week_start,
        Visit.visit_date <= week_sat,
    )
    if target_days is not None:
        wipe_stmt = wipe_stmt.where(Visit.visit_date.in_(sorted(target_days)))
    wipe_rows = list((await db.scalars(wipe_stmt)).all())
    result.wiped = len(wipe_rows)

    # --- 「今週だけ取消」ガード (week-cockpit-design.md D1) -------------------
    # 置換は対象日の visit を cancelled 含めて白紙化しカイポケ現況で作り直す。
    # らく助側で「今週だけ取消」した枠 (= まだ⇧送信していない) がそこに居ると、
    # 取消が黙って復活してしまう。**日単位**で止める:
    #   * 実適用 (dry_run=False) → ReplaceBlockedError (呼び出し側が 422)
    #   * プレビュー (dry_run=True) → その日を対象から外し、カイポケ行を
    #     ``skipped`` に積んで可視化する (プレビューは例外にしない)
    # 対象は ``source='manual_cancel'`` = らく助側の取消だけ。**取込 delete 由来**
    # の cancelled は元々カイポケに無い予定なので置換して構わない。
    blocked_days: set[date] = {
        v.visit_date
        for v in wipe_rows
        if v.status == VISIT_STATUS_CANCELLED and v.source == VISIT_SOURCE_MANUAL_CANCEL
    }
    if blocked_days:
        _days = "/".join(d.isoformat() for d in sorted(blocked_days))
        _msg = (
            "らく助側で今週だけ取消済みの訪問があります。"
            "⇧送信でカイポケへ反映してから置換してください"
            f"（対象日: {_days}）"
        )
        if not dry_run:
            raise ReplaceBlockedError(_msg)
        wipe_rows = [v for v in wipe_rows if v.visit_date not in blocked_days]
        result.wiped = len(wipe_rows)

    # --- 日ごとの内訳 (レポート明細 kind='day') -------------------------------
    # 置換の対象になった日を 0 件で先に並べ、白紙化した訪問を日別に数える
    # (挿入は Phase 3 で加算)。blocked_days は触らないので含めない。
    _scope_days = (
        sorted(target_days)
        if target_days is not None
        else [week_start + timedelta(days=i) for i in range(6)]
    )
    for _d in _scope_days:
        if _d not in blocked_days:
            result.per_day.setdefault(_d, {"wiped": 0, "inserted": 0})
    for _v in wipe_rows:
        result.per_day.setdefault(_v.visit_date, {"wiped": 0, "inserted": 0})["wiped"] += 1

    # --- 実績ガード ----------------------------------------------------------
    achievements = await _count_week_achievements(db, [v.id for v in wipe_rows])
    if achievements > 0:
        raise ReplaceBlockedError(
            f"この週には打刻などの実績が {achievements} 件あります。"
            "実績の紐付けを守るため置換はできません（差分取り込みを使ってください）"
        )

    # --- 名寄せ・コース索引 --------------------------------------------------
    patients = (await db.scalars(select(Patient).where(Patient.deleted_at.is_(None)))).all()
    pindex = build_name_index({str(p.id): p.name for p in patients})
    patient_by_id = {str(p.id): p for p in patients}

    staff_index_raw, staff_map = await load_staff_name_index(db)
    trainee_ids = {
        s.id for s in staff_map.values() if getattr(s, "is_trainee", False) and s.status == "active"
    }

    course_idx = await load_week_course_index(db, week_start)
    # コース単位の同行リンク (staff2 判定①: コースの同行スタッフは secondary にしない)。
    # kind を見ない / 集合で返す等の不変条件は共用ヘルパー側の docstring が正典
    # (以前はここに同等クエリを手書きしており、条件の説明が 2 箇所へ分散していた)。
    #
    # visit 単位のリンクを引かないのは置換の性質: 対象週の real 訪問は直前に白紙化
    # (soft-delete) され、visit は全て新規 INSERT される = 突合できる直リンクが存在しない。
    # 生き残るのはコースリンクだけ (diff-inbound の add 経路は復活枠があるため直リンクも
    # 見る — inbound.py 側の判定① 参照)。
    #
    # 臨時コースの掃除 (下) より**前**に引く: 掃除は当週の臨系コースへ deleted_at を
    # 立てるため、後に回すとヘルパーの live JOIN から漏れる。
    accompaniment_by_course = await resolve_accompaniment_staff_by_course(
        db, list(course_idx.by_id.keys())
    )

    # --- 臨時コースの掃除 (2026-07-26) ---------------------------------------
    # 前回の置換が作った臨系コースは、訪問の白紙化で空になっても行が残る。
    # 空の「臨」が旧担当のまま残ると、テンプレ単位の表示 (臨・臨2…を1枠に束ねる)
    # で他スタッフの臨N訪問がその旧担当へ誤帰属する (PO報告 2026-07-26)。
    # 置換のたびに当週の臨系コースを削除し、必要な分だけ作り直す。
    target_weekdays: set[int] | None = (
        {d.weekday() for d in target_days} if target_days is not None else None
    )
    temp_rows = [
        c
        for c in course_idx.by_id.values()
        if str(c.code).startswith("臨")
        and (target_weekdays is None or c.weekday in target_weekdays)
    ]
    for c in temp_rows:
        course_idx.by_id.pop(c.id, None)
        course_idx.codes_in_use.get((c.weekday, c.office_id), set()).discard(str(c.code))
        for k, v in list(course_idx.by_staff.items()):
            if v.id == c.id:
                course_idx.by_staff.pop(k, None)
        if not dry_run:
            c.deleted_at = now

    # --- 白紙化 (real のみ) --------------------------------------------------
    if not dry_run:
        for v in wipe_rows:
            v.deleted_at = now

    # --- Phase 1: 行の解決 (名寄せ・重複・日曜除外) ---------------------------
    seen_keys: set[tuple[str, date, str]] = set()

    def _skip(reason: str, e: ScheduleEntry, d: date | None) -> None:
        result.skipped.append(
            ReplaceSkip(
                reason=reason,
                user_name=e.user_name,
                staff_name=e.staff1_name,
                date=d.isoformat() if d else str(e.date),
                start=e.start_time,
            )
        )

    resolved: list[_ResolvedRow] = []
    for e in entries:
        try:
            day_num = int(str(e.date).strip())
        except (TypeError, ValueError):
            continue  # 日付列が数値でない行 (ヘッダ残骸等) は対象外
        d = day_to_date(day_num, week_start, week_sun)
        if d is None:
            continue  # 週レンジ外 (月まるごとCSVの他週分)
        if d.weekday() == 6:
            result.sunday_skipped += 1
            continue
        if target_days is not None and d not in target_days:
            continue  # 差分モード担当日 (smart-inbound) — 置換では触らない
        if d in blocked_days:
            # 「今週だけ取消」がある日 (プレビューのみ到達・実適用は上で 422)。
            _skip(
                "らく助側で今週だけ取消済みの訪問があります。"
                "⇧送信でカイポケへ反映してから置換してください",
                e,
                d,
            )
            continue

        start_t = parse_hhmm(e.start_time)
        end_t = parse_hhmm(e.end_time)
        if start_t is None or end_t is None:
            _skip("時刻が解釈できません", e, d)
            continue

        pid_str = match_name(e.user_name, pindex)
        if pid_str is None:
            _skip("患者を名寄せできません（らく助未登録の可能性）", e, d)
            continue
        patient = patient_by_id[pid_str]

        sid_str = match_name(e.staff1_name, staff_index_raw) if e.staff1_name else None
        if sid_str is None:
            _skip("担当1を名寄せできません（らく助未登録の可能性）", e, d)
            continue
        sid = uuid.UUID(sid_str)
        if sid in trainee_ids:
            # 方針転換 (PO確定 2026-07-26): カイポケは最終的な「正」のため、担当1が
            # 新人でも現実として取り込む。⚠警告 (trainee_solo) で可視化して
            # 新人フラグ見直しの判断材料にする。らく助発の割当経路の封鎖は不変。
            result.trainee_solo[e.staff1_name] = result.trainee_solo.get(e.staff1_name, 0) + 1

        key = (pid_str, d, e.start_time)
        if key in seen_keys:
            _skip("カイポケ側の重複行（同一患者・同時刻）", e, d)
            continue
        seen_keys.add(key)

        # staff2 の名寄せ (3段階判定はコース確定後の Phase 3 で行う)
        sid2: uuid.UUID | None = None
        if e.staff2_name:
            sid2_str = match_name(e.staff2_name, staff_index_raw)
            if sid2_str is not None:
                sid2 = uuid.UUID(sid2_str)

        office_id = patient.primary_office_id
        if office_id is None:
            _skip("患者の主担当拠点が未設定です", e, d)
            continue

        resolved.append(
            _ResolvedRow(
                entry=e,
                patient=patient,
                d=d,
                start_t=start_t,
                end_t=end_t,
                sid=sid,
                sid2=sid2,
                office_id=office_id,
            )
        )

    # --- Phase 2: コース計画 (2026-07-26 改修・臨時コース乱立の根治) -----------
    # カイポケは「正」なので、コースの担当もカイポケの現実に合わせる:
    #   1. スタッフが既にその日のコースを持っている → そのまま使う
    #   2. 持っていない → その日の空き通常コース (白紙化で全コース空) の担当を
    #      そのスタッフへ付け替えて使う (= コース丸ごと交代と同じ考え方)
    #   3. コースが足りない場合だけ臨時コースへ
    # これをしないと、らく助の古い割当とズレたスタッフ全員が臨時コース行きになり、
    # 表示 (テンプレート単位の枠) で臨・臨2・臨3… が1枠に合算されて
    # 「1枠15件」のような見え方になる (2026-07-26 PO報告の原因)。
    cell_staff: dict[tuple[int, uuid.UUID], set[uuid.UUID]] = {}
    for r in resolved:
        cell_staff.setdefault((r.d.weekday(), r.office_id), set()).add(r.sid)

    regular_by_cell: dict[tuple[int, uuid.UUID], list[Course]] = {}
    for c in course_idx.by_id.values():
        code = str(c.code)
        # M系 (マネージャー予備枠) と 臨系は付け替え対象にしない
        if code.startswith("臨") or code.upper().startswith("M"):
            continue
        regular_by_cell.setdefault((c.weekday, c.office_id), []).append(c)
    for lst in regular_by_cell.values():
        lst.sort(key=lambda c: str(c.code))

    # 未使用の通常テンプレート (例: 稲毛E)。既存コース行が足りないとき、臨時の前に
    # ここからコース行を新設する (2026-07-26・臨+臨2 合算表示の構造的削減)。
    regular_templates_by_office: dict[uuid.UUID, list[CourseTemplate]] = {}
    tpl_rows = (
        await db.scalars(select(CourseTemplate).where(CourseTemplate.deleted_at.is_(None)))
    ).all()
    for t in tpl_rows:
        label = (t.label or "").strip()
        if not label or label.startswith("臨") or label.upper().startswith("M"):
            continue
        regular_templates_by_office.setdefault(t.office_id, []).append(t)
    for tlst in regular_templates_by_office.values():
        tlst.sort(key=lambda t: t.label or "")

    course_plan: dict[tuple[int, uuid.UUID, uuid.UUID], Course] = {}
    reassignments: list[tuple[Course, uuid.UUID]] = []
    temp_keys: set[tuple[int, uuid.UUID, uuid.UUID]] = set()
    for cell, staff_ids in cell_staff.items():
        wd, office_id = cell
        regs = regular_by_cell.get(cell, [])
        used_course_ids: set[uuid.UUID] = set()
        # 1. 現担当がそのままの人 (同一スタッフ複数コースはコード順先頭を採用)
        for c in regs:
            if (
                c.assigned_staff_id is not None
                and c.assigned_staff_id in staff_ids
                and (wd, office_id, c.assigned_staff_id) not in course_plan
            ):
                course_plan[(wd, office_id, c.assigned_staff_id)] = c
                used_course_ids.add(c.id)
        # 2. 残りのスタッフへ空きコースをコード順で付け替え (名前順で決定的に)
        free = [c for c in regs if c.id not in used_course_ids]
        pending = sorted(
            (sid for sid in staff_ids if (wd, office_id, sid) not in course_plan),
            key=lambda s: staff_map[s].name if s in staff_map else "",
        )
        for sid, c in zip(pending, free, strict=False):
            course_plan[(wd, office_id, sid)] = c
            if c.assigned_staff_id != sid:
                reassignments.append((c, sid))
        # 3. まだあぶれる場合は、未使用の通常テンプレートからコース行を新設
        leftover = pending[len(free) :]
        if leftover:
            used_codes = {str(c.code) for c in regs}
            avail_tpls = [
                t
                for t in regular_templates_by_office.get(office_id, [])
                if (t.label or "").strip() not in used_codes
            ]
            for sid, t in zip(leftover, avail_tpls, strict=False):
                result.courses_created += 1
                if not dry_run:
                    new_course = Course(
                        iso_year=course_idx.iso_year,
                        iso_week=course_idx.iso_week,
                        weekday=wd,
                        code=(t.label or "").strip(),
                        course_status=COURSE_STATUS_STAFF_ASSIGNED,
                        assigned_staff_id=sid,
                        staff_assigned_at=now,
                        template_id=t.id,
                        office_id=office_id,
                        note="カイポケ置換取込によるコース新設",
                    )
                    db.add(new_course)
                    await db.flush()
                    course_idx.register(new_course)
                    course_plan[(wd, office_id, sid)] = new_course
            # 4. テンプレートも尽きた分だけ臨時コースへ
            for sid in leftover[len(avail_tpls) :]:
                temp_keys.add((wd, office_id, sid))

    result.courses_reassigned = len(reassignments)
    result.temp_courses = len(temp_keys)

    if not dry_run:
        for c, sid in reassignments:
            c.assigned_staff_id = sid

    # --- Phase 3: 挿入 -------------------------------------------------------
    # ⛔ NG スタッフ警告 (Phase 3・警告のみ): 取込後に「担当スタッフ × その患者」が
    # NG ペアになる組を dry-run のときだけ集める (実適用の挙動は変えない)。
    ng_pairs: list[NgPair] = []
    for r in resolved:
        e = r.entry
        d = r.d
        sid = r.sid
        weekday = d.weekday()
        course: Course | None = course_plan.get((weekday, r.office_id, sid))

        sid2 = r.sid2
        accompaniment_sid2: uuid.UUID | None = None
        if sid2 is not None:
            # staff2 の 3 段階判定 (diff-inbound の add と同じ規則・設計 §9 / 一般化 §3-5)
            # ① コースの同行リンクと一致 (kind 不問) → secondary にしない・要2名化しない
            # ② リンク無しの新人 → 同行リンクとして取り込む (一般スタッフへは開放しない)
            # ③ リンク無しの一般スタッフ → 従来どおり secondary + required_staff_count=2
            if course is not None and sid2 in accompaniment_by_course.get(course.id, set()):
                sid2 = None  # コースの同行スタッフ → secondary にしない
            elif sid2 in trainee_ids:
                accompaniment_sid2 = sid2
                sid2 = None  # 新人 → 同行リンクとして取り込む

        if dry_run:
            course_code = str(course.code) if course is not None else None
            for staff_id in (sid, sid2):
                if staff_id is not None:
                    ng_pairs.append((r.patient.id, staff_id, d, course_code))
            result.inserted += 1
            result.per_day.setdefault(d, {"wiped": 0, "inserted": 0})["inserted"] += 1
            continue

        if course is None:
            course = await ensure_temp_course(
                db, course_idx, weekday=weekday, office_id=r.office_id, staff_id=sid, now=now
            )
            if course is None:
                _skip("臨時コース枠（臨〜臨9）が満杯です", e, d)
                continue
            # 以後の同一スタッフ・同日の行はこのコースへ相乗り
            course_plan[(weekday, r.office_id, sid)] = course

        patient = r.patient
        start_t = r.start_t
        end_t = r.end_t
        new_visit = Visit(
            patient_id=patient.id,
            visit_date=d,
            start_time=start_t,
            end_time=end_t,
            type="regular",
            status="planned",
            source="import",  # 取込由来・週再生成から保護
            required_staff_count=2 if sid2 is not None else 1,
            primary_staff_id=sid,
            secondary_staff_id=sid2,
            course_id=course.id,
        )
        _stamp_note(new_visit, "カイポケ置換取込", today)
        try:
            async with db.begin_nested():
                db.add(new_visit)
                await db.flush()
                await _replace_assignments(db, new_visit, [s for s in (sid, sid2) if s is not None])
                if accompaniment_sid2 is not None:
                    db.add(
                        Accompaniment(
                            accompanying_staff_id=accompaniment_sid2,
                            target_type="visit",
                            visit_id=new_visit.id,
                            source="manual",
                            kind=resolve_accompaniment_kind(staff_map[accompaniment_sid2]),
                            created_by=None,
                        )
                    )
        except IntegrityError:
            _skip("UNIQUE衝突（想定外・要確認）", e, d)
            continue
        result.inserted += 1
        result.per_day.setdefault(d, {"wiped": 0, "inserted": 0})["inserted"] += 1

    if dry_run:
        result.ng_conflicts = await collect_ng_conflicts(db, ng_pairs)

    return result
