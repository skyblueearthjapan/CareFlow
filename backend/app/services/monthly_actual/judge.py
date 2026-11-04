"""月の実績の組み方と判定 (純粋関数・DB を見ない).

正典: ``docs/tools/monthly-actual/review_oct.py`` (11/1 に 10 月分で使った道具) と、11/1 の
PO の決まり (``docs/plans/session-2026-11-01-HANDOFF.md`` §1)。

組み方
    (日・利用者) で組にする。利用者名は字体 (齋/齊・髙/高・﨑/崎・栁/柳・邉邊/辺・濵濱/浜・
    德/徳・冨/富 など) と空白をそろえる。同じ (日・利用者) のらく助の訪問とカイポケの行の
    全部の組を、到着 (無ければ予定の開始) とカイポケの開始の差の小さい順に割り当てる (差 0 を
    先に固定・差が同じなら担当 (職員1) が一致する組を先に・120 分以内・1 つのカイポケの行は
    1 回だけ)。近い順の早い者勝ちにすると、別の訪問の行を取って書き換えてしまう。らく助と
    カイポケの件数が違うか、組めなかった行が 1 つでもある (日・利用者) は、組めた行も全部「人が
    見る」にする (組めなかったらく助の訪問は「カイポケに無い」)。組んだ後に順番が入れ替わっている組がある (日・利用者) と、
    らく助の利用者コードが 2 種類以上 (空を含む) ある (日・利用者) も、組を全部「人が見る」にする。

新しい時刻
    らく助の QR 実績 (後から合わせた時刻を含む) が正。30 分未満の滞在と、退出の QR が無い
    訪問は、終了を到着＋35 分にする。

判定
    * ``match``  一致 (開始・終了ともカイポケと同じ)
    * ``apply``  反映する (印が付いても、反映してよい印だけ)
    * ``k2_only`` 記録書Ⅱだけ反映する (実績はもう QR どおりで、記録書Ⅱだけ違う)
    * ``review`` 人が見る (反映しない印が 1 つでも付いた)
    * ``missing`` カイポケに無い (足す候補・RPA はまだ足せない)
    * ``kaipoke_only`` カイポケだけ

反映しない印 (人が見る)
    1 時間以上ずれる・同じ職員の別の訪問と時間が重なる・担当が違う・2 名訪問の人数が違う・
    QR の到着が無い・同じカイポケの行を 2 件が指す・同じ日の訪問の数が違う・
    30 分未満 (退出なし) で到着＋35 分にするのにカイポケの滞在が 60 分以上ある・
    順番が入れ替わっている・同じ名前の別の方の可能性。
反映してよい印
    30 分未満→到着＋35 分・退出の QR が無い→到着＋35 分・カイポケの終了が開始より前・
    記録書Ⅱと 10 分以上ずれる・滞在が長い・記録書Ⅱと 60 分以上ずれる・記録書Ⅱの時刻の順が逆
    (後の 2 つは実績だけ反映し、記録書Ⅱは送らない)。

記録書Ⅱ (PO 決定 2026-11-04・``docs/plans/kaipoke-kiroku2-time-design-2026-11-04.md`` §2-2)
    カイポケの行に記録書Ⅱの時刻 (CSV の「記録Ⅱ開始時間」「記録Ⅱ終了時間」) があり、新しい時刻と
    違えば、実績と同じ流れで記録書Ⅱもそろえる (``JudgedRow.k2_apply``)。記録書Ⅱの開始が終了より
    前 (``to_minutes`` で比べる) の行だけ扱う。記録書Ⅱと新しい時刻が 60 分以上ずれる行と、記録書Ⅱの
    時刻の順が逆・読めない行は、記録書Ⅱを送らない (実績は今までどおり反映する・PO 決定 案B)。
    実績はもう QR どおりで記録書Ⅱだけがそうなっている行は「人が見る」(送らない)。
    記録書Ⅱが無い行は記録書Ⅱを扱わない。
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
#: 到着＋35 分にする行で、カイポケの滞在がこの分以上あれば人が見る (縮めすぎのおそれ)。
KP_LONGER_MINUTES: Final = 60

# --- 判定 -------------------------------------------------------------------------

STATUS_MATCH: Final = "match"
STATUS_APPLY: Final = "apply"
STATUS_K2_ONLY: Final = "k2_only"
STATUS_REVIEW: Final = "review"
STATUS_MISSING: Final = "missing"
STATUS_KAIPOKE_ONLY: Final = "kaipoke_only"
STATUSES: Final = (
    STATUS_REVIEW,
    STATUS_APPLY,
    STATUS_K2_ONLY,
    STATUS_MISSING,
    STATUS_KAIPOKE_ONLY,
    STATUS_MATCH,
)
STATUS_LABELS: Final = {
    STATUS_MATCH: "一致",
    STATUS_APPLY: "反映する",
    STATUS_K2_ONLY: "記録書Ⅱだけ",
    STATUS_REVIEW: "人が見る",
    STATUS_MISSING: "カイポケに無い",
    STATUS_KAIPOKE_ONLY: "カイポケだけ",
}
#: 「反映する／しない」を選べる判定 (カイポケの行と新しい時刻の両方がある)。
SELECTABLE_STATUSES: Final = (STATUS_APPLY, STATUS_K2_ONLY, STATUS_REVIEW)
#: 既定で「反映する」に選ぶ判定。
DEFAULT_SELECTED_STATUSES: Final = (STATUS_APPLY, STATUS_K2_ONLY)

# 印のコード。review=True の印が 1 つでもあれば「人が見る」。
FLAG_BIG_GAP: Final = "big_gap"
FLAG_OVERLAP: Final = "overlap"
FLAG_STAFF_DIFF: Final = "staff_diff"
FLAG_STAFF_COUNT: Final = "staff_count"
FLAG_NO_ARRIVAL: Final = "no_arrival"
FLAG_DUP_TARGET: Final = "dup_target"
FLAG_COUNT_DIFF: Final = "count_diff"
FLAG_KP_LONGER: Final = "kp_longer"
FLAG_ORDER_SWAP: Final = "order_swap"
FLAG_CODE_VARIANT: Final = "code_variant"
FLAG_SHORT_STAY: Final = "short_stay"
FLAG_NO_DEPARTURE: Final = "no_departure"
FLAG_KP_END_BEFORE_START: Final = "kp_end_before_start"
FLAG_K2_GAP: Final = "k2_gap"
FLAG_K2_BIG_GAP: Final = "k2_big_gap"
FLAG_K2_ORDER: Final = "k2_order"
FLAG_LONG_STAY: Final = "long_stay"
REVIEW_FLAGS: Final = frozenset(
    {
        FLAG_BIG_GAP,
        FLAG_OVERLAP,
        FLAG_STAFF_DIFF,
        FLAG_STAFF_COUNT,
        FLAG_NO_ARRIVAL,
        FLAG_DUP_TARGET,
        FLAG_COUNT_DIFF,
        FLAG_KP_LONGER,
        FLAG_ORDER_SWAP,
        FLAG_CODE_VARIANT,
    }
)
#: 記録書Ⅱを送らない印 (実績は反映してよい・記録書Ⅱは人が見る)。
K2_HOLD_FLAGS: Final = frozenset({FLAG_K2_BIG_GAP, FLAG_K2_ORDER})

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
    #: 記録書Ⅱも新しい時刻へそろえる (カイポケの行に記録書Ⅱがあり、新しい時刻と違う)。
    k2_apply: bool = False

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
        return self.status in DEFAULT_SELECTED_STATUSES

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

    def has(self, code: str) -> bool:
        return any(f.code == code for f in self.flags)

    def add(self, code: str, label: str) -> None:
        if all(f.code != code for f in self.flags):
            self.flags.append(Flag(code, label))


# --- 組み方 -----------------------------------------------------------------------


def _group_key(d: date, patient: str) -> tuple[date, str]:
    return (d, name_key(patient))


def pair_rows(
    rk_visits: Sequence[RkVisit], kp_rows: Sequence[KpRow]
) -> list[tuple[RkVisit | None, KpRow | None]]:
    """(日・利用者) ごとに、全部の組を差の小さい順に割り当てる (差 0 を先に固定)。

    差 = 到着 (無ければ予定の開始) とカイポケの開始の差。同じ差なら担当 (カイポケの職員1 が
    らく助の担当に居る) が一致する方、次に退出とカイポケの終了の差が小さい方。120 分を超える
    組は作らない。
    """
    rk_by: dict[tuple[date, str], list[RkVisit]] = defaultdict(list)
    kp_by: dict[tuple[date, str], list[KpRow]] = defaultdict(list)
    for v in rk_visits:
        rk_by[_group_key(v.date, v.patient)].append(v)
    for r in kp_rows:
        kp_by[_group_key(r.date, r.patient)].append(r)

    pairs: list[tuple[RkVisit | None, KpRow | None]] = []
    for key in sorted(set(rk_by) | set(kp_by)):
        visits = sorted(rk_by.get(key, []), key=lambda v: (v.plan_start or "", v.visit_id))
        rows = sorted(kp_by.get(key, []), key=lambda r: (to_minutes(r.start) or 0, r.end))
        candidates: list[tuple[int, int, int, int]] = []
        for vi, v in enumerate(visits):
            ref = to_minutes(v.arrival)
            if ref is None:
                ref = to_minutes(v.plan_start)
            if ref is None:
                continue
            dep = to_minutes(v.departure)
            rk_staff = {name_key(s) for s in v.staff if s}
            for ri, r in enumerate(rows):
                start = to_minutes(r.start)
                if start is None:
                    continue
                gap = abs(start - ref)
                if gap > PAIR_WINDOW_MINUTES:
                    continue
                end = to_minutes(r.end)
                end_gap = abs(end - dep) if end is not None and dep is not None else 10**6
                staff_miss = 0 if r.staff1 and name_key(r.staff1) in rk_staff else 1
                candidates.append((gap, staff_miss, end_gap, vi, ri))
        matched: dict[int, int] = {}
        used: set[int] = set()
        for _gap, _staff_miss, _end_gap, vi, ri in sorted(candidates):
            if vi in matched or ri in used:
                continue
            matched[vi] = ri
            used.add(ri)
        for vi, v in enumerate(visits):
            pairs.append((v, rows[matched[vi]] if vi in matched else None))
        for ri, r in enumerate(rows):
            if ri not in used:
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
    k2_ok = k2s is not None and k2e is not None and k2s < k2e
    if (r.k2_start or r.k2_end) and not k2_ok:
        # RPA は開始 < 終了でない旧の時刻を受け付けない (400)。
        row.add(FLAG_K2_ORDER, "記録書Ⅱの時刻の順が逆・人が見る")
    if k2_ok and ns is not None and ne is not None:
        assert k2s is not None and k2e is not None
        k2_gap = max(abs(ns - k2s), abs(ne - k2e))
        if k2_gap >= BIG_GAP_MINUTES:
            row.add(FLAG_K2_BIG_GAP, f"記録書Ⅱと {BIG_GAP_MINUTES} 分以上ずれる・記録書Ⅱは人が見る")
        elif k2_gap >= K2_GAP_MINUTES:
            row.add(FLAG_K2_GAP, f"記録書Ⅱと {K2_GAP_MINUTES} 分以上ずれる")
    # 記録書Ⅱは開始 < 終了で読めるときだけ扱う (RPA は旧の時刻で照らすため)。
    # 60 分以上ずれる行・順が逆の行は記録書Ⅱを送らない (実績は反映してよい)。
    row.k2_apply = bool(
        row.new_start
        and row.new_end
        and k2_ok
        and not any(row.has(code) for code in K2_HOLD_FLAGS)
        and (norm_time(r.k2_start or ""), norm_time(r.k2_end or "")) != (row.new_start, row.new_end)
    )
    if row.has(FLAG_SHORT_STAY) or row.has(FLAG_NO_DEPARTURE):
        kp_stay = ke - ks if ks is not None and ke is not None and ke >= ks else r.minutes
        if kp_stay is not None and kp_stay >= KP_LONGER_MINUTES:
            row.add(FLAG_KP_LONGER, f"カイポケの方が長い（カイポケの滞在 {kp_stay} 分）")
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
    """同じ鍵の行が 2 つ以上あれば (カイポケだけの行も数える)、らく助の側の行を人が見るにする。

    鍵が同じだと RPA はどちらの行を書き換えるか決められない。
    """
    keys = Counter(row.item_key for row in rows if row.item_key)
    for row in rows:
        if row.rk is not None and row.item_key and keys[row.item_key] > 1:
            row.add(FLAG_DUP_TARGET, "同じカイポケの行（鍵）が 2 つある")


def _mark_count_diffs(
    rows: Sequence[JudgedRow], rk_visits: Sequence[RkVisit], kp_rows: Sequence[KpRow]
) -> None:
    """同じ (日・利用者) で件数が違うか、組めなかった行が 1 つでもあれば、組めた行を全部人が見るにする。

    件数が同じでも、らく助 09:00・11:00、カイポケ 10:30・13:30 のように片側ずつ組めない日は、
    組めた 11:00→10:30 も取り違えのおそれがある。
    """
    rk_count = Counter(_group_key(v.date, v.patient) for v in rk_visits)
    kp_count = Counter(_group_key(r.date, r.patient) for r in kp_rows)
    unpaired = {
        _group_key(row.date, row.patient) for row in rows if row.rk is None or row.kp is None
    }
    for row in rows:
        if row.rk is None or row.kp is None:
            continue
        key = _group_key(row.rk.date, row.rk.patient)
        if rk_count[key] != kp_count[key]:
            row.add(
                FLAG_COUNT_DIFF,
                f"同じ日の訪問の数が違う（らく助 {rk_count[key]} 件・カイポケ {kp_count[key]} 件）",
            )
        elif key in unpaired:
            row.add(FLAG_COUNT_DIFF, "同じ日に組めなかった訪問がある")


def _rk_ref(v: RkVisit) -> int | None:
    """組むときの基準の時刻 = 到着 (無ければ予定の開始)。"""
    ref = to_minutes(v.arrival)
    return ref if ref is not None else to_minutes(v.plan_start)


def _mark_order_swaps(rows: Sequence[JudgedRow]) -> None:
    """同じ (日・利用者) で、らく助の順とカイポケの開始の順が逆の組が 1 つでもあれば、組を全部人が見るにする。

    例: らく助 09:30・10:00、カイポケ 09:45・10:20 が 09:30→10:20・10:00→09:45 と組まれると、
    両方「反映する」になって行を取り違えて書き換えてしまう。
    """
    groups: dict[tuple[date, str], list[JudgedRow]] = defaultdict(list)
    for row in rows:
        if row.rk is not None and row.kp is not None:
            groups[_group_key(row.rk.date, row.rk.patient)].append(row)
    for group in groups.values():
        keys: list[tuple[int, int]] = []
        for row in group:
            assert row.rk is not None and row.kp is not None
            ref = _rk_ref(row.rk)
            start = to_minutes(row.kp.start)
            if ref is not None and start is not None:
                keys.append((ref, start))
        swapped = any(
            (r1 - r2) * (k1 - k2) < 0 for i, (r1, k1) in enumerate(keys) for r2, k2 in keys[i + 1 :]
        )
        if swapped:
            for row in group:
                row.add(FLAG_ORDER_SWAP, "順番が入れ替わっている")


def _mark_code_variants(rows: Sequence[JudgedRow]) -> None:
    """同じ (日・利用者) にらく助の利用者コードが 2 種類以上 (空を含む) あれば、組を全部人が見るにする。

    字体をそろえたせいで、同じ名前の別の方 (髙橋様と高橋様など) を 1 つにまとめたおそれがある。
    """
    groups: dict[tuple[date, str], list[JudgedRow]] = defaultdict(list)
    for row in rows:
        if row.rk is not None:
            groups[_group_key(row.rk.date, row.rk.patient)].append(row)
    for group in groups.values():
        # コードが空の訪問が混ざるときも、別の方かどうか決められないので人が見る。
        patient_codes = {row.rk.patient_code for row in group if row.rk}
        if len(patient_codes) >= 2:
            for row in group:
                row.add(FLAG_CODE_VARIANT, "同じ名前の別の方の可能性")


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
    _mark_count_diffs(rows, rk_visits, kp_rows)
    _mark_order_swaps(rows)
    _mark_code_variants(rows)

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
            # 実績は QR どおり。記録書Ⅱだけ違えば「記録書Ⅱだけ」(反映しない印があれば人が見る)。
            # 記録書Ⅱが 60 分以上ずれる・順が逆なら、送るものが無いので人が見る (送らない)。
            if any(row.has(code) for code in K2_HOLD_FLAGS):
                row.status = STATUS_REVIEW
            elif not row.k2_apply:
                row.status = STATUS_MATCH
            elif any(f.review for f in row.flags):
                row.status = STATUS_REVIEW
            else:
                row.status = STATUS_K2_ONLY
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


def count_k2(rows: Iterable[JudgedRow]) -> int:
    """記録書Ⅱもそろえる行の数 (実績と続けて・記録書Ⅱだけ の両方)。"""
    return sum(1 for row in rows if row.k2_apply)


__all__ = [
    "FLAG_BIG_GAP",
    "FLAG_CODE_VARIANT",
    "FLAG_COUNT_DIFF",
    "FLAG_DUP_TARGET",
    "FLAG_K2_BIG_GAP",
    "FLAG_K2_GAP",
    "FLAG_K2_ORDER",
    "FLAG_KP_END_BEFORE_START",
    "FLAG_KP_LONGER",
    "FLAG_LONG_STAY",
    "FLAG_NO_ARRIVAL",
    "FLAG_NO_DEPARTURE",
    "FLAG_ORDER_SWAP",
    "FLAG_OVERLAP",
    "FLAG_SHORT_STAY",
    "K2_HOLD_FLAGS",
    "FLAG_STAFF_COUNT",
    "FLAG_STAFF_DIFF",
    "REVIEW_FLAGS",
    "SELECTABLE_STATUSES",
    "STATUSES",
    "DEFAULT_SELECTED_STATUSES",
    "STATUS_APPLY",
    "STATUS_K2_ONLY",
    "STATUS_KAIPOKE_ONLY",
    "STATUS_LABELS",
    "STATUS_MATCH",
    "STATUS_MISSING",
    "STATUS_REVIEW",
    "Flag",
    "JudgedRow",
    "KpRow",
    "RkVisit",
    "count_k2",
    "count_statuses",
    "fmt_minutes",
    "judge",
    "kp_staff_tuple",
    "name_key",
    "pair_rows",
    "to_minutes",
]
