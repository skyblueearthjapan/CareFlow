"""月の実績の組み方と判定 (純粋関数・DB を見ない).

正典: ``docs/tools/monthly-actual/review_oct.py`` (11/1 に 10 月分で使った道具) と、11/1 の
PO の決まり (``docs/plans/session-2026-11-01-HANDOFF.md`` §1)。

組み方
    (日・利用者) で組にする。利用者名は字体 (齋/齊・髙/高・﨑/崎・栁/柳・邉邊/辺・濵濱/浜・
    德/徳・冨/富 など) と空白をそろえる。らく助の訪問を予定の開始の順に見て、到着 (無ければ
    予定の開始) に近いカイポケの行と組む (120 分以内・1 つのカイポケの行は 1 回だけ)。

新しい時刻
    らく助の QR 実績 (後から合わせた時刻を含む) が正。30 分未満の滞在と、退出の QR が無い
    訪問は、終了を到着＋35 分にする。

判定
    * ``match``  一致 (開始・終了ともカイポケと同じ)
    * ``apply``  反映する (印が付いても、反映してよい印だけ)
    * ``review`` 人が見る (反映しない印が 1 つでも付いた)
    * ``missing`` カイポケに無い (足す候補・RPA はまだ足せない)
    * ``kaipoke_only`` カイポケだけ

反映しない印 (人が見る)
    1 時間以上ずれる・同じ職員の別の訪問と時間が重なる・担当が違う・2 名訪問の人数が違う・
    QR の到着が無い・同じカイポケの行を 2 件が指す。
反映してよい印
    30 分未満→到着＋35 分・退出の QR が無い→到着＋35 分・カイポケの終了が開始より前・
    記録書Ⅱと 10 分以上ずれる・滞在が長い。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final

from app.services.kaipoke.name_match import normalize_name_key
from app.services.kaipoke.plan_actual_compare import NO_STAFF_TOKENS, norm_time

# --- 決まりの数 -------------------------------------------------------------------

#: 組にするときの到着とカイポケの開始の差の上限 (分)。
PAIR_WINDOW_MINUTES: Final = 120
#: この分より短い滞在は、終了を到着＋``SHORT_STAY_END_MINUTES`` にする。
SHORT_STAY_MINUTES: Final = 30
#: 30 分未満・退出の QR が無いときの終了 = 到着＋この分 (松岡さんの前からの決まり)。
SHORT_STAY_END_MINUTES: Final = 35
#: 開始か終了がこの分以上ずれたら、取り違えのおそれ (人が見る)。
BIG_GAP_MINUTES: Final = 60
#: 記録書Ⅱとこの分以上ずれたら印だけ付ける (反映はしてよい)。
K2_GAP_MINUTES: Final = 10
#: この分以上の滞在は印だけ付ける。
LONG_STAY_MINUTES: Final = 90

# --- 判定 -------------------------------------------------------------------------

STATUS_MATCH: Final = "match"
STATUS_APPLY: Final = "apply"
STATUS_REVIEW: Final = "review"
STATUS_MISSING: Final = "missing"
STATUS_KAIPOKE_ONLY: Final = "kaipoke_only"
STATUSES: Final = (STATUS_REVIEW, STATUS_APPLY, STATUS_MISSING, STATUS_KAIPOKE_ONLY, STATUS_MATCH)
STATUS_LABELS: Final = {
    STATUS_MATCH: "一致",
    STATUS_APPLY: "反映する",
    STATUS_REVIEW: "人が見る",
    STATUS_MISSING: "カイポケに無い",
    STATUS_KAIPOKE_ONLY: "カイポケだけ",
}
#: 「反映する／しない」を選べる判定 (カイポケの行と新しい時刻の両方がある)。
SELECTABLE_STATUSES: Final = (STATUS_APPLY, STATUS_REVIEW)

# 印のコード。review=True の印が 1 つでもあれば「人が見る」。
FLAG_BIG_GAP: Final = "big_gap"
FLAG_OVERLAP: Final = "overlap"
FLAG_STAFF_DIFF: Final = "staff_diff"
FLAG_STAFF_COUNT: Final = "staff_count"
FLAG_NO_ARRIVAL: Final = "no_arrival"
FLAG_DUP_TARGET: Final = "dup_target"
FLAG_SHORT_STAY: Final = "short_stay"
FLAG_NO_DEPARTURE: Final = "no_departure"
FLAG_KP_END_BEFORE_START: Final = "kp_end_before_start"
FLAG_K2_GAP: Final = "k2_gap"
FLAG_LONG_STAY: Final = "long_stay"
REVIEW_FLAGS: Final = frozenset(
    {
        FLAG_BIG_GAP,
        FLAG_OVERLAP,
        FLAG_STAFF_DIFF,
        FLAG_STAFF_COUNT,
        FLAG_NO_ARRIVAL,
        FLAG_DUP_TARGET,
    }
)

#: 名前の字体をそろえる追加の表 (``normalize_name_key`` に無いもの)。
_EXTRA_VARIANTS: Final = str.maketrans({"德": "徳", "冨": "富", "濱": "浜", "邉": "辺"})


def name_key(name: str | None) -> str:
    """利用者・職員の名前の照合キー (字体と空白をそろえる)。"""
    return normalize_name_key((name or "").translate(_EXTRA_VARIANTS))


def to_minutes(value: str | None) -> int | None:
    """``H:MM`` / ``HH:MM`` (秒があっても) → 分。読めなければ None。"""
    text = norm_time(value or "")
    if ":" not in text:
        return None
    hour, _, minute = text.partition(":")
    if not (hour.isdigit() and minute[:2].isdigit()):
        return None
    return int(hour) * 60 + int(minute[:2])


def fmt_minutes(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


# --- 入力 -------------------------------------------------------------------------


@dataclass(frozen=True)
class RkVisit:
    """らく助の訪問 1 件 (取消・削除・未訪問を除いたもの)。時刻は JST の ``HH:MM``。"""

    visit_id: str
    date: date
    patient: str
    patient_code: str = ""
    #: 担当 (主担当・2 人目・同行の指導者)。空は入れない。
    staff: tuple[str, ...] = ()
    plan_start: str | None = None
    plan_end: str | None = None
    arrival: str | None = None
    departure: str | None = None
    arrival_adjusted: bool = False
    departure_adjusted: bool = False


@dataclass(frozen=True)
class KpRow:
    """カイポケの実績 CSV の 1 行 (訪問)。"""

    date: date
    patient: str
    #: 職員名１〜３ (担当なしの ``-`` は入れない)。
    staff: tuple[str, ...]
    start: str
    end: str
    minutes: int | None = None
    k2_start: str | None = None
    k2_end: str | None = None
    service: str = ""

    @property
    def staff1(self) -> str:
        return self.staff[0] if self.staff else ""


def kp_staff_tuple(names: Iterable[str | None]) -> tuple[str, ...]:
    """CSV の職員名の列 → 空と担当なしを除いた tuple。"""
    return tuple(n.strip() for n in names if n and n.strip() not in NO_STAFF_TOKENS)


# --- 出力 -------------------------------------------------------------------------


@dataclass
class Flag:
    code: str
    label: str

    @property
    def review(self) -> bool:
        return self.code in REVIEW_FLAGS

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "label": self.label, "review": self.review}


@dataclass
class JudgedRow:
    """判定の 1 行 (らく助の訪問とカイポケの行の組。片方だけのこともある)。"""

    status: str
    date: date
    patient: str
    rk: RkVisit | None = None
    kp: KpRow | None = None
    flags: list[Flag] = field(default_factory=list)
    new_start: str | None = None
    new_end: str | None = None
    rk_stay: int | None = None

    @property
    def item_key(self) -> str | None:
        """RPA へ送る項目の鍵 = 日・利用者・カイポケの旧時刻・職員1。

        利用者名を必ず含める (11/1 の事故: 同じ日・同じ時刻のご姉妹の行を 2 件が指した)。
        """
        if self.kp is None:
            return None
        return (
            f"{self.kp.date.isoformat()}|{name_key(self.kp.patient)}|"
            f"{norm_time(self.kp.start)}-{norm_time(self.kp.end)}|{name_key(self.kp.staff1)}"
        )

    @property
    def default_selected(self) -> bool:
        return self.status == STATUS_APPLY

    @property
    def rk_adjusted(self) -> str:
        if self.rk is None:
            return ""
        parts = [
            label
            for label, on in (
                ("到着", self.rk.arrival_adjusted),
                ("退出", self.rk.departure_adjusted),
            )
            if on
        ]
        return "・".join(parts)

    def add(self, code: str, label: str) -> None:
        if all(f.code != code for f in self.flags):
            self.flags.append(Flag(code, label))


# --- 組み方 -----------------------------------------------------------------------


def pair_rows(
    rk_visits: Sequence[RkVisit], kp_rows: Sequence[KpRow]
) -> list[tuple[RkVisit | None, KpRow | None]]:
    """(日・利用者) ごとに、到着 (無ければ予定の開始) に近いカイポケの行と組む。"""
    rk_by: dict[tuple[date, str], list[RkVisit]] = defaultdict(list)
    kp_by: dict[tuple[date, str], list[KpRow]] = defaultdict(list)
    for v in rk_visits:
        rk_by[(v.date, name_key(v.patient))].append(v)
    for r in kp_rows:
        kp_by[(r.date, name_key(r.patient))].append(r)

    pairs: list[tuple[RkVisit | None, KpRow | None]] = []
    for key in sorted(set(rk_by) | set(kp_by)):
        visits = sorted(rk_by.get(key, []), key=lambda v: (v.plan_start or "", v.visit_id))
        rows = sorted(kp_by.get(key, []), key=lambda r: (to_minutes(r.start) or 0, r.end))
        used: set[int] = set()
        for v in visits:
            ref = to_minutes(v.arrival)
            if ref is None:
                ref = to_minutes(v.plan_start)
            best: tuple[int, int] | None = None
            if ref is not None:
                for i, r in enumerate(rows):
                    start = to_minutes(r.start)
                    if i in used or start is None:
                        continue
                    gap = abs(start - ref)
                    if best is None or gap < best[0]:
                        best = (gap, i)
            if best is not None and best[0] <= PAIR_WINDOW_MINUTES:
                used.add(best[1])
                pairs.append((v, rows[best[1]]))
            else:
                pairs.append((v, None))
        for i, r in enumerate(rows):
            if i not in used:
                pairs.append((None, r))
    return pairs


# --- 判定 -------------------------------------------------------------------------


def _new_times(row: JudgedRow, v: RkVisit) -> None:
    """新しい開始・終了を決め、30 分未満・退出なしの印を付ける。"""
    arr = to_minutes(v.arrival)
    dep = to_minutes(v.departure)
    if arr is None:
        row.add(FLAG_NO_ARRIVAL, "QR の到着が無い")
        return
    end = dep
    if dep is None:
        end = arr + SHORT_STAY_END_MINUTES
        row.add(FLAG_NO_DEPARTURE, f"QR の退出が無い → 到着＋{SHORT_STAY_END_MINUTES} 分")
    else:
        row.rk_stay = dep - arr
        if row.rk_stay < SHORT_STAY_MINUTES:
            end = arr + SHORT_STAY_END_MINUTES
            row.add(
                FLAG_SHORT_STAY,
                f"QR の滞在 {row.rk_stay} 分（{SHORT_STAY_MINUTES} 分未満）"
                f" → 到着＋{SHORT_STAY_END_MINUTES} 分",
            )
        elif row.rk_stay >= LONG_STAY_MINUTES:
            row.add(FLAG_LONG_STAY, f"QR の滞在 {row.rk_stay} 分（長い）")
    row.new_start = fmt_minutes(arr)
    row.new_end = fmt_minutes(end) if end is not None else None


def _compare_with_kaipoke(row: JudgedRow, v: RkVisit, r: KpRow) -> None:
    ns = to_minutes(row.new_start)
    ne = to_minutes(row.new_end)
    ks = to_minutes(r.start)
    ke = to_minutes(r.end)
    kp_backwards = ks is not None and ke is not None and ke < ks
    if kp_backwards:
        row.add(FLAG_KP_END_BEFORE_START, "カイポケの終了が開始より前")
    if ns is not None and ks is not None:
        big = abs(ns - ks) >= BIG_GAP_MINUTES
        # 終了が開始より前のカイポケの行は、終了の差で取り違えとみなさない (らく助が正)。
        if not kp_backwards and ne is not None and ke is not None:
            big = big or abs(ne - ke) >= BIG_GAP_MINUTES
        if big:
            row.add(FLAG_BIG_GAP, "1 時間以上ずれる（別の訪問の取り違えかも）")
    k2s = to_minutes(r.k2_start)
    k2e = to_minutes(r.k2_end)
    if ns is not None and k2s is not None:
        if abs(ns - k2s) >= K2_GAP_MINUTES or (
            ne is not None and k2e is not None and abs(ne - k2e) >= K2_GAP_MINUTES
        ):
            row.add(FLAG_K2_GAP, f"記録書Ⅱと {K2_GAP_MINUTES} 分以上ずれる")
    rk_staff = {name_key(s) for s in v.staff if s}
    kp_staff = {name_key(s) for s in r.staff if s}
    if rk_staff and kp_staff and not (rk_staff & kp_staff):
        row.add(FLAG_STAFF_DIFF, "担当が違う")
    elif rk_staff and kp_staff and len(rk_staff) != len(kp_staff):
        row.add(
            FLAG_STAFF_COUNT,
            f"2 名訪問の人数が違う（らく助 {len(rk_staff)} 名・カイポケ {len(kp_staff)} 名）",
        )


def _mark_overlaps(rows: Sequence[JudgedRow]) -> None:
    """同じ職員・同じ日に、新しい時刻どうしが重なる訪問に印を付ける。

    まったく同じ時刻の 2 件 (同じ建物のご姉妹をまとめて訪問) は重なりとみなさない。
    """
    spans: dict[tuple[date, str], list[tuple[int, int, int]]] = defaultdict(list)
    for idx, row in enumerate(rows):
        if row.rk is None:
            continue
        start = to_minutes(row.new_start)
        end = to_minutes(row.new_end)
        if start is None or end is None:
            continue
        for staff in {name_key(s) for s in row.rk.staff if s}:
            spans[(row.date, staff)].append((start, end, idx))
    hit: set[int] = set()
    for items in spans.values():
        items.sort()
        for i, (s1, e1, a) in enumerate(items):
            for s2, e2, b in items[i + 1 :]:
                if s2 >= e1:
                    break
                if (s1, e1) == (s2, e2):
                    continue
                hit.update((a, b))
    for idx in hit:
        rows[idx].add(FLAG_OVERLAP, "同じ職員の別の訪問と時間が重なる")


def _mark_duplicate_targets(rows: Sequence[JudgedRow]) -> None:
    """同じカイポケの行 (鍵) を 2 件以上が指していたら、全部を人が見るにする。"""
    keys = Counter(row.item_key for row in rows if row.item_key and row.rk is not None)
    for row in rows:
        if row.rk is not None and row.item_key and keys[row.item_key] > 1:
            row.add(FLAG_DUP_TARGET, "同じカイポケの行を 2 件が指す")


def judge(rk_visits: Sequence[RkVisit], kp_rows: Sequence[KpRow]) -> list[JudgedRow]:
    """突き合わせて判定する。並びは 人が見る → 反映する → カイポケに無い → カイポケだけ → 一致。"""
    rows: list[JudgedRow] = []
    for v, r in pair_rows(rk_visits, kp_rows):
        if v is None:
            assert r is not None
            rows.append(JudgedRow(status=STATUS_KAIPOKE_ONLY, date=r.date, patient=r.patient, kp=r))
            continue
        row = JudgedRow(status=STATUS_APPLY, date=v.date, patient=v.patient, rk=v, kp=r)
        _new_times(row, v)
        if r is not None:
            _compare_with_kaipoke(row, v, r)
        rows.append(row)

    _mark_overlaps(rows)
    _mark_duplicate_targets(rows)

    for row in rows:
        if row.rk is None:
            continue
        if row.kp is None:
            row.status = STATUS_MISSING
        elif (
            row.new_start is not None
            and row.new_start == norm_time(row.kp.start)
            and row.new_end == norm_time(row.kp.end)
        ):
            row.status = STATUS_MATCH
        elif row.new_start is None or row.new_end is None or any(f.review for f in row.flags):
            row.status = STATUS_REVIEW
        else:
            row.status = STATUS_APPLY

    order = {status: i for i, status in enumerate(STATUSES)}
    rows.sort(
        key=lambda x: (
            order[x.status],
            x.date,
            name_key(x.patient),
            x.new_start or (x.kp.start if x.kp else ""),
        )
    )
    return rows


def count_statuses(rows: Iterable[Any]) -> dict[str, int]:
    """判定ごとの件数 (``status`` を持つものなら何でも)。全部の判定のキーを出す。"""
    counts = {status: 0 for status in STATUSES}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    counts["total"] = sum(counts[s] for s in STATUSES)
    return counts


__all__ = [
    "FLAG_BIG_GAP",
    "FLAG_DUP_TARGET",
    "FLAG_K2_GAP",
    "FLAG_KP_END_BEFORE_START",
    "FLAG_LONG_STAY",
    "FLAG_NO_ARRIVAL",
    "FLAG_NO_DEPARTURE",
    "FLAG_OVERLAP",
    "FLAG_SHORT_STAY",
    "FLAG_STAFF_COUNT",
    "FLAG_STAFF_DIFF",
    "REVIEW_FLAGS",
    "SELECTABLE_STATUSES",
    "STATUSES",
    "STATUS_APPLY",
    "STATUS_KAIPOKE_ONLY",
    "STATUS_LABELS",
    "STATUS_MATCH",
    "STATUS_MISSING",
    "STATUS_REVIEW",
    "Flag",
    "JudgedRow",
    "KpRow",
    "RkVisit",
    "count_statuses",
    "fmt_minutes",
    "judge",
    "kp_staff_tuple",
    "name_key",
    "pair_rows",
    "to_minutes",
]
