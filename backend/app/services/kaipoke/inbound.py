"""カイポケ → CareFlow 逆反映 (R-1/R-2・docs/plans/kaipoke-reverse-sync-design.md).

「週のバトンリレー」: 週apply でその週の正はカイポケに移る。提供中の週に
カイポケ側で入った直し込み (キャンセル・時刻変更) を CareFlow visits へ
書き写して追いかけるのが本モジュール。

安全設計:
  * apply実績ゲート — CareFlow から実apply (dry_run=false) した記録のある週だけ
    取り込み可 (``real_apply_record``)。正がカイポケに移っていない週は取り込まない。
  * その週限りの原則 — visits のみ書き、固定パターン (patient_fixed_visits) には
    一切触れない。edit/date_change は ``source='manual_week'`` を刻み週再生成から保護。
  * キャンセルは ``status='cancelled'`` (soft-delete しない・履歴が残る)。
  * 2名体制の防御 — 対象 visit が visit_group_id を持つ場合はグループ全行に同じ
    操作を適用する (片割れだけ残さない)。※本番データは現状グループ0件。
  * 「今週だけ取消」(週空間 Phase E / week-cockpit-design.md D1) の尊重 —
    らく助側の取消は ``status='cancelled'`` + ``source='manual_cancel'`` で刻まれる。
    add がその枠に当たったら復活させず failed にする (⇧送信でカイポケへ delete を
    反映させてから取込)。**取込 delete 由来**の cancelled (source は元のまま) は
    従来どおり復活させる — 同一実行内の delete+add ペア (名寄せ差) の収束に要る。
  * 置換モード (``replace_inbound.replace_week_from_kaipoke``) も同じ考え方で
    **日単位**に止める: 白紙化対象日に ``manual_cancel`` の visit が居れば、
    実適用は ReplaceBlockedError (呼び出し側で 422)、プレビューはその日を対象から
    外して ``skipped`` に理由付きで積む。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.models.accompaniment import Accompaniment
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.course_template import CourseTemplate
from app.models.kaipoke_job import KaipokeJob
from app.models.patient import Patient
from app.models.staff import Staff
from app.models.visit import (
    VISIT_SOURCE_MANUAL_CANCEL,
    VISIT_STATUS_CANCELLED,
    Visit,
)
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.accompaniment import (
    resolve_accompaniment_kind,
    resolve_accompaniment_staff_by_course,
    resolve_accompaniment_staff_by_visit,
    resolve_direct_accompaniment_staff_by_visit,
)
from app.services.kaipoke.name_match import build_name_index, match_name
from app.services.kaipoke.ng_conflicts import NgConflict, NgPair, collect_ng_conflicts

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.correction_sheet import CorrectionSheetItem

# 取り込みの実績刻印 (visit.note に追記する行の接頭辞)。人間向け日本語のため
# モバイルの内部note非表示 (lib/visit-note.ts) の対象外 = 現場にも表示される。
NOTE_STAMP_PREFIX = "カイポケ取込"

# ゲート判定の「今日」は JST 基準 (サーバは UTC・週境界の判定がずれないように)。
JST = ZoneInfo("Asia/Tokyo")

# R-3 臨時コース (設計 §8-1・PO合意): 新担当がその日コースを持たない場合に
# 「その日だけ1人で対応する回り」を新設する。コード=臨/臨2..臨9 (migration 0057)、
# テンプレート=各拠点の「臨時」(無ければ作る)。コースは週×曜日インスタンスなので
# 他の週には残らない。
TEMP_COURSE_CODES: tuple[str, ...] = ("臨", "臨2", "臨3", "臨4", "臨5", "臨6", "臨7", "臨8", "臨9")
TEMP_TEMPLATE_LABEL = "臨時"


async def real_apply_record(db: AsyncSession, week_start: date) -> KaipokeJob | None:
    """対象週に「実apply (dry_run=false)」の記録があれば最新の job を返す。

    週apply がバトンタッチの瞬間 = この記録がある週だけ正がカイポケに移っている。
    failed も対象に含める: 実書込が部分的に走った可能性があり、その週は既に
    混在状態のため、取り込みでカイポケ現況へ揃える方が安全に働く。
    week_start 列 (索引付き Date) で対象週へ絞ったうえで、params の突合だけ
    Python 側で行う (JSONB 演算子は SQLite テスト環境で使えないため)。
    """
    rows = await db.scalars(
        select(KaipokeJob)
        .where(
            KaipokeJob.job_type == "push",
            KaipokeJob.status.in_(("completed", "failed")),
            # trigger_apply は job.week_start = sheet.week_start を刻む (0056以降)。
            # 旧形式 (月初日) の apply は params にも week_start が無く元々対象外。
            KaipokeJob.week_start == week_start,
        )
        .order_by(KaipokeJob.created_at.desc())
    )
    iso = week_start.isoformat()
    for job in rows.all():
        p = job.params or {}
        if p.get("op") == "apply" and p.get("dry_run") is False and p.get("week_start") == iso:
            return job
    return None


async def inbound_week_eligible(
    db: AsyncSession, week_start: date, *, today: date | None = None
) -> tuple[bool, KaipokeJob | None]:
    """取り込みゲート (2026-08-09 改訂・PO確定) — (eligible, 実apply job) を返す。

    **全週で取り込み可 (常に eligible=True)**。客先運用は「カイポケで先に計画を
    入れ、らく助へ映す」— 未来週でもカイポケが正になり得るため、旧・時間ゲート
    (週開始<=今日 or 実apply記録) は撤廃した (2026-07-26 改訂の置き換え)。

    事故防止は時間ではなく**内容ベースの安全装置**へ移譲:
      * 空CSV拒否 — カイポケが空の週での白紙化 (週全滅) はゲートと無関係に拒否
      * dry-run 既定 + 確認ダイアログ (挿入n/削除n を見てから実行)
      * FE 大量キャンセル警告 (候補>=10で赤警告) + 未来週専用の警告表示
      * 実績(打刻)ガード — 打刻のある週は置換不可

    戻り値の tuple 形と実apply record は表示用 (last_applied_at) と将来の
    再制限に備えて維持する。today 引数も互換のため残す (判定には不使用)。
    """
    del today  # 2026-08-09 撤廃: 時間判定はしない (シグネチャ互換のため受け取るだけ)
    record = await real_apply_record(db, week_start)
    return True, record


def day_to_date(day: int, week_start: date, week_end: date) -> date | None:
    """CSV の「日」(1-31) を週レンジ内の実日付へ解決する (月境界の折返しも安全)。"""
    cur = week_start
    while cur <= week_end:
        if cur.day == day:
            return cur
        cur = date.fromordinal(cur.toordinal() + 1)
    return None


def parse_hhmm(value: str | None) -> time | None:
    if not value:
        return None
    try:
        h, m = value.split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        return None


async def load_week_visit_index(
    db: AsyncSession, week_start: date, week_end: date
) -> dict[tuple[uuid.UUID, date, time], Visit]:
    """対象週の active visits を (patient_id, date, start_time) で索引化する。

    (patient, date, start) は部分UNIQUE (migration 0026) のため一意に引ける。
    """
    rows = await db.scalars(
        select(Visit).where(
            Visit.deleted_at.is_(None),
            Visit.visit_date >= week_start,
            Visit.visit_date <= week_end,
        )
    )
    return {(v.patient_id, v.visit_date, v.start_time): v for v in rows.all()}


async def _group_partners(db: AsyncSession, visit: Visit) -> list[Visit]:
    """同一 visit_group の全行 (自分含む)。グループ無しなら自分のみ。"""
    if visit.visit_group_id is None:
        return [visit]
    rows = await db.scalars(
        select(Visit).where(
            Visit.visit_group_id == visit.visit_group_id,
            Visit.deleted_at.is_(None),
        )
    )
    return list(rows.all())


def _stamp_note(visit: Visit, message: str, today: date) -> None:
    line = f"{NOTE_STAMP_PREFIX} {today.month}/{today.day}: {message}"
    visit.note = f"{visit.note}\n{line}" if visit.note else line


# --- R-3: スタッフ名寄せ・コース解決 ----------------------------------------


async def load_staff_name_index(
    db: AsyncSession,
) -> tuple[dict[str, list[str]], dict[uuid.UUID, Staff]]:
    """スタッフ名 → staff.id の名寄せ索引 (患者と同じ match_name 機構を流用)。

    削除済みは除外。同姓同名や退職者との正規化キー衝突は match_name が
    None (=要確認) に落とすため取り違えは起きない。
    """
    rows = await db.scalars(select(Staff).where(Staff.deleted_at.is_(None)))
    staff_map = {s.id: s for s in rows.all()}
    return build_name_index({str(s.id): s.name for s in staff_map.values()}), staff_map


@dataclass
class WeekCourseIndex:
    """対象週のコース索引 (スタッフ変更/追加のコース解決に使う)。"""

    iso_year: int
    iso_week: int
    by_id: dict[uuid.UUID, Course] = field(default_factory=dict)
    # (weekday, office_id, staff_id) → その日その拠点でそのスタッフが担当するコース
    # (複数あればコード昇順の先頭 = 通常コース優先)。
    by_staff: dict[tuple[int, uuid.UUID, uuid.UUID], Course] = field(default_factory=dict)
    codes_in_use: dict[tuple[int, uuid.UUID], set[str]] = field(default_factory=dict)

    def register(self, course: Course) -> None:
        self.by_id[course.id] = course
        key = (course.weekday, course.office_id)
        self.codes_in_use.setdefault(key, set()).add(course.code)
        if course.assigned_staff_id is not None:
            skey = (course.weekday, course.office_id, course.assigned_staff_id)
            cur = self.by_staff.get(skey)
            if cur is None or course.code < cur.code:
                self.by_staff[skey] = course


async def load_week_course_index(db: AsyncSession, week_start: date) -> WeekCourseIndex:
    iso = week_start.isocalendar()
    idx = WeekCourseIndex(iso_year=iso[0], iso_week=iso[1])
    rows = await db.scalars(
        select(Course).where(
            Course.iso_year == iso[0],
            Course.iso_week == iso[1],
            Course.deleted_at.is_(None),
            Course.course_status != "proposed",
        )
    )
    for c in rows.all():
        idx.register(c)
    return idx


async def _ensure_temp_template(db: AsyncSession, office_id: uuid.UUID) -> CourseTemplate:
    tpl = await db.scalar(
        select(CourseTemplate).where(
            CourseTemplate.office_id == office_id,
            CourseTemplate.label == TEMP_TEMPLATE_LABEL,
            CourseTemplate.deleted_at.is_(None),
        )
    )
    if tpl is not None:
        return tpl
    tpl = CourseTemplate(
        office_id=office_id,
        label=TEMP_TEMPLATE_LABEL,
        notes="カイポケ取り込み由来の臨時コース用テンプレート (自動作成)",
    )
    db.add(tpl)
    await db.flush()
    return tpl


async def ensure_temp_course(
    db: AsyncSession,
    idx: WeekCourseIndex,
    *,
    weekday: int,
    office_id: uuid.UUID,
    staff_id: uuid.UUID,
    now: datetime,
) -> Course | None:
    """臨時コースを取得または新設する (同一スタッフ・同一日の2件目は相乗り)。

    臨/臨2..臨9 が全て使用済みなら None (呼び出し側で failed 扱い)。
    """
    # 既に同スタッフの臨時コースがあれば相乗り (コース乱立防止)。
    for c in idx.by_id.values():
        if (
            c.weekday == weekday
            and c.office_id == office_id
            and c.assigned_staff_id == staff_id
            and c.code in TEMP_COURSE_CODES
        ):
            return c
    used = idx.codes_in_use.get((weekday, office_id), set())
    free = next((code for code in TEMP_COURSE_CODES if code not in used), None)
    if free is None:
        return None
    tpl = await _ensure_temp_template(db, office_id)
    course = Course(
        iso_year=idx.iso_year,
        iso_week=idx.iso_week,
        weekday=weekday,
        code=free,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff_id,
        staff_assigned_at=now,
        template_id=tpl.id,
        office_id=office_id,
        note="カイポケ取込による臨時コース",
    )
    db.add(course)
    await db.flush()
    idx.register(course)
    return course


async def _replace_assignments(db: AsyncSession, visit: Visit, staff_ids: list[uuid.UUID]) -> None:
    """visit_staff_assignments を新しい担当セットへ置き換える (v1/v2整合)。

    staff1=staff2 のような重複入力でも (visit_id, staff_id) 複合PKに
    衝突しないよう順序保持で重複除去する。
    """
    await db.execute(delete(VisitStaffAssignment).where(VisitStaffAssignment.visit_id == visit.id))
    for sid in dict.fromkeys(staff_ids):
        db.add(VisitStaffAssignment(visit_id=visit.id, staff_id=sid))


@dataclass
class InboundItemResult:
    item_id: str
    action: str
    outcome: str  # cancelled / updated / added / skipped / failed
    detail: str = ""
    patient_name: str = ""
    date: str = ""


@dataclass
class InboundApplySummary:
    cancelled: int = 0
    updated: int = 0
    added: int = 0
    skipped: int = 0
    failed: int = 0
    results: list[InboundItemResult] = field(default_factory=list)
    # ⛔ NG スタッフ (patient_ng_staff) に該当する組み合わせ (dry-run のみ集計)。
    # カイポケが「正」なので取り込みはブロックしない — 警告として可視化するだけ。
    # ``as_dict`` には **載せない**: 実適用の job.result_summary を変えないため。
    ng_conflicts: list[NgConflict] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cancelled": self.cancelled,
            "updated": self.updated,
            "added": self.added,
            "skipped": self.skipped,
            "failed": self.failed,
            "results": [r.__dict__ for r in self.results],
        }


async def apply_inbound_items(
    db: AsyncSession,
    *,
    items: list[CorrectionSheetItem],
    week_start: date,
    week_end: date,
    days: list[date] | None,
    dry_run: bool,
    now: datetime,
) -> InboundApplySummary:
    """inbound CorrectionSheetItem を CareFlow visits へ適用する (同期・ローカル)。

    days 指定時はその日付の item だけを対象にする (曜日チップの複数選択)。
    dry_run=True では一切 mutate せず、予定される結果だけを返す。

    対応 (R-2/R-3):
      * delete → キャンセル (status='cancelled'・グループ丸ごと)
      * edit / date_change → 時刻・日付の変更 (source='manual_week')
      * スタッフ変更 → **コースの変更**として扱う (設計 §8-1・PO合意):
        コース丸ごと交代 → Course.assigned_staff_id 変更 /
        単発 → 新担当のその日のコースへ移動 / コース無し → 臨時コース新設
      * add → visit INSERT (source='import'・コースは担当のコース or 臨時)
    """
    summary = InboundApplySummary()
    index = await load_week_visit_index(db, week_start, week_end)
    staff_index, staff_map = await load_staff_name_index(db)
    course_idx = await load_week_course_index(db, week_start)
    today = now.date()
    day_set = set(days) if days else None

    # 同行由来 staff2 のラウンドトリップ汚染防止 (設計 §9 / 一般化 §3-5): CareFlow が
    # 職員名2 としてカイポケへ送った同行スタッフが逆取込で staff2 として返ってきたとき、
    # それを「要2名患者」として secondary_staff_id へ書き戻す / required_staff_count=2 へ
    # 昇格させると汚染が起きる。既存の同行リンクと一致する staff2 は同行由来とみなし
    # 反映しない (同行は accompaniments が唯一の正典)。
    #
    # **判定① は kind を問わない** (一般化 §3-5): 新人同行 (kind='trainee') も一般スタッフの
    # 同行 (kind='support') も CSV では等しく職員名2 に載る = 同じ経路で返ってくるため、
    # 「新人かどうか」ではなく「**そのリンクが実在するかどうか**」で判定するのが正しい。
    # 下 3 本のヘルパーは kind でフィルタせず全同行リンクを返すので、集合の汎化はここで
    # 完了している (新人限定なのは後述の判定② だけ)。staff2 判定を持つのは edit と add の
    # 2 経路だけ (delete は訪問をキャンセルするだけで staff2 を読まない)。
    # - edit 経路: 実在 visit を visit_id で突合 (直リンク ∪ コースリンク・N+1 回避)。
    #   visit のコースは変わらないので、現在の course_id のリンクを混ぜてよい。
    # - add 経路: 新設 visit が張り付く**新しい**コースの course-level リンクで突合。
    #   復活枠 (revive) はそこへ **visit 直リンクのみ**を足す — 復活は course_id を
    #   付け替えるため、旧コースのリンクを混ぜると誤マッチする (レビュー MEDIUM-2)。
    # いずれも「同行スタッフ集合への membership 判定」(複数同行リンク時の last-wins
    # 非決定性で誤って secondary へ書き戻す汚染を防ぐ — Phase 3 レビュー MINOR-2)。
    accompaniment_by_visit = await resolve_accompaniment_staff_by_visit(db, list(index.values()))
    accompaniment_direct_by_visit = await resolve_direct_accompaniment_staff_by_visit(
        db, list(index.values())
    )
    accompaniment_by_course = await resolve_accompaniment_staff_by_course(
        db, list(course_idx.by_id.keys())
    )

    def _remember_accompaniment(visit_id: uuid.UUID, staff_id: uuid.UUID) -> None:
        """自動作成した同行リンクを突合集合へ反映する (同一実行内の二重 INSERT 予防).

        1 回の apply に同じ訪問の item が 2 つ入っていると (名寄せ差の delete+add 等)、
        2 つ目が判定② をもう一度通って同じリンクを INSERT しようとし UNIQUE
        (staff, visit_id) で落ちる。作成直後に集合へ足しておけば 2 つ目は判定① へ
        落ちて何もしない。edit / add / 復活枠の 3 経路すべてから呼ぶ。
        """
        accompaniment_by_visit.setdefault(visit_id, set()).add(staff_id)
        accompaniment_direct_by_visit.setdefault(visit_id, set()).add(staff_id)

    # 案B (設計 §9 拡張・PO承認 2026-07-12): カイポケ側 staff2 がリンク未作成でも新人
    # (is_trainee) なら「要2名患者」化せず同行リンクを自動作成する。判定用に active・
    # 未削除の新人 staff id を1クエリでロード (N+1 回避)。match_name で解決した staff.id
    # がこの集合に含まれるかで判定 2 (自動同行化) と判定 3 (実スタッフの要2名化) を分岐。
    #
    # **一般化しない** (一般化 §3-5 で意図的に見送り): 判定② を一般スタッフへ開放すると、
    # 本当に 2 名体制で入った訪問まで「同行」に化けて要2名化が消える。リンクの無い
    # 一般スタッフの staff2 は従来どおり判定③ (2名体制) へ落とす。
    # (staff1 が新人のときの ⚠警告注記も同じ集合を使う。)
    trainee_ids: set[uuid.UUID] = set(
        (
            await db.scalars(
                select(Staff.id).where(
                    Staff.is_trainee.is_(True),
                    Staff.status == "active",
                    Staff.deleted_at.is_(None),
                )
            )
        ).all()
    )

    # 患者 → 拠点 (コース解決に使う)。
    pids = {it.patient_id for it in items if it.patient_id is not None}
    patient_office: dict[uuid.UUID, uuid.UUID] = {}
    if pids:
        prows = await db.scalars(select(Patient).where(Patient.id.in_(pids)))
        patient_office = {
            p.id: p.primary_office_id for p in prows.all() if p.primary_office_id is not None
        }

    async def _resolve_visit(item: CorrectionSheetItem, target_date: date) -> Visit | None:
        b = item.before or {}
        st = parse_hhmm(str(b.get("start_time") or ""))
        visit: Visit | None = None
        if item.visit_id is not None:
            visit = await db.get(Visit, item.visit_id)
            if visit is not None and visit.deleted_at is not None:
                visit = None
        if visit is None and st is not None and item.patient_id is not None:
            visit = index.get((item.patient_id, target_date, st))
        return visit

    # --- コース丸ごと交代の検出 (事前パス・設計 §8-1 パターンA) --------------
    # 同じコース×日の planned 全訪問が「同じ新担当への変更」を示していれば、
    # 訪問を動かさずコースの担当を替える (現実 = その日の回りを丸ごと交代)。
    planned_by_course: dict[tuple[uuid.UUID, date], set[uuid.UUID]] = {}
    for v in index.values():
        if v.status == "planned" and v.course_id is not None:
            planned_by_course.setdefault((v.course_id, v.visit_date), set()).add(v.id)

    takeover_votes: dict[tuple[uuid.UUID, date], dict[uuid.UUID, set[uuid.UUID]]] = {}
    for it in items:
        if it.action != "edit" or it.patient_id is None:
            continue
        b, a = it.before or {}, it.after or {}
        if (b.get("staff1") or "") == (a.get("staff1") or "") or not (a.get("staff1") or ""):
            continue
        try:
            d = int(str(b.get("date") or a.get("date")))
        except (TypeError, ValueError):
            continue
        td = day_to_date(d, week_start, week_end)
        if td is None or (day_set is not None and td not in day_set):
            continue
        vv = await _resolve_visit(it, td)
        if vv is None or vv.course_id is None:
            continue
        sid_str = match_name(str(a.get("staff1")), staff_index)
        if not sid_str:
            continue
        takeover_votes.setdefault((vv.course_id, td), {}).setdefault(uuid.UUID(sid_str), set()).add(
            vv.id
        )

    takeover: dict[tuple[uuid.UUID, date], uuid.UUID] = {}
    for tkey, votes in takeover_votes.items():
        if len(votes) != 1:
            continue
        sid, vids = next(iter(votes.items()))
        if vids and vids == planned_by_course.get(tkey, set()):
            takeover[tkey] = sid

    # ⛔ NG スタッフ警告 (Phase 3・設計書 §6 末尾): 取込適用後に「担当スタッフ ×
    # その担当が回る患者」が NG ペア (patient_ng_staff) になる組を集める。
    # **警告のみ・取込はブロックしない** (カイポケが正)。照会は末尾で 1 回だけ行い、
    # 実適用 (dry_run=False) では照会そのものを走らせない (apply の挙動は不変)。
    ng_pairs: list[NgPair] = []
    patient_by_visit_id = {v.id: v.patient_id for v in index.values()}
    for (takeover_course_id, takeover_date), takeover_sid in takeover.items():
        takeover_course = course_idx.by_id.get(takeover_course_id)
        takeover_code = str(takeover_course.code) if takeover_course is not None else None
        for vid in planned_by_course.get((takeover_course_id, takeover_date), set()):
            pid = patient_by_visit_id.get(vid)
            if pid is not None:
                ng_pairs.append((pid, takeover_sid, takeover_date, takeover_code))

    # 適用順序: キャンセル/変更 → 追加 (設計 §8 共通原則)。delete で空いた同時刻の
    # 枠へ add が入る玉突きケースを確実に成立させるため明示ソートする (安定ソート
    # なので同アクション内の相対順序は保たれる)。2026-07-26 まではシート挿入順に
    # 依存しており、同時刻の delete+add ペアが順序次第で失敗していた。
    items = sorted(items, key=lambda it: it.action == "add")
    # この実行で「キャンセルされる予定」の visit id 集合。dry-run では status を
    # 書き換えないため、add 側の復活判定 (下記) の予測に使う (dry-run の結果が
    # 実適用と一致するように)。
    pending_cancelled: set[uuid.UUID] = set()

    for item in items:
        before = item.before or {}
        after = item.after or {}
        patient_name = str(before.get("user_name") or after.get("user_name") or "")

        def _finish(
            outcome: str,
            detail: str,
            target_date: date | None,
            *,
            _item=item,
            _pname=patient_name,
        ) -> None:
            summary.results.append(
                InboundItemResult(
                    item_id=str(_item.id),
                    action=_item.action,
                    outcome=outcome,
                    detail=detail,
                    patient_name=_pname,
                    date=target_date.isoformat() if target_date else "",
                )
            )
            if outcome == "cancelled":
                summary.cancelled += 1
            elif outcome == "updated":
                summary.updated += 1
            elif outcome == "added":
                summary.added += 1
            elif outcome == "skipped":
                summary.skipped += 1
            else:
                summary.failed += 1
            if not dry_run:
                _item.comment = f"{outcome}: {detail}" if detail else outcome

        # --- 対象日の解決 (before 側が CareFlow の現在地) --------------------
        raw_day = before.get("date") or after.get("date")
        try:
            day = int(str(raw_day))
        except (TypeError, ValueError):
            _finish("failed", f"日付が解釈できません: {raw_day!r}", None)
            continue
        target_date = day_to_date(day, week_start, week_end)
        if target_date is None:
            _finish("failed", f"日付 {day} が週レンジ外です", None)
            continue
        if day_set is not None and target_date not in day_set:
            continue  # 選択外の曜日 — 結果にも数えない (対象外)。

        # --- add: カイポケにのみ存在する予定 → visit INSERT (R-3b) -----------
        if item.action == "add":
            if item.patient_id is None:
                _finish("failed", "利用者名を CareFlow 患者に解決できませんでした", target_date)
                continue
            start_new = parse_hhmm(str(after.get("start_time") or ""))
            end_new = parse_hhmm(str(after.get("end_time") or ""))
            if start_new is None or end_new is None:
                _finish("failed", "追加予定の時刻が解釈できません", target_date)
                continue
            existing = index.get((item.patient_id, target_date, start_new))
            revive: Visit | None = None
            if existing is not None:
                # cancelled も UNIQUE 枠 (0026: deleted_at IS NULL) を占有するため
                # INSERT できない。代わりに **キャンセル済みの枠を復活** させて
                # 上書きする (2026-07-26): 同一実行内の delete+add ペア (名寄せ差 =
                # 氏名の空白違い等で同一訪問が分解されるケース) が安全に収束する。
                # dry-run では delete が status を書かないため pending_cancelled で予測。
                #
                # 例外 = **らく助側の「今週だけ取消」** (source='manual_cancel' /
                # 週空間 Phase E・week-cockpit-design.md D1)。まだ⇧送信していない
                # だけなので、取込で黙って復活させると利用者の意思に反して訪問が
                # 戻る。⇧送信で delete をカイポケへ反映してから取り込ませる。
                # 取込 delete 由来の cancelled は従来どおり復活してよい。
                if existing.source == VISIT_SOURCE_MANUAL_CANCEL:
                    _finish(
                        "failed",
                        "らく助側で今週だけ取消済みです。"
                        "⇧送信でカイポケへ反映してから取り込んでください",
                        target_date,
                    )
                    continue
                if existing.status == VISIT_STATUS_CANCELLED or existing.id in pending_cancelled:
                    revive = existing
                else:
                    _finish(
                        "failed",
                        "同時刻の予定が既にあります（差分を取り直してください）",
                        target_date,
                    )
                    continue
            staff1_name = str(after.get("staff1") or "")
            sid_str = match_name(staff1_name, staff_index) if staff1_name else None
            if not sid_str:
                _finish("failed", f"担当「{staff1_name}」を解決できませんでした", target_date)
                continue
            sid = uuid.UUID(sid_str)
            # 方針転換 (PO確定 2026-07-26): カイポケは最終的な「正」のため、担当1が
            # 新人 (is_trainee) でも現実として取り込む (拒否しない)。ただし
            # ⚠警告注記で可視化し、新人フラグ見直しの判断材料にする。
            # らく助発の割当経路 (Layer3/course PATCH/apply-staff-review) の封鎖は不変。
            trainee_note = (
                f"／⚠担当1「{staff1_name}」は新人です（単独訪問・新人フラグの見直しを検討）"
                if sid in trainee_ids
                else ""
            )
            staff2_name = str(after.get("staff2") or "")
            sid2: uuid.UUID | None = None
            accompaniment_sid2: uuid.UUID | None = None
            staff2_note = ""
            if staff2_name:
                sid2_str = match_name(staff2_name, staff_index)
                if sid2_str:
                    sid2 = uuid.UUID(sid2_str)
                else:
                    staff2_note = f"／担当2「{staff2_name}」未解決（1名で登録）"
            office_id = patient_office.get(item.patient_id)
            if office_id is None:
                _finish("failed", "患者の主担当拠点が特定できません", target_date)
                continue
            weekday = target_date.weekday()
            course = course_idx.by_staff.get((weekday, office_id, sid))
            # staff2 の 3 段階判定 (案B・設計 §9 拡張 / 一般化 §3-5):
            # ① 既存の同行リンクと一致 (**kind 不問** = 新人同行も一般スタッフの同行も) →
            #    secondary へ書かず要2名化もしない (ラウンドトリップ汚染防止)。
            #    突合先 = 訪問が**これから張り付く**コースのリンク ∪ (復活枠なら) その
            #    visit の **直リンクのみ**。
            #    直リンクを足す理由: 復活枠は visit を再利用するので、patient 単位で
            #    張った同行 (target_type='visit') が生き残っている。落とすと一般スタッフの
            #    同行が ③ へ流れて secondary へ書き戻される (一般化で露出した汚染経路)。
            #    直リンク**だけ**に絞る理由: 復活は course_id を新しいコースへ付け替える
            #    ため、その visit が今居る旧コースのリンクは復活後には効かない。混ぜると
            #    旧コースの同行者と同名の staff2 が ① へ誤マッチし、本物の 2 人目が
            #    secondary に書かれず消える (2026-08-17 レビュー MEDIUM-2)。
            # ② リンクは無いが新人 (is_trainee) → visit 新設後に同行リンクを自動作成し、
            #    secondary へは書かない・要2名化しない (盤面に2人目コース列の無い不整合を
            #    生む「要2名化」を避ける・PO指摘)。一般スタッフへは開放しない (上述)。
            # ③ 新人でない実スタッフ (リンク無し) → 従来どおり secondary +
            #    required_staff_count=2 (= 2名体制への昇格)。
            if sid2 is not None:
                linked: set[uuid.UUID] = set()
                if course is not None:
                    linked |= accompaniment_by_course.get(course.id, set())
                if revive is not None:
                    linked |= accompaniment_direct_by_visit.get(revive.id, set())
                if sid2 in linked:
                    sid2 = None
                    staff2_note = "／担当2は同行のため未反映（要2名化しない）"
                elif sid2 in trainee_ids:
                    accompaniment_sid2 = sid2
                    sid2 = None
                    staff2_note = f"／担当2「{staff2_name}」は新人のため同行として取り込みました"
            course_note = f"コース{course.code}" if course is not None else "臨時コース新設"
            # ⛔ NG: 追加される訪問の担当 (1/2) × 患者。
            for ng_sid in (sid, sid2):
                if ng_sid is not None:
                    ng_pairs.append(
                        (
                            item.patient_id,
                            ng_sid,
                            target_date,
                            str(course.code) if course is not None else None,
                        )
                    )
            revive_note = "・キャンセル済みの枠を復活" if revive is not None else ""
            detail = (
                f"{target_date.month}/{target_date.day} {after.get('start_time')} を追加"
                f"（担当 {staff1_name}・{course_note}{revive_note}）{staff2_note}{trainee_note}"
            )
            if not dry_run:
                if course is None:
                    course = await ensure_temp_course(
                        db,
                        course_idx,
                        weekday=weekday,
                        office_id=office_id,
                        staff_id=sid,
                        now=now,
                    )
                    if course is None:
                        _finish("failed", "臨時コース枠（臨〜臨9）が満杯です", target_date)
                        continue
                if revive is not None:
                    # キャンセル済み (または本実行でキャンセルされた) 同時刻の枠を
                    # 復活させ、カイポケの内容で上書きする (INSERT しない = UNIQUE 安全)。
                    # visit_group_id は保持する: ペア相手も同じ delete+add 復活を
                    # 通るためリンクは正しく残り、相手がキャンセルのままでも表示・
                    # 集計は cancelled 除外のため実害がない。
                    # savepoint で item 単位に隔離 (INSERT 経路と同じ方針・
                    # 想定外の IntegrityError でバッチを巻き添えにしない)。
                    try:
                        async with db.begin_nested():
                            revive.status = "planned"
                            revive.start_time = start_new
                            revive.end_time = end_new
                            revive.source = "import"
                            revive.required_staff_count = 2 if sid2 is not None else 1
                            revive.primary_staff_id = sid
                            revive.secondary_staff_id = sid2
                            revive.course_id = course.id
                            _stamp_note(revive, "カイポケ取込で復活", today)
                            await _replace_assignments(
                                db, revive, [s for s in (sid, sid2) if s is not None]
                            )
                            # 案B②: 復活枠へ新人 staff2 の同行リンクを自動作成。
                            # 冪等ガードは **直リンク集合** で見る — UNIQUE は
                            # (staff, visit_id) なので、コースリンクの有無は衝突と無関係。
                            if accompaniment_sid2 is not None and (
                                accompaniment_sid2
                                not in accompaniment_direct_by_visit.get(revive.id, set())
                            ):
                                db.add(
                                    Accompaniment(
                                        accompanying_staff_id=accompaniment_sid2,
                                        target_type="visit",
                                        visit_id=revive.id,
                                        source="manual",
                                        kind=resolve_accompaniment_kind(
                                            staff_map[accompaniment_sid2]
                                        ),
                                        created_by=None,
                                    )
                                )
                                _remember_accompaniment(revive.id, accompaniment_sid2)
                    except IntegrityError:
                        _finish(
                            "failed",
                            "復活時に衝突しました（差分を取り直してください）",
                            target_date,
                        )
                        continue
                    item.visit_id = revive.id
                    _finish("added", detail, target_date)
                    continue
                new_visit = Visit(
                    patient_id=item.patient_id,
                    visit_date=target_date,
                    start_time=start_new,
                    end_time=end_new,
                    type="regular",
                    status="planned",
                    # 取込由来として識別・週再生成からも保護される。
                    source="import",
                    required_staff_count=2 if sid2 is not None else 1,
                    primary_staff_id=sid,
                    secondary_staff_id=sid2,
                    course_id=course.id,
                )
                _stamp_note(new_visit, "カイポケ側で追加された予定", today)
                # 索引が万一古くても UNIQUE 衝突を savepoint で item 単位の failed に
                # 留める (バッチ全体を巻き添えにしない)。
                try:
                    async with db.begin_nested():
                        db.add(new_visit)
                        await db.flush()
                        await _replace_assignments(
                            db, new_visit, [s for s in (sid, sid2) if s is not None]
                        )
                        # 案B②: 新人 staff2 を同行リンク (visit 直リンク) として自動作成。
                        # flush 後 = new_visit.id が確定してから。source='inbound' 値は
                        # 追加せず 'manual' を刻む (CHECK 制約 migration を避ける・既定の
                        # 再展開が上書きしない挙動は 'manual' で正しい)。UNIQUE(staff,
                        # visit_id) が冪等を担保 (新設 visit の id は毎回新規のため衝突なし)。
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
                            _remember_accompaniment(new_visit.id, accompaniment_sid2)
                except IntegrityError:
                    _finish(
                        "failed",
                        "同時刻の予定と衝突しました（差分を取り直してください）",
                        target_date,
                    )
                    continue
                index[(item.patient_id, target_date, start_new)] = new_visit
                item.visit_id = new_visit.id
            _finish("added", detail, target_date)
            continue

        # --- 対象 visit の特定 ----------------------------------------------
        if item.patient_id is None:
            _finish("failed", "利用者名を CareFlow 患者に解決できませんでした", target_date)
            continue
        visit = await _resolve_visit(item, target_date)
        if visit is None:
            _finish("failed", "対象の訪問が CareFlow に見つかりません", target_date)
            continue
        if visit.status == VISIT_STATUS_CANCELLED:
            _finish("skipped", "既にキャンセル済み", target_date)
            continue

        partners = await _group_partners(db, visit)

        # --- delete → キャンセル ---------------------------------------------
        if item.action == "delete":
            for v in partners:
                pending_cancelled.add(v.id)  # add 側の復活判定用 (dry-run でも記録)
                if not dry_run:
                    v.status = VISIT_STATUS_CANCELLED
                    _stamp_note(v, "カイポケ側で削除されたためキャンセル", today)
            _finish(
                "cancelled",
                f"{target_date.month}/{target_date.day} {before.get('start_time') or ''} をキャンセル",
                target_date,
            )
            continue

        # --- edit / date_change → 時刻・日付の変更 ---------------------------
        start_after = parse_hhmm(str(after.get("start_time") or ""))
        end_after = parse_hhmm(str(after.get("end_time") or ""))
        new_date: date | None = None
        if item.action == "date_change":
            try:
                after_day = int(str(after.get("date")))
            except (TypeError, ValueError):
                after_day = -1
            new_date = day_to_date(after_day, week_start, week_end)
            if new_date is None:
                _finish(
                    "failed", f"変更後の日付 {after.get('date')!r} が週レンジ外です", target_date
                )
                continue

        time_changed = (start_after is not None and start_after != visit.start_time) or (
            end_after is not None and end_after != visit.end_time
        )
        date_changed = new_date is not None and new_date != visit.visit_date
        final_date = new_date if (date_changed and new_date is not None) else target_date
        weekday = final_date.weekday()

        # --- 移動先の占有チェック (先に立ちはだかる枠があれば failed で継続) ---
        # visits には partial UNIQUE ``uq_visits_pds_active``
        # (patient_id, visit_date, start_time) WHERE deleted_at IS NULL がある。
        # 移動先が別の visit に既に取られていると flush 時に IntegrityError =
        # 500 (残りの item も巻き添え) になるため、ここで item 単位の failed に
        # 落として続行する。索引は cancelled 行も持つ ⇒ 「今週だけ取消」した枠へ
        # 別訪問を滑り込ませることも防げる (week-cockpit-design.md D1)。
        if time_changed or date_changed:
            final_start = start_after if start_after is not None else visit.start_time
            occupant = index.get((item.patient_id, final_date, final_start))
            if occupant is not None and occupant.id not in {v.id for v in partners}:
                _finish(
                    "failed",
                    f"移動先 {final_date.month}/{final_date.day} "
                    f"{final_start.strftime('%H:%M')} に別の予定があります"
                    "（差分を取り直してください）",
                    target_date,
                )
                continue
            # 移動前のキーを控える (書込後は visit の値が変わるので後から引けない)。
            old_index_keys = [(v.patient_id, v.visit_date, v.start_time) for v in partners]

        # --- スタッフ変更の解決 (R-3a: 「コースの変更」として扱う・設計 §8-1) --
        staff1_before_name = str(before.get("staff1") or "")
        staff1_after_name = str(after.get("staff1") or "")
        staff1_changed = staff1_before_name != staff1_after_name
        staff2_before_name = str(before.get("staff2") or "")
        staff2_after_name = str(after.get("staff2") or "")
        staff2_changed = staff2_before_name != staff2_after_name

        notes: list[str] = []
        new_sid: uuid.UUID | None = None
        if staff1_changed:
            if not staff1_after_name:
                notes.append("担当が空に変更（未対応・要手動確認）")
            else:
                sid_str = match_name(staff1_after_name, staff_index)
                if sid_str:
                    new_sid = uuid.UUID(sid_str)
                    # 方針転換 (PO確定 2026-07-26): カイポケは最終的な「正」のため、
                    # 担当1が新人 (is_trainee) でも現実として適用する (拒否しない)。
                    # ⚠警告注記で可視化し、新人フラグ見直しの判断材料にする。
                    if new_sid in trainee_ids:
                        notes.append(
                            f"⚠担当1「{staff1_after_name}」は新人です"
                            "（単独訪問・新人フラグの見直しを検討）"
                        )
                else:
                    notes.append(f"担当「{staff1_after_name}」未解決（担当変更は未反映）")

        # 担当2: 解決できたときだけ反映 (解除 = 空文字も反映)。
        staff2_update = False
        new_sid2: uuid.UUID | None = None
        accompaniment_sid2: uuid.UUID | None = None
        if staff2_changed:
            if not staff2_after_name:
                staff2_update = True  # 2人目の解除。
            else:
                sid2_str = match_name(staff2_after_name, staff_index)
                if sid2_str:
                    resolved_sid2 = uuid.UUID(sid2_str)
                    # staff2 の 3 段階判定 (案B・設計 §9 拡張 / 一般化 §3-5):
                    # ① 既存の同行リンクと一致 (**kind 不問** = 新人同行も一般スタッフの
                    #    同行も) → secondary へ書かず要2名化しない (ラウンドトリップ
                    #    汚染防止。同行は accompaniments が唯一の正典)。
                    #    突合集合は直リンク ∪ コースリンクの和 (resolve_..._by_visit)。
                    if resolved_sid2 in accompaniment_by_visit.get(visit.id, set()):
                        notes.append(
                            f"担当2「{staff2_after_name}」は同行のため未反映（要2名化しない）"
                        )
                    # ② リンクは無いが新人 → 同行リンクを自動作成し要2名化しない
                    #    (一般スタッフへは開放しない = 一般化 §3-5 で意図的に見送り)。
                    elif resolved_sid2 in trainee_ids:
                        accompaniment_sid2 = resolved_sid2
                    # ③ 新人でない実スタッフ (リンク無し) → 従来どおり secondary + 要2名化。
                    else:
                        new_sid2 = resolved_sid2
                        staff2_update = True
                else:
                    notes.append(f"担当2「{staff2_after_name}」未解決（未反映）")

        if (
            not time_changed
            and not date_changed
            and new_sid is None
            and not staff2_update
            and accompaniment_sid2 is None
        ):
            _finish("skipped", "・".join(notes) or "変更点なし", target_date)
            continue

        changes: list[str] = []
        if date_changed and new_date is not None:
            changes.append(
                f"{visit.visit_date.month}/{visit.visit_date.day}→{new_date.month}/{new_date.day}"
            )
        if time_changed and start_after is not None:
            changes.append(f"{visit.start_time.strftime('%H:%M')}→{start_after.strftime('%H:%M')}")

        # コース解決: 丸ごと交代 / 既存コースへ移動 / 臨時コース新設。
        course_takeover = False
        target_course: Course | None = None
        need_temp_course = False
        if new_sid is not None:
            cur_course = course_idx.by_id.get(visit.course_id) if visit.course_id else None
            tkey = (visit.course_id, target_date) if visit.course_id is not None else None
            office_id = patient_office.get(item.patient_id)
            if tkey is not None and takeover.get(tkey) == new_sid:
                course_takeover = True
                cur_code = cur_course.code if cur_course else "?"
                changes.append(
                    f"担当 {staff1_before_name or '−'}→{staff1_after_name}"
                    f"（コース{cur_code}を丸ごと交代）"
                )
            elif office_id is None:
                notes.append("患者の主担当拠点が特定できず担当変更は未反映")
                new_sid = None
            else:
                target_course = course_idx.by_staff.get((weekday, office_id, new_sid))
                cur_code = cur_course.code if cur_course else "−"
                if target_course is not None:
                    changes.append(
                        f"担当 {staff1_before_name or '−'}→{staff1_after_name}"
                        f"（コース{cur_code}→{target_course.code}）"
                    )
                else:
                    need_temp_course = True
                    changes.append(
                        f"担当 {staff1_before_name or '−'}→{staff1_after_name}（臨時コース新設）"
                    )
        # ⛔ NG: 担当変更 (丸ごと交代 / 既存コースへ移動 / 臨時新設) 後の担当 × 患者。
        # 丸ごと交代は事前パスでも同組を積むが、collect_ng_conflicts が重複を畳む。
        if new_sid is not None or new_sid2 is not None:
            ng_course = target_course
            if ng_course is None and course_takeover and visit.course_id is not None:
                ng_course = course_idx.by_id.get(visit.course_id)
            ng_code = str(ng_course.code) if ng_course is not None else None
            for ng_sid in (new_sid, new_sid2):
                if ng_sid is not None:
                    ng_pairs.append((item.patient_id, ng_sid, final_date, ng_code))

        if staff2_update:
            changes.append(f"担当2 →{staff2_after_name}" if new_sid2 is not None else "担当2を解除")
        # 案B②: 新人 staff2 の自動同行化はリンク作成が実変更 → updated として集計する
        # (他に変更点が無くても skipped にしない)。
        if accompaniment_sid2 is not None:
            changes.append(f"担当2「{staff2_after_name}」は新人のため同行として取り込みました")

        if not changes:
            _finish("skipped", "・".join(notes) or "変更点なし", target_date)
            continue
        detail = "・".join(changes + notes)

        if not dry_run:
            if need_temp_course and new_sid is not None:
                office_id = patient_office.get(item.patient_id)
                if office_id is None:
                    # 上流の分岐で除外済みだが、リファクタで壊れないよう明示ガード。
                    _finish("failed", "患者の主担当拠点が特定できません", target_date)
                    continue
                target_course = await ensure_temp_course(
                    db,
                    course_idx,
                    weekday=weekday,
                    office_id=office_id,
                    staff_id=new_sid,
                    now=now,
                )
                if target_course is None:
                    _finish("failed", "臨時コース枠（臨〜臨9）が満杯です", target_date)
                    continue
            if course_takeover and new_sid is not None and visit.course_id is not None:
                course = course_idx.by_id.get(visit.course_id)
                if course is not None and course.assigned_staff_id != new_sid:
                    course.assigned_staff_id = new_sid
                    course.staff_assigned_at = now
            for v in partners:
                if date_changed and new_date is not None:
                    v.visit_date = new_date
                if time_changed:
                    if start_after is not None:
                        v.start_time = start_after
                    if end_after is not None:
                        v.end_time = end_after
                if new_sid is not None:
                    if target_course is not None and not course_takeover:
                        v.course_id = target_course.id
                    v.primary_staff_id = new_sid
                if staff2_update:
                    v.secondary_staff_id = new_sid2
                    v.required_staff_count = 2 if new_sid2 is not None else 1
                # その週限りの変更として週再生成から保護する。
                v.source = "manual_week"
                _stamp_note(v, detail, today)
                if new_sid is not None or staff2_update:
                    primary = new_sid if new_sid is not None else v.primary_staff_id
                    secondary = new_sid2 if staff2_update else v.secondary_staff_id
                    await _replace_assignments(
                        db, v, [s for s in (primary, secondary) if s is not None]
                    )
            # 案B②: 新人 staff2 を同行リンク (visit 直リンク) として自動作成 (対象 visit へ)。
            # source='inbound' 値は追加せず 'manual' を刻む (CHECK 制約 migration を避ける)。
            # 同一実行内の item 重複でも二重に張らないよう、作成後は突合集合へ反映して
            # 以降は判定 ① (スキップ) に落ちるようにする。UNIQUE(staff, visit_id) も冪等
            # を担保 (別実行の二重取込は再ロードされた突合集合で判定 ① に落ちる)。
            #
            # この ``db.add`` が savepoint の外にあるのは意図的: edit 経路は visit を
            # INSERT しないため UNIQUE 衝突の主因が無く、唯一の衝突源だった「同一実行内の
            # 二重 INSERT」は上の集合更新 (``_remember_accompaniment``) で予防している。
            # savepoint で包むと、まとめて 1 TX にしている呼び出し側の境界を細切れにする
            # 割に得るものが無い。
            if accompaniment_sid2 is not None:
                db.add(
                    Accompaniment(
                        accompanying_staff_id=accompaniment_sid2,
                        target_type="visit",
                        visit_id=visit.id,
                        source="manual",
                        kind=resolve_accompaniment_kind(staff_map[accompaniment_sid2]),
                        created_by=None,
                    )
                )
                _remember_accompaniment(visit.id, accompaniment_sid2)
        # 索引を移動後のキーへ張り替える (同一実行内の後続 item が旧キーを
        # 占有済みと誤判定したり、空いた枠へ二重に移動して 500 になるのを防ぐ)。
        # dry-run でも張り替える = 予測と実適用の結果を一致させる。
        if time_changed or date_changed:
            _final_start = start_after if start_after is not None else visit.start_time
            for key in old_index_keys:
                index.pop(key, None)
            index[(item.patient_id, final_date, _final_start)] = visit
        _finish("updated", detail, target_date)

    if dry_run:
        summary.ng_conflicts = await collect_ng_conflicts(db, ng_pairs)

    return summary
