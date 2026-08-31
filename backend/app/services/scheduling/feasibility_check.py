"""実現性チェック (移動・重なり・バッファ・同住所ルール) — 読み取り専用の判定サービス.

PO 要望 (2026-08-31): 手で並べ替えた週の予定について「物理的に移動できない」「重なっている」
「バッファが足りない」をらく助の内部前提と同じ基準で機械判定し、A4 レポートにする。
運用当日は ``tools/run_feasibility.sh`` (スクリプト) で回していたものを、ボタン 1 つで
出せるように移植した。設計 = ``docs/plans/feasibility-check-design.md``。

判定の前提 (すべて既存の単一ソースから取る・ここで独自定義しない):
  * 移動時間 = 患者座標間の直線距離 (haversine) / ``config.travel_speed_kmh``
    → ``auto_allocator_v2.haversine_minutes`` (最低 1 分・同住所 0 分)
  * 訪問間バッファ = ``config.visit_buffer_min``
  * 昼休み = ``config.lunch_window_*`` の間に ``config.lunch_duration_min`` の連続空きがあるか
  * 同住所 = ``constants.SAME_ADDRESS_TOLERANCE`` (座標 0.001 度バケット)
  * 同住所ペア = 同一スタッフ・同住所・**別患者** の 2 名を「同時刻スタート」または
    「端点連続」で配置 (``auto_allocator_v2`` の「ペア関係」と同じ・同一患者の分割訪問は除外)
    → 占有 = max(サービス合計, ``SAME_ADDRESS_PAIR_MIN_OCCUPANCY``=90 分)
    ※ エンジン側は max(自分の終了, 起点+90) だが、本レポートは「合計」で見る (物理的により厳しい側)。
      エンジンを本レポートに合わせて変えないこと (レビュー LOW-5・2026-08-31)。
  * 同住所同時刻の上限 = ``SAME_ADDRESS_MAX``=2 名 (``auto_allocator`` と同じく **同時刻** のみ判定)
  * 参考: 実走行想定 = 直線 × ``ROAD_FACTOR`` (1.3・レポート専用の目安)
  * 担当の解決 = 盤面 (``board_service``) と同じく **コースの担当 (courses.assigned_staff_id) を正**、
    コース無し/未割当なら ``visits.primary_staff_id`` にフォールバック
  * 同行 = ``visits.secondary_staff_id / mentor_staff_id`` (旧) + ``accompaniments`` (正典・visit 単位)

判定の種類 (``Finding.kind``):
  重なり / 移動不可 / バッファ不足 / 要注意(実走行) / 同住所ペア90分未確保 /
  同住所ペア:同時刻でない / 同住所3名以上 / 昼休みなし / 座標なし

DB への書込は一切しない (SELECT のみ)。
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accompaniment import Accompaniment
from app.models.course import Course
from app.models.office import Office
from app.models.patient import Patient
from app.models.staff import Staff, StaffEvent
from app.models.visit import Visit
from app.services.scheduling.auto_allocator import SAME_ADDRESS_MAX
from app.services.scheduling.auto_allocator_v2 import (
    SAME_ADDRESS_PAIR_MIN_OCCUPANCY,
    haversine_km,
    haversine_minutes,
)
from app.services.scheduling.config import SchedulingConfig, load_scheduling_config
from app.services.scheduling.constants import SAME_ADDRESS_TOLERANCE

# 実走行想定の道路係数 (直線距離の 1.3 倍)。レポートの「要注意」判定にだけ使う目安。
ROAD_FACTOR: float = 1.3
# 朝会など「所属拠点で行う」イベントのタイトル。拠点座標を出発地として移動判定に使う。
OFFICE_EVENT_TITLES: frozenset[str] = frozenset({"朝会"})
# 担当未定 (コース担当も primary_staff_id も無い) の訪問をまとめる仮想スタッフ。
UNASSIGNED_STAFF_KEY = "__unassigned__"
UNASSIGNED_STAFF_LABEL = "担当未定"
ROLE_PRIMARY = "主"
ROLE_ACCOMPANY = "同行"
ROLE_EVENT = "行事"

KIND_OVERLAP = "重なり"
KIND_IMPOSSIBLE = "移動不可"
KIND_TIGHT = "バッファ不足"
KIND_WATCH = "要注意(実走行)"
KIND_PAIR_SHORT = "同住所ペア90分未確保"
KIND_PAIR_NOT_SAME_START = "同住所ペア:同時刻でない"
KIND_PAIR_OVER = "同住所3名以上"
KIND_NO_LUNCH = "昼休みなし"
KIND_NO_COORD = "座標なし"
# 「成立しない」(❗) と「余裕がない/ルール逸脱」(△)。それ以外 (昼休み・座標なし) は参考。
HARD_KINDS: frozenset[str] = frozenset({KIND_OVERLAP, KIND_IMPOSSIBLE, KIND_PAIR_OVER})
SOFT_KINDS: frozenset[str] = frozenset(
    {KIND_TIGHT, KIND_WATCH, KIND_PAIR_SHORT, KIND_PAIR_NOT_SAME_START}
)
# item.level → (❗/△/参考, ラベル)。HTML 側の表示にも使う。
LEVEL_HARD: frozenset[str] = frozenset({"overlap", "impossible", "pair_over"})
LEVEL_SOFT: frozenset[str] = frozenset({"tight", "watch", "pair_seq"})


@dataclass
class TimelineItem:
    """1 スタッフ・1 日の時間軸に載る 1 予定 (訪問 or イベント)."""

    kind: str  # 'visit' | 'event'
    start_min: int
    end_min: int
    name: str  # 患者名 / イベントタイトル
    address: str = ""
    pos: tuple[float, float] | None = None
    role: str = ROLE_PRIMARY  # 主 / 同行 / 行事
    blocking: bool = True  # イベントのみ: False なら時間軸を占有しない (メモ扱い)
    patient_id: uuid.UUID | None = None  # 同住所ペアの「別患者」判定に使う
    # --- 判定結果 (evaluate が埋める) ---
    level: str = "ok"  # ok | overlap | impossible | tight | watch | pair_over | pair_seq
    note: str = ""
    travel_km: float | None = None
    travel_min: int | None = None
    gap_min: int | None = None


@dataclass
class Finding:
    staff: str
    day: date
    kind: str
    at: str  # HH:MM ('' = 日単位の指摘)
    to: str  # 該当予定 (患者名 / 説明)
    frm: str  # 直前の予定 (患者名 / 説明)
    gap_min: int | None = None
    need_min: int | None = None
    km: float | None = None
    # 表示名 (staff) とは別の一意キー (staff.id)。同名スタッフの混同防止 (レビュー LOW-6)。
    staff_key: str = ""

    @property
    def severity(self) -> str:
        if self.kind in HARD_KINDS:
            return "hard"
        if self.kind in SOFT_KINDS:
            return "soft"
        return "info"


@dataclass
class DayTimeline:
    staff: str
    day: date
    items: list[TimelineItem]
    lunch_free_min: int  # 昼休み枠内の最長連続空き (分)
    staff_key: str = ""  # staff.id (表示名とは別の一意キー)


@dataclass
class FeasibilityReport:
    iso_year: int
    iso_week: int
    week_start: date
    week_end: date
    generated_at: datetime
    config: SchedulingConfig
    timelines: list[DayTimeline] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    visit_count: int = 0  # 主担当として載った訪問の数 (2 名体制も 1 と数える)
    event_count: int = 0

    @property
    def summary(self) -> dict[str, int]:
        return dict(Counter(f.kind for f in self.findings))

    @property
    def hard_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "hard")

    @property
    def soft_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "soft")


# ---------------------------------------------------------------------------
# 純粋関数 (テスト容易性のため DB に依存しない)
# ---------------------------------------------------------------------------


def fmt_hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _to_min(t: time) -> int:
    return t.hour * 60 + t.minute


def _addr_bucket(pos: tuple[float, float] | None) -> tuple[int, int] | None:
    """同住所判定用の座標バケット (``SAME_ADDRESS_TOLERANCE`` 度刻み)."""
    if pos is None:
        return None
    return (round(pos[0] / SAME_ADDRESS_TOLERANCE), round(pos[1] / SAME_ADDRESS_TOLERANCE))


def _same_address(a: tuple[float, float] | None, b: tuple[float, float] | None) -> bool:
    return a is not None and b is not None and _addr_bucket(a) == _addr_bucket(b)


@dataclass
class _PairBlock:
    members: list[TimelineItem]
    start_min: int
    end_min: int
    same_start: bool

    @property
    def label(self) -> str:
        return "・".join(m.name for m in self.members)


def _pair_blocks(items: list[TimelineItem]) -> list[_PairBlock]:
    """同住所ペア (同時刻 or 端点連続・**別患者**) を検出し占有ブロックを返す.

    ``auto_allocator_v2`` の「ペア関係」定義 (同 start_time / end == start の連続・patient_id が
    異なる) に合わせる。同建物でも間に隙間がある単なる連続訪問はペアではない。
    同一患者の分割訪問 (patient_id が同じ) はペアにしない。
    """
    blocks: list[_PairBlock] = []
    by_addr: dict[tuple[int, int], list[TimelineItem]] = defaultdict(list)
    for it in items:
        if it.kind == "visit" and it.pos is not None:
            b = _addr_bucket(it.pos)
            if b is not None:
                by_addr[b].append(it)
    for group in by_addr.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda x: (x.start_min, x.end_min))
        used: set[int] = set()
        for i, anchor in enumerate(group):
            if id(anchor) in used:
                continue
            members = [anchor]
            for other in group[i + 1 :]:
                if id(other) in used:
                    continue
                if other.patient_id is not None and other.patient_id == anchor.patient_id:
                    continue  # 同一患者 (分割訪問 / 2 名体制の 2 行目) はペアではない
                if other.start_min == anchor.start_min or other.start_min == members[-1].end_min:
                    members.append(other)
            if len(members) >= 2:
                used.update(id(m) for m in members)
                s = min(m.start_min for m in members)
                total = sum(m.end_min - m.start_min for m in members)
                e = max(
                    s + max(total, SAME_ADDRESS_PAIR_MIN_OCCUPANCY), max(m.end_min for m in members)
                )
                blocks.append(
                    _PairBlock(
                        members=members,
                        start_min=s,
                        end_min=e,
                        same_start=len({m.start_min for m in members}) == 1,
                    )
                )
    return blocks


def evaluate_day(
    staff: str,
    day: date,
    items: list[TimelineItem],
    config: SchedulingConfig,
    *,
    staff_key: str | None = None,
) -> tuple[DayTimeline, list[Finding]]:
    """1 スタッフ・1 日の時間軸を判定する (in-place で item.level/note を埋める).

    ``staff_key`` は一意キー (staff.id)。省略時は表示名をキーにする (純粋関数テスト用)。
    """
    skey = staff_key or staff
    items = sorted(items, key=lambda x: (x.start_min, x.end_min))
    findings: list[Finding] = []
    buffer_min = config.visit_buffer_min
    speed = config.travel_speed_kmh

    blocks = _pair_blocks(items)
    member_of: dict[int, _PairBlock] = {id(m): b for b in blocks for m in b.members}
    for b in blocks:
        # 同住所同時刻の上限 (SAME_ADDRESS_MAX) は「同時刻」にだけ適用 (auto_allocator と同じ)。
        # 連続配置で 3 名を回るのは物理的に成立するので違反にしない。
        if b.same_start and len(b.members) > SAME_ADDRESS_MAX:
            for m in b.members:
                m.level = "pair_over"
            findings.append(
                Finding(
                    staff, day, KIND_PAIR_OVER, fmt_hm(b.start_min), b.label, "", None, None, None
                )
            )
        elif not b.same_start:
            for m in b.members:
                m.level = "pair_seq"
            findings.append(
                Finding(
                    staff,
                    day,
                    KIND_PAIR_NOT_SAME_START,
                    fmt_hm(b.start_min),
                    b.label,
                    "連続配置（ルールは同時刻スタート）",
                    None,
                    None,
                    None,
                )
            )

    def _set(it: TimelineItem, level: str, note: str) -> None:
        # ❗ は △ / ペア表示を上書きする。既に ❗ の項目は弱い判定で上書きしない。
        if it.level in LEVEL_HARD and level not in LEVEL_HARD:
            it.note = note if not it.note else f"{it.note}｜{note}"
            return
        it.level = level
        it.note = note

    prev: TimelineItem | None = None
    for it in items:
        hard = it.kind == "visit" or it.blocking
        blk = member_of.get(id(it))
        pair_note = ""
        if blk is not None:
            pair_note = (
                f"同住所ペア({'同時刻' if blk.same_start else '連続'}) {blk.label}: "
                f"占有 {fmt_hm(blk.start_min)}〜{fmt_hm(blk.end_min)}（{blk.end_min - blk.start_min}分）"
            )
        if prev is not None and hard:
            if blk is not None and member_of.get(id(prev)) is blk:
                pass  # ペアの相方同士: 重なり/移動判定はしない (同時刻 or 連続が正)
            else:
                prev_blk = member_of.get(id(prev))
                prev_end = prev_blk.end_min if prev_blk is not None else prev.end_min
                gap = it.start_min - prev_end
                it.gap_min = gap
                if prev_blk is not None and gap < 0 and it.start_min >= prev.end_min:
                    _set(
                        it,
                        "tight",
                        f"同住所ペア90分未確保: {prev_blk.label} の占有 〜{fmt_hm(prev_blk.end_min)} に対し "
                        f"{fmt_hm(it.start_min)} 開始（{-gap}分不足）",
                    )
                    findings.append(
                        Finding(
                            staff,
                            day,
                            KIND_PAIR_SHORT,
                            fmt_hm(it.start_min),
                            it.name,
                            prev_blk.label,
                            gap,
                            SAME_ADDRESS_PAIR_MIN_OCCUPANCY,
                            None,
                        )
                    )
                elif gap < 0:
                    _set(
                        it,
                        "overlap",
                        f"重なり {abs(gap)}分（前: {prev.name} 〜{fmt_hm(prev.end_min)}）",
                    )
                    findings.append(
                        Finding(
                            staff,
                            day,
                            KIND_OVERLAP,
                            fmt_hm(it.start_min),
                            it.name,
                            prev.name,
                            gap,
                            None,
                            None,
                        )
                    )
                elif prev.pos is not None and it.pos is not None:
                    km = haversine_km(prev.pos[0], prev.pos[1], it.pos[0], it.pos[1])
                    same = _same_address(prev.pos, it.pos)
                    need = 0 if same else haversine_minutes(km, speed_kmh=speed)
                    need_road = 0 if same else haversine_minutes(km * ROAD_FACTOR, speed_kmh=speed)
                    it.travel_km = km
                    it.travel_min = need
                    if same:
                        it.note = "同住所（移動なし）"
                    elif gap < need:
                        _set(
                            it,
                            "impossible",
                            f"移動不可: 間隔{gap}分 < 移動{need}分（直線{km:.1f}km）",
                        )
                        findings.append(
                            Finding(
                                staff,
                                day,
                                KIND_IMPOSSIBLE,
                                fmt_hm(it.start_min),
                                it.name,
                                prev.name,
                                gap,
                                need,
                                km,
                            )
                        )
                    elif gap < need + buffer_min:
                        _set(
                            it,
                            "tight",
                            f"バッファ不足: 間隔{gap}分 < 移動{need}分＋{buffer_min}分（直線{km:.1f}km）",
                        )
                        findings.append(
                            Finding(
                                staff,
                                day,
                                KIND_TIGHT,
                                fmt_hm(it.start_min),
                                it.name,
                                prev.name,
                                gap,
                                need,
                                km,
                            )
                        )
                    elif gap < need_road + buffer_min:
                        _set(
                            it,
                            "watch",
                            f"要注意: 実走行想定{need_road}分＋{buffer_min}分 > 間隔{gap}分（直線{km:.1f}km）",
                        )
                        findings.append(
                            Finding(
                                staff,
                                day,
                                KIND_WATCH,
                                fmt_hm(it.start_min),
                                it.name,
                                prev.name,
                                gap,
                                need_road,
                                km,
                            )
                        )
                    else:
                        it.note = f"移動{need}分／間隔{gap}分"
                elif prev.kind == "event" and prev.pos is None:
                    it.note = "（直前が場所不明のイベント: 移動判定なし）"
                elif it.pos is None or prev.pos is None:
                    # どちらかが座標なし = 移動判定不可 (指摘そのものは下の一括処理で 1 患者 1 回)。
                    who = it.name if it.pos is None else prev.name
                    it.note = f"（{who} が座標なし: 移動判定なし）"
        if pair_note:
            it.note = pair_note if not it.note else f"{it.note}｜{pair_note}"
        if hard and (prev is None or it.end_min > prev.end_min):
            prev = it

    # 座標のない訪問は黙って OK にせず、1 件 1 回「座標なし」として参考に残す (レビュー MEDIUM-2/NEW-4)。
    for it in items:
        if it.kind == "visit" and it.pos is None:
            it.note = (
                "座標なし: 移動判定不可" if not it.note else f"{it.note}｜座標なし: 移動判定不可"
            )
            findings.append(Finding(staff, day, KIND_NO_COORD, fmt_hm(it.start_min), it.name, ""))

    # 昼休み: 窓内に lunch_duration_min の連続空きがあるか
    busy = [(x.start_min, x.end_min) for x in items if x.kind == "visit" or x.blocking]
    w_s, w_e = _to_min(config.lunch_window_start), _to_min(config.lunch_window_end)
    free = best = 0
    for t in range(w_s, w_e):
        if any(s <= t < e for s, e in busy):
            free = 0
        else:
            free += 1
            best = max(best, free)
    if best < config.lunch_duration_min and any(x.kind == "visit" for x in items):
        findings.append(
            Finding(
                staff,
                day,
                KIND_NO_LUNCH,
                "",
                f"{fmt_hm(w_s)}〜{fmt_hm(w_e)} の最長空き {best}分",
                "",
                None,
                config.lunch_duration_min,
                None,
            )
        )
    for f in findings:
        f.staff_key = skey
    return (
        DayTimeline(staff=staff, day=day, items=items, lunch_free_min=best, staff_key=skey),
        findings,
    )


def evaluate_week(
    items_by_key: dict[tuple[str, date], list[TimelineItem]],
    config: SchedulingConfig,
    *,
    iso_year: int,
    iso_week: int,
    week_start: date,
    week_end: date,
    staff_names: dict[str, str] | None = None,
) -> FeasibilityReport:
    """全スタッフ×日を判定してレポートに束ねる (純粋関数).

    ``items_by_key`` のキーは ``(staff_key, day)``。``staff_names`` で表示名へ解決する
    (省略時はキーをそのまま表示名にする)。同名スタッフが混ざらないよう、ローダーは
    staff.id をキーにする (レビュー LOW-6)。
    """
    names = staff_names or {}
    report = FeasibilityReport(
        iso_year=iso_year,
        iso_week=iso_week,
        week_start=week_start,
        week_end=week_end,
        generated_at=datetime.now(UTC),
        config=config,
    )
    ordered = sorted(items_by_key.items(), key=lambda kv: (kv[0][1], names.get(kv[0][0], kv[0][0])))
    # 訪問数は「患者×日×時間帯」で一意に数える (2 名体制 = 2 行でも 1 件・レビュー NEW-3)。
    seen_visits: set[tuple[Any, date, int, int]] = set()
    for (staff_key, day), items in ordered:
        label = names.get(staff_key, staff_key)
        tl, fs = evaluate_day(label, day, items, config, staff_key=staff_key)
        report.timelines.append(tl)
        report.findings.extend(fs)
        for x in items:
            if x.kind == "visit" and x.role == ROLE_PRIMARY:
                seen_visits.add((x.patient_id or x.name, day, x.start_min, x.end_min))
        report.event_count += sum(1 for x in items if x.kind == "event")
    report.visit_count = len(seen_visits)
    return report


# ---------------------------------------------------------------------------
# DB ローダー (SELECT のみ)
# ---------------------------------------------------------------------------


def _event_local(dt: datetime) -> datetime:
    """StaffEvent の starts_at/ends_at をアプリ内の壁時計 (JST の HH:MM) に正規化する.

    運用上 naive JST を UTC として書き込んでいるため (``staff_event_defaults.content_key``
    と同じ規約)、aware なら UTC へ揃えて naive にする。SQLite (テスト) は naive のまま返る。
    """
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


async def load_week_items(
    db: AsyncSession,
    *,
    week_start: date,
    week_end: date,
    office_id: uuid.UUID | None = None,
) -> tuple[dict[tuple[str, date], list[TimelineItem]], dict[str, str]]:
    """対象期間の訪問 (cancelled 除外) と拘束イベントを時間軸アイテムに変換する.

    Returns ``(items_by_(staff_key, day), staff_names)``。staff_key = staff.id の文字列
    (担当未定は ``UNASSIGNED_STAFF_KEY``)。

    * 担当 = コースの担当 (``courses.assigned_staff_id``) を正、無ければ ``primary_staff_id``
      (盤面 ``board_service`` と同じ解決順)。
    * 同行 = ``secondary_staff_id`` / ``mentor_staff_id`` (旧) と ``accompaniments`` (visit 単位)
      をその職員の時間軸に「同行」として載せる。
    * 2 名体制 (同一患者・同時刻の 2 行) や旧列の相互参照で同じ職員に同じ訪問が 2 回来る場合は
      1 つにまとめる (「主」を優先)。
    * 拠点フィルタは盤面と同じくコースの拠点。コース無しの訪問は患者の主担当拠点で判定。
    """
    staff_rows = (await db.scalars(select(Staff).where(Staff.deleted_at.is_(None)))).all()
    staff_names: dict[str, str] = {
        str(s.id): (s.name or "").replace("　", " ").strip() for s in staff_rows
    }
    staff_names[UNASSIGNED_STAFF_KEY] = UNASSIGNED_STAFF_LABEL
    staff_office = {s.id: s.primary_office_id for s in staff_rows}
    offices = (await db.scalars(select(Office))).all()
    office_pos: dict[uuid.UUID, tuple[float, float]] = {
        o.id: (float(o.lat), float(o.lng))
        for o in offices
        if o.lat is not None and o.lng is not None
    }

    # コースは盤面 (board_service) と同じガード付きで結合: 未削除・同 ISO 週。
    # 条件に合わないコース (削除済み / 別週の残骸) は無いものとして primary_staff_id へ落とす。
    iso_year, iso_week, _ = week_start.isocalendar()
    course_on = and_(
        Course.id == Visit.course_id,
        Course.deleted_at.is_(None),
        Course.iso_year == iso_year,
        Course.iso_week == iso_week,
    )
    stmt = (
        select(Visit, Patient, Course)
        .join(Patient, Patient.id == Visit.patient_id)
        .outerjoin(Course, course_on)
        .where(
            Visit.deleted_at.is_(None),
            Visit.status != "cancelled",
            Visit.visit_date >= week_start,
            Visit.visit_date <= week_end,
        )
    )
    if office_id is not None:
        stmt = stmt.where(
            or_(
                Course.office_id == office_id,
                and_(Course.id.is_(None), Patient.primary_office_id == office_id),
            )
        )
    rows = (await db.execute(stmt)).all()

    # 同行リンク (accompaniments): visit 単位 (target_type='visit') と コース単位
    # (target_type='course' = 週の既定展開) の両方を拾う (レビュー MEDIUM-5)。
    visit_ids = [v.id for v, _p, _c in rows]
    course_ids = sorted({c.id for _v, _p, c in rows if c is not None})
    accompany: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    accompany_by_course: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    if visit_ids:
        acc_rows = (
            await db.execute(
                select(Accompaniment.visit_id, Accompaniment.accompanying_staff_id).where(
                    Accompaniment.visit_id.in_(visit_ids)
                )
            )
        ).all()
        for vid, sid in acc_rows:
            if vid is not None and sid is not None:
                accompany[vid].add(sid)
    if course_ids:
        acc_c_rows = (
            await db.execute(
                select(Accompaniment.course_id, Accompaniment.accompanying_staff_id).where(
                    Accompaniment.course_id.in_(course_ids)
                )
            )
        ).all()
        for cid, sid in acc_c_rows:
            if cid is not None and sid is not None:
                accompany_by_course[cid].add(sid)

    items: dict[tuple[str, date], list[TimelineItem]] = defaultdict(list)
    # 同じ職員 × 同じ患者 × 同じ時間帯は 1 アイテム (「主」優先)。
    seen: dict[tuple[str, uuid.UUID, date, int, int], TimelineItem] = {}
    for v, p, c in rows:
        pos = (float(p.lat), float(p.lng)) if p.lat is not None and p.lng is not None else None
        pname = (p.name or "").replace("　", " ").strip()
        primary_sid = (c.assigned_staff_id if c is not None and c.assigned_staff_id else None) or (
            v.primary_staff_id
        )
        primary_key = str(primary_sid) if primary_sid and str(primary_sid) in staff_names else None
        who: dict[str, str] = {}
        who[primary_key or UNASSIGNED_STAFF_KEY] = ROLE_PRIMARY
        linked = set(accompany.get(v.id, ()))
        if c is not None:
            linked |= accompany_by_course.get(c.id, set())
        for sid in (v.secondary_staff_id, v.mentor_staff_id, *sorted(linked)):
            if sid is None:
                continue
            key = str(sid)
            if key in staff_names and key not in who:
                who[key] = ROLE_ACCOMPANY
        s_min, e_min = _to_min(v.start_time), _to_min(v.end_time)
        for key, role in who.items():
            dedupe_key = (key, v.patient_id, v.visit_date, s_min, e_min)
            existing = seen.get(dedupe_key)
            if existing is not None:
                if role == ROLE_PRIMARY and existing.role != ROLE_PRIMARY:
                    existing.role = ROLE_PRIMARY
                continue
            item = TimelineItem(
                kind="visit",
                start_min=s_min,
                end_min=e_min,
                name=pname,
                address=p.address or "",
                pos=pos,
                role=role,
                patient_id=v.patient_id,
            )
            seen[dedupe_key] = item
            items[(key, v.visit_date)].append(item)

    ev_rows = (
        await db.scalars(
            select(StaffEvent).where(
                StaffEvent.cancelled_at.is_(None),
                StaffEvent.starts_at >= datetime.combine(week_start, time.min),
                StaffEvent.starts_at < datetime.combine(week_end + timedelta(days=1), time.min),
            )
        )
    ).all()
    for ev in ev_rows:
        s_dt, e_dt = _event_local(ev.starts_at), _event_local(ev.ends_at)
        if s_dt == e_dt:
            continue  # メモ (0 分) は時間軸に載せない
        key = str(ev.staff_id)
        if key not in staff_names:
            continue
        ev_office = staff_office.get(ev.staff_id)
        if office_id is not None and ev_office != office_id:
            continue
        title = (ev.title or ev.event_type or "").strip()
        # 朝会など拠点で行うイベントは所属拠点の座標を出発地にする (それ以外は場所不明)。
        pos = (
            office_pos.get(ev_office)
            if (title in OFFICE_EVENT_TITLES and ev_office is not None)
            else None
        )
        s_min = _to_min(s_dt.time())
        # 日跨ぎ (終了が翌日) は当日 24:00 で切る (稀・レビュー LOW-3)。
        e_min = _to_min(e_dt.time()) if e_dt.date() == s_dt.date() else 24 * 60
        items[(key, s_dt.date())].append(
            TimelineItem(
                kind="event",
                start_min=s_min,
                end_min=e_min,
                name=title or "(無題)",
                pos=pos,
                role=ROLE_EVENT,
                blocking=bool(ev.blocking),
            )
        )
    return items, staff_names


async def build_feasibility_report(
    db: AsyncSession,
    *,
    iso_year: int,
    iso_week: int,
    office_id: uuid.UUID | None = None,
    days: int = 6,
) -> FeasibilityReport:
    """ISO 週 (月曜起点・既定 6 日 = 月〜土) の実現性レポートを作る (read-only)."""
    week_start = date.fromisocalendar(iso_year, iso_week, 1)
    week_end = week_start + timedelta(days=max(1, min(days, 7)) - 1)
    config = await load_scheduling_config(db)
    items, names = await load_week_items(
        db, week_start=week_start, week_end=week_end, office_id=office_id
    )
    return evaluate_week(
        items,
        config,
        iso_year=iso_year,
        iso_week=iso_week,
        week_start=week_start,
        week_end=week_end,
        staff_names=names,
    )


def report_to_dict(report: FeasibilityReport) -> dict[str, Any]:
    """API (JSON) 用の素朴な辞書化."""
    return {
        "iso_year": report.iso_year,
        "iso_week": report.iso_week,
        "week_start": report.week_start.isoformat(),
        "week_end": report.week_end.isoformat(),
        "generated_at": report.generated_at.isoformat(),
        "visit_count": report.visit_count,
        "event_count": report.event_count,
        "hard_count": report.hard_count,
        "soft_count": report.soft_count,
        "summary": report.summary,
        "assumptions": {
            "travel_speed_kmh": report.config.travel_speed_kmh,
            "visit_buffer_min": report.config.visit_buffer_min,
            "lunch_duration_min": report.config.lunch_duration_min,
            "lunch_window": (
                f"{report.config.lunch_window_start:%H:%M}-{report.config.lunch_window_end:%H:%M}"
            ),
            "road_factor": ROAD_FACTOR,
            "same_address_pair_min_occupancy": SAME_ADDRESS_PAIR_MIN_OCCUPANCY,
        },
        "findings": [
            {
                "staff": f.staff,
                "day": f.day.isoformat(),
                "kind": f.kind,
                "severity": f.severity,
                "at": f.at,
                "to": f.to,
                "from": f.frm,
                "gap_min": f.gap_min,
                "need_min": f.need_min,
                "km": round(f.km, 2) if f.km is not None else None,
            }
            for f in report.findings
        ],
    }
