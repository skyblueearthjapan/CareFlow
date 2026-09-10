"""カイポケ 予定 × 実績 の月次突合 (read-only) + A4 印刷レポート.

PO 要望 (2026-09-10): 請求前に、カイポケの **予定** と **実績** のズレを一覧で潰したい。
掃除する対象は原則カイポケの実績側なので、レポートは「どの行を、なぜ、どう直すか」を
1 行ずつ言い切る形にする。

データ源は ``kaipoke_csv_snapshots`` の 予定 (``division='plan'``) と
実績 (``division='actual'``) の最新スナップショット 2 本だけ。RPA は回さず、
DB も書かない (取得は ``plan_actual_fetch`` の責務)。

## 判定の作り方

突合キーは ``(日, 利用者)``。同キー内で 予定行 ↔ 実績行 を **6 段階の貪欲マッチ**
で組む。段階を分けるのは、同一キーに複数訪問がある日に「担当も時刻も合う組」を
先に確定させないと、無関係な行同士が「相違」に化けるため。

1. {主担当, 同行者} の集合一致 かつ 時刻一致 → 一致
2. 主担当 (職員名1) 一致 かつ 時刻一致 → 一致
3. 集合一致 → 時刻ズレ
4. 主担当一致 → 時刻ズレ
5. 時刻一致 → 担当違い
6. 残り物同士 → 相違 (時刻・担当の両方が違う)

担当を **順不同の集合** で先に見るのは、カイポケ側で職員名1/2 が入れ替わっただけの
行を「担当違い」に化けさせないため (誰が 1 番かは CSV 生成の内部都合)。

突合するのは **訪問の行だけ** (業務種別が 医療保険 / 介護保険)。イベント行
(個別業務・朝会など) は両側で落とし、その数を ``counts.events_skipped`` に出す。
イベントは予定側にしか出ない性質があり、混ぜると「予定のみ」が偽陽性で埋まる。
列数が足りず読めなかった行は ``counts.malformed_rows`` に数える (黙って捨てない)。

余った予定行 = 予定のみ / 余った実績行 = 実績のみ（予定外）。ペアには次のタグを重ねる:

* ``サービス違い`` — サービス内容が食い違う
* ``同行違い`` — 主担当は合っているのに同行者 (職員名2) が食い違う
* ``重複（予定側/実績側）`` — 同キー内に **担当も時刻も同じ** 行が 2 本以上
  (担当だけをキーにすると「同じ人が午前と午後に 2 回訪問」まで重複に化ける)

**タグは判定カテゴリではない (内数)** — 一致/時刻ズレ/…の 6 カテゴリだけで
全行がちょうど 1 回数えられる、という読み方を壊さないため。
"""

from __future__ import annotations

import csv
import html
import io
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from app.services.kaipoke.name_match import normalize_name_key
from app.services.kaipoke.report_css import REPORT_CSS

WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]

# --- 判定カテゴリ (この 6 つで全行がちょうど 1 回数えられる) -------------------
CAT_MATCH = "一致"
CAT_TIME = "時刻ズレ"
CAT_STAFF = "担当違い"
CAT_BOTH = "相違"
CAT_PLAN_ONLY = "予定のみ"
CAT_ACTUAL_ONLY = "実績のみ"

CATEGORIES: tuple[str, ...] = (
    CAT_MATCH,
    CAT_TIME,
    CAT_STAFF,
    CAT_BOTH,
    CAT_PLAN_ONLY,
    CAT_ACTUAL_ONLY,
)

#: 判定セルに出す表示名 (集計キーは短いまま保つ)。
CATEGORY_LABELS: dict[str, str] = {
    CAT_MATCH: "一致",
    CAT_TIME: "時刻ズレ",
    CAT_STAFF: "担当違い",
    CAT_BOTH: "相違（時刻・担当）",
    CAT_PLAN_ONLY: "予定のみ",
    CAT_ACTUAL_ONLY: "実績のみ（予定外）",
}

# --- タグ (判定に重ねる内数) --------------------------------------------------
TAG_DUP_PLAN = "重複（予定側）"
TAG_DUP_ACTUAL = "重複（実績側）"
TAG_SERVICE = "サービス違い"
TAG_ACCOMPANY = "同行違い"

#: 集計キー「重複」に数えるタグ。
_DUP_TAGS = (TAG_DUP_PLAN, TAG_DUP_ACTUAL)

_ADVICE: dict[str, str] = {
    CAT_MATCH: "",
    CAT_TIME: "実績の時刻が正。予定を合わせるか確認",
    CAT_STAFF: "担当を確認",
    CAT_BOTH: "時刻・担当ともに相違。実績が正か確認",
    CAT_PLAN_ONLY: "実績が未登録。訪問したなら実績を登録、未実施なら予定を取消",
    CAT_ACTUAL_ONLY: "予定外の実績。誤登録なら実績側を削除",
}
_ADVICE_DUP_ACTUAL = "実績側の重複。記録Ⅱの無い方を削除"
_ADVICE_DUP_PLAN = "予定側の重複。不要な方の予定を取消"
_ADVICE_SERVICE = "サービス内容も相違。請求区分を確認"
_ADVICE_ACCOMPANY = "同行者（職員2）が一致しません。実績側を確認"

#: 担当が空欄のときの表示 (カイポケの「-」行 = 担当なし)。
NO_STAFF = "（担当なし）"

#: 実績CSVの既知の制約 (PO へ必ず伝える)。
CAVEAT = (
    "未確定（未）の実績はカイポケの CSV 出力に含まれないため本レポートに出ません。"
    "未確定行は月間スケジュール管理の実績側で直接確認してください"
)

#: 同行者の扱いの限界 (CAVEAT と並べて出す)。
CAVEAT_ACCOMPANY = "同行者（職員名2）は一致／同行違いの判定にのみ使い、請求区分の正誤は判定しません"

#: 予定と実績の行数がこの比率以上ずれていたら、CSV の取得範囲を疑う警告を出す。
_ROW_COUNT_DIVERGENCE = 0.30

# --- CSV パース ---------------------------------------------------------------

#: ヘッダ名で列を引くときの対応表 (カイポケ18列CSV)。
_HEADER_COLUMNS: dict[str, str] = {
    "staff1": "職員名１",
    "staff2": "職員名２",
    "day": "日付",
    "patient": "利用者",
    "business": "業務種別",
    "service": "サービス内容",
    "start": "開始時間",
    "end": "終了時間",
}

#: ヘッダ名で引けなかったときの列位置 (diff/engine._parse_kaipoke_rows と同じ)。
_INDEX_COLUMNS: dict[str, int] = {
    "staff1": 0,
    "staff2": 2,
    "day": 9,
    "patient": 11,
    "business": 12,
    "service": 13,
    "start": 14,
    "end": 15,
}

#: 突合対象の業務種別 = 訪問だけ (reconcile_report_html と同じ規則)。
#: イベント行 (個別業務・朝会など) は業務種別にイベント名が入るため、これで落ちる。
#: 予定側にしか出ない性質があり、混ぜると「予定のみ」が大量の偽陽性になる。
VISIT_BUSINESS_TYPES: tuple[str, ...] = ("医療保険", "介護保険")


def _norm_name(s: str) -> str:
    """表示用の軽い正規化 (全角スペース→半角)。照合キーは normalize_name_key を使う。"""
    return (s or "").replace("　", " ").strip()


def _norm_time(s: str) -> str:
    """時刻を ``HH:MM`` に揃える (実績CSVは前ゼロが落ちる・2026-09-01 実測)。

    ``9:50`` → ``09:50`` / ``9:5`` → ``09:05`` / ``9:50:00`` → ``09:50``。
    先に 5 文字で切ると ``9:50:00`` が ``9:50:`` になって分が壊れるので、
    切らずに ``:`` で割ってから前ゼロを詰める。コロンが無い値 (空欄や
    ``未定`` のような自由入力) は判断材料が無いのでそのまま返す。
    """
    t = (s or "").strip()
    if ":" not in t:
        return t
    h, _, m = t.partition(":")
    return f"{h.zfill(2)}:{m[:2].zfill(2)}"


#: 「担当なし」を表すカイポケ側の表記。CSV では空欄のほか ``-`` / ``－`` が来る
#: (担当なし行・2026-09-03 の送信事故で実在を確認)。照合でも集計でも 1 つに寄せる。
_NO_STAFF_TOKENS: frozenset[str] = frozenset({"", "-", "－", "ー", "―"})

#: 担当なしの照合キー (実在の氏名と衝突しない値)。
_NO_STAFF_KEY = "\x00none"


def _staff_key(name: str) -> str:
    """担当名 → 照合キー。担当なし表記はすべて同じキーに寄せる。"""
    if (name or "").strip() in _NO_STAFF_TOKENS:
        return _NO_STAFF_KEY
    return normalize_name_key(name)


def _staff_label(name: str) -> str:
    """担当名 → 表示・集計用の名前。担当なし表記はすべて ``NO_STAFF``。"""
    cleaned = (name or "").strip()
    return NO_STAFF if cleaned in _NO_STAFF_TOKENS else cleaned


def _service_matches(a: str, b: str) -> bool:
    """サービス内容の一致判定 — diff エンジンと同じ「完全一致 or 双方向の前方一致」。"""
    if a == b:
        return True
    if not a or not b:
        return False
    return a.startswith(b) or b.startswith(a)


@dataclass
class PARow:
    """CSV 1 行 (予定側 or 実績側)。"""

    day: int
    patient: str
    staff1: str
    staff2: str
    start: str
    end: str
    service: str
    business: str = ""

    @property
    def is_visit(self) -> bool:
        """訪問の行か (= 突合対象か)。イベント行は False。"""
        return self.business in VISIT_BUSINESS_TYPES

    @property
    def staff_key(self) -> str:
        """主担当の照合キー。担当なし (空欄 / ``-`` / ``－``) は 1 つの値に寄せる。"""
        return _staff_key(self.staff1)

    @property
    def staff_label(self) -> str:
        """表示・集計用の担当名。担当なしは ``NO_STAFF``。"""
        return _staff_label(self.staff1)

    @property
    def staff2_key(self) -> str:
        """同行者の照合キー (居なければ空文字)。"""
        key = _staff_key(self.staff2)
        return "" if key == _NO_STAFF_KEY else key

    @property
    def staff_set(self) -> frozenset[str]:
        """{主担当, 同行者} の順不同集合 (空は含めない)。

        カイポケ側で職員名1/2 が入れ替わっただけの行を「担当違い」にしないための
        キー。誰が 1 番かは csv_builder の配分順序で決まる内部都合であって、
        現場の事実 (この 2 人で行った) は順序に依らない。
        """
        return frozenset(k for k in (self.staff_key, self.staff2_key) if k)

    @property
    def time_key(self) -> tuple[str, str]:
        return (self.start, self.end)


def parse_rows(csv_text: str) -> list[PARow]:
    """カイポケ18列CSV → ``PARow`` リスト (壊れ行の数は捨てる)。"""
    return parse_rows_with_stats(csv_text)[0]


def parse_rows_with_stats(csv_text: str) -> tuple[list[PARow], int]:
    """カイポケ18列CSV → ``(PARow リスト, 列数不足で読めなかった行数)``。

    ヘッダ名で引き、駄目なら列位置で引く。**列数が足りず読めなかった行は
    黙って捨てない** — 数えて呼び出し側に返し、レポートに出す。取りこぼしが
    「予定のみ 0 件」のような静かな安心に化けるのを防ぐため。
    日付欄が数字でない行 (ヘッダ・区切りの空行) は壊れ行ではないので数えない。
    """
    rows = [r for r in csv.reader(io.StringIO(csv_text or "")) if any(r)]
    if not rows:
        return [], 0

    header = {name.strip().lstrip("﻿"): k for k, name in enumerate(rows[0])}
    if all(name in header for name in _HEADER_COLUMNS.values()):
        cols = {key: header[name] for key, name in _HEADER_COLUMNS.items()}
        body = rows[1:]
    else:
        # ヘッダ名で引けない CSV (列名が変わった/ヘッダ無し) は列位置で読む。
        # 日付列が数字でない行 (ヘッダ含む) は下のループで自動的に落ちる。
        cols = dict(_INDEX_COLUMNS)
        body = rows

    highest = max(cols.values())
    out: list[PARow] = []
    malformed = 0
    for r in body:
        if len(r) <= highest:
            malformed += 1
            continue
        day = r[cols["day"]].strip()
        if not day.isdigit():
            continue
        out.append(
            PARow(
                day=int(day),
                patient=_norm_name(r[cols["patient"]]),
                staff1=_norm_name(r[cols["staff1"]]),
                staff2=_norm_name(r[cols["staff2"]]),
                start=_norm_time(r[cols["start"]]),
                end=_norm_time(r[cols["end"]]),
                service=(r[cols["service"]] or "").strip(),
                business=(r[cols["business"]] or "").strip(),
            )
        )
    return out, malformed


# --- 突合 ---------------------------------------------------------------------


@dataclass
class PlanActualEntry:
    """レポート 1 行 = 予定行と実績行のペア (片側だけのこともある)。"""

    day: int
    patient: str
    category: str
    tags: list[str] = field(default_factory=list)
    advice: str = ""
    staff: str = ""
    plan: PARow | None = None
    actual: PARow | None = None

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)

    @property
    def is_duplicate(self) -> bool:
        return any(t in _DUP_TAGS for t in self.tags)


@dataclass
class PlanActualDay:
    day: int
    date: date | None
    entries: list[PlanActualEntry] = field(default_factory=list)


@dataclass
class StaffSummary:
    """担当者別の内訳 (実績側の担当を優先。実績が無い行は予定側の担当で数える)。"""

    staff: str
    counts: dict[str, int] = field(default_factory=dict)
    duplicates: int = 0
    total: int = 0


@dataclass
class PlanActualReport:
    month: str
    office_name: str
    generated_at: datetime
    plan_fetched_at: datetime | None
    actual_fetched_at: datetime | None
    plan_rows: int
    actual_rows: int
    #: 業務種別が訪問でない行 (イベント/個別業務) を両側で落とした数。
    events_skipped: int
    #: 列数が足りず読めなかった行の数 (両側合計)。0 でないなら CSV を疑う。
    malformed_rows: int
    counts: dict[str, int]
    by_staff: list[StaffSummary]
    days: list[PlanActualDay]

    @property
    def row_counts_diverge(self) -> bool:
        """予定と実績の行数が大きく食い違うか (取得範囲を疑う材料)。

        週限定CSVを月次レポートに使ってしまった等の事故は、判定の内訳ではなく
        **行数の桁違い** に最初に現れる。片側 0 件も「大きく違う」に含める。
        """
        bigger = max(self.plan_rows, self.actual_rows)
        if bigger == 0:
            return False
        return abs(self.plan_rows - self.actual_rows) / bigger > _ROW_COUNT_DIVERGENCE


def _advice_for(category: str, tags: list[str]) -> str:
    """推奨対処。重複タグは判定より強い (まず重複を潰さないと他の判定が読めない)。"""
    if TAG_DUP_ACTUAL in tags:
        return _ADVICE_DUP_ACTUAL
    if TAG_DUP_PLAN in tags:
        return _ADVICE_DUP_PLAN
    parts = [p for p in (_ADVICE.get(category, ""),) if p]
    if TAG_ACCOMPANY in tags:
        parts.append(_ADVICE_ACCOMPANY)
    if TAG_SERVICE in tags:
        parts.append(_ADVICE_SERVICE)
    return "／".join(parts)


def _duplicate_indices(rows: list[PARow]) -> set[int]:
    """同じ担当 **かつ同じ時刻** の行が 2 本以上 → その全部を重複として返す。

    担当だけをキーにすると「同じ人が同じ利用者を午前と午後に 2 回訪問した」
    という正当な予定まで重複に化ける (1 日 2 回訪問は実在する運用)。
    重複として潰してよいのは、担当も時刻も同じ = 二重登録しかありえない行だけ。
    """
    by_key: dict[tuple[str, tuple[str, str]], list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_key[(r.staff_key, r.time_key)].append(i)
    dup: set[int] = set()
    for idxs in by_key.values():
        if len(idxs) >= 2:
            dup.update(idxs)
    return dup


def _pair_group(
    day: int, patient: str, plans: list[PARow], actuals: list[PARow]
) -> list[PlanActualEntry]:
    """同キー (日・利用者) の 予定行 × 実績行 を 4 段階の貪欲マッチで組む。"""
    plans = sorted(plans, key=lambda r: (r.start, r.staff1))
    actuals = sorted(actuals, key=lambda r: (r.start, r.staff1))
    dup_plan = _duplicate_indices(plans)
    dup_actual = _duplicate_indices(actuals)

    used_p: set[int] = set()
    used_a: set[int] = set()
    matched: list[tuple[int, int, str]] = []

    def _sweep(pred, category: str) -> None:
        for pi, p in enumerate(plans):
            if pi in used_p:
                continue
            for ai, a in enumerate(actuals):
                if ai in used_a or not pred(p, a):
                    continue
                used_p.add(pi)
                used_a.add(ai)
                matched.append((pi, ai, category))
                break

    # 段階の順序が判定の質を決める。{主担当, 同行者} の**順不同集合**を先に見るのは、
    # カイポケ側で職員名1/2 が入れ替わっただけの行を「担当違い」に化けさせないため
    # (誰が 1 番かは CSV 生成の内部都合で、現場の事実ではない)。
    _sweep(lambda p, a: p.staff_set == a.staff_set and p.time_key == a.time_key, CAT_MATCH)
    _sweep(lambda p, a: p.staff_key == a.staff_key and p.time_key == a.time_key, CAT_MATCH)
    _sweep(lambda p, a: p.staff_set == a.staff_set, CAT_TIME)
    _sweep(lambda p, a: p.staff_key == a.staff_key, CAT_TIME)
    _sweep(lambda p, a: p.time_key == a.time_key, CAT_STAFF)
    _sweep(lambda p, a: True, CAT_BOTH)

    entries: list[PlanActualEntry] = []

    def _add(category: str, pi: int | None, ai: int | None) -> None:
        p = plans[pi] if pi is not None else None
        a = actuals[ai] if ai is not None else None
        tags: list[str] = []
        if pi is not None and pi in dup_plan:
            tags.append(TAG_DUP_PLAN)
        if ai is not None and ai in dup_actual:
            tags.append(TAG_DUP_ACTUAL)
        if p is not None and a is not None:
            # 同行違いは「主担当は合っているのに同行者だけ食い違う」ときだけ。
            # 担当違いの行に付けても情報が増えず、判定を読みにくくするだけ。
            if p.staff_key == a.staff_key and p.staff2_key != a.staff2_key:
                tags.append(TAG_ACCOMPANY)
            if not _service_matches(p.service, a.service):
                tags.append(TAG_SERVICE)
        anchor = a or p
        entries.append(
            PlanActualEntry(
                day=day,
                patient=patient,
                category=category,
                tags=tags,
                advice=_advice_for(category, tags),
                # 集計は主担当 (職員名1) のバケツに入れる — 同行者まで数えると
                # 「1 訪問が 2 件」に見えて担当者別の件数が実態とずれる。
                staff=(anchor.staff_label if anchor else NO_STAFF),
                plan=p,
                actual=a,
            )
        )

    for pi, ai, category in matched:
        _add(category, pi, ai)
    for pi in range(len(plans)):
        if pi not in used_p:
            _add(CAT_PLAN_ONLY, pi, None)
    for ai in range(len(actuals)):
        if ai not in used_a:
            _add(CAT_ACTUAL_ONLY, None, ai)

    entries.sort(
        key=lambda e: ((e.plan or e.actual).start if (e.plan or e.actual) else "", e.staff)
    )
    return entries


def build_plan_actual_report(
    *,
    month: str,
    plan_csv_text: str,
    actual_csv_text: str,
    plan_fetched_at: datetime | None = None,
    actual_fetched_at: datetime | None = None,
    office_name: str = "",
) -> PlanActualReport:
    """予定CSV × 実績CSV → 突合レポート (純関数・DB も RPA も触らない)。"""
    plan_all, plan_malformed = parse_rows_with_stats(plan_csv_text)
    actual_all, actual_malformed = parse_rows_with_stats(actual_csv_text)
    malformed_rows = plan_malformed + actual_malformed
    # イベント行 (個別業務・朝会など) は突合対象外 — 訪問だけを見る
    # (reconcile_report_html と同じ規則)。除外数は内数として counts に出す。
    plan_rows = [r for r in plan_all if r.is_visit]
    actual_rows = [r for r in actual_all if r.is_visit]
    events_skipped = (len(plan_all) - len(plan_rows)) + (len(actual_all) - len(actual_rows))

    try:
        year, mon = int(month[:4]), int(month[5:7])
    except ValueError:  # pragma: no cover — API 側で ^\d{4}-\d{2}$ を強制済み
        year = mon = 0

    plans_by_key: dict[tuple[int, str], list[PARow]] = defaultdict(list)
    actuals_by_key: dict[tuple[int, str], list[PARow]] = defaultdict(list)
    display: dict[tuple[int, str], str] = {}
    for r in plan_rows:
        key = (r.day, normalize_name_key(r.patient))
        display.setdefault(key, r.patient)
        plans_by_key[key].append(r)
    for r in actual_rows:
        key = (r.day, normalize_name_key(r.patient))
        display.setdefault(key, r.patient)
        actuals_by_key[key].append(r)

    entries_by_day: dict[int, list[PlanActualEntry]] = defaultdict(list)
    for key in sorted(set(plans_by_key) | set(actuals_by_key)):
        day, norm_patient = key
        entries_by_day[day].extend(
            _pair_group(
                day,
                display.get(key, norm_patient),
                plans_by_key.get(key, []),
                actuals_by_key.get(key, []),
            )
        )

    days: list[PlanActualDay] = []
    for day in sorted(entries_by_day):
        try:
            d = date(year, mon, day)
        except ValueError:
            d = None
        entries = sorted(
            entries_by_day[day],
            key=lambda e: ((e.plan or e.actual).start if (e.plan or e.actual) else "", e.patient),
        )
        days.append(PlanActualDay(day=day, date=d, entries=entries))

    counts: dict[str, int] = dict.fromkeys(CATEGORIES, 0)
    counts["重複"] = 0
    staff_acc: dict[str, StaffSummary] = {}
    for pday in days:
        for e in pday.entries:
            counts[e.category] += 1
            if e.is_duplicate:
                counts["重複"] += 1
            summary = staff_acc.get(e.staff)
            if summary is None:
                summary = StaffSummary(staff=e.staff, counts=dict.fromkeys(CATEGORIES, 0))
                staff_acc[e.staff] = summary
            summary.counts[e.category] += 1
            summary.total += 1
            if e.is_duplicate:
                summary.duplicates += 1
    # plan_rows / actual_rows は **突合した訪問行の数** (イベント除外後)。
    # events_skipped はその外側で落とした行数 = 内数ではなく「対象外」の内訳。
    counts["plan_rows"] = len(plan_rows)
    counts["actual_rows"] = len(actual_rows)
    counts["events_skipped"] = events_skipped
    counts["malformed_rows"] = malformed_rows

    return PlanActualReport(
        month=month,
        office_name=office_name,
        generated_at=datetime.now(UTC),
        plan_fetched_at=plan_fetched_at,
        actual_fetched_at=actual_fetched_at,
        plan_rows=len(plan_rows),
        actual_rows=len(actual_rows),
        events_skipped=events_skipped,
        malformed_rows=malformed_rows,
        counts=counts,
        by_staff=sorted(staff_acc.values(), key=lambda s: s.staff),
        days=days,
    )


# --- HTML ---------------------------------------------------------------------


def _esc(s: str) -> str:
    return html.escape(s or "")


def _fmt_dt(dt: datetime | None) -> str:
    return dt.astimezone().strftime("%Y-%m-%d %H:%M") if dt else "未取得"


def _row_class(entry: PlanActualEntry) -> str:
    """行の色: 一致=緑 / 予定のみ・実績のみ・相違=赤 / 時刻ズレ・担当違い・重複=黄。"""
    if entry.is_duplicate:
        return "warn"
    if entry.category == CAT_MATCH:
        return "ok" if not entry.tags else "warn"
    if entry.category in (CAT_TIME, CAT_STAFF):
        return "warn"
    return "ng"


def _side_cells(row: PARow | None) -> str:
    if row is None:
        return '<td class="t">—</td><td>—</td><td>—</td>'
    staff = _esc(row.staff1) if row.staff1 else NO_STAFF
    if row.staff2:
        staff += f" +{_esc(row.staff2)}"
    return (
        f'<td class="t">{_esc(row.start)}–{_esc(row.end)}</td>'
        f"<td>{staff}</td>"
        f'<td class="svc">{_esc(row.service)}</td>'
    )


def _tag_html(entry: PlanActualEntry) -> str:
    return "".join(f'<span class="tag warn">{_esc(t)}</span>' for t in entry.tags)


def _day_sections(report: PlanActualReport) -> str:
    out: list[str] = []
    for pday in report.days:
        weekday = f"（{WEEKDAY_JA[pday.date.weekday()]}）" if pday.date else ""
        ng = sum(1 for e in pday.entries if e.category != CAT_MATCH)
        body = "".join(
            f'<tr class="{_row_class(e)}">'
            f"<td>{_esc(e.patient)}</td>"
            f"{_side_cells(e.plan)}{_side_cells(e.actual)}"
            f'<td class="res">{_esc(e.category_label)}{_tag_html(e)}</td>'
            f"<td>{_esc(e.advice)}</td>"
            "</tr>"
            for e in pday.entries
        )
        compact = " compact" if len(pday.entries) <= 14 else ""
        out.append(
            f'<section class="day{compact}">'
            f"<h2>{report.month[:4]}/{int(report.month[5:7])}/{pday.day}{weekday}"
            f'<span class="cnt">{len(pday.entries)}件・要確認 {ng}</span></h2>'
            "<table><thead><tr>"
            '<th rowspan="2">利用者</th><th colspan="3">予定</th><th colspan="3">実績</th>'
            '<th rowspan="2">判定</th><th rowspan="2">推奨対処</th></tr>'
            "<tr><th>時刻</th><th>担当</th><th>サービス内容</th>"
            "<th>時刻</th><th>担当</th><th>サービス内容</th></tr></thead>"
            f"<tbody>{body}</tbody></table></section>"
        )
    return "".join(out)


def _by_staff_table(report: PlanActualReport) -> str:
    if not report.by_staff:
        return '<p class="small">対象データがありません。</p>'
    head = "".join(f"<th>{_esc(CATEGORY_LABELS[c])}</th>" for c in CATEGORIES)
    body = "".join(
        f'<tr><td class="k">{_esc(s.staff)}</td>'
        + "".join(f'<td class="n">{s.counts.get(c, 0) or ""}</td>' for c in CATEGORIES)
        + f'<td class="n">{s.duplicates or ""}</td><td class="n">{s.total}</td></tr>'
        for s in report.by_staff
    )
    return (
        f"<table><thead><tr><th>担当</th>{head}<th>重複</th><th>計</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def _caveat_box(report: PlanActualReport) -> str:
    """注意書き。実績CSVの制約 + 同行者の扱い + 行数が食い違うときの警告。"""
    lines = [_esc(CAVEAT) + "。", _esc(CAVEAT_ACCOMPANY) + "。"]
    if report.row_counts_diverge:
        lines.append(
            "<b>予定側の行数が実績側と大きく異なります。予定 CSV が週単位の"
            f"可能性があります</b>（予定 {report.plan_rows} 行 / 実績 "
            f"{report.actual_rows} 行）。"
        )
    body = "".join(f"<li>{line}</li>" for line in lines)
    return f'\n<div class="box warn"><b>注意</b><ul>{body}</ul></div>\n'


def _malformed_note(report: PlanActualReport) -> str:
    """列数不足で読めなかった行があるときだけ、読み方に 1 項目足す。"""
    if report.malformed_rows <= 0:
        return ""
    return (
        f"<li><b>読めなかった行 {report.malformed_rows} 件</b> … 列数が足りず"
        "突合できませんでした。CSV の出力条件を確認してください（この件数分は"
        "どの判定にも入っていません）。</li>"
    )


def render_plan_actual_html(report: PlanActualReport) -> str:
    """A4 縦 1 枚目=要約 / 2 枚目以降=日別明細 の自己完結 HTML。"""
    c = report.counts
    need = sum(c.get(k, 0) for k in CATEGORIES if k != CAT_MATCH)
    lead_class = "green" if need == 0 else ("amber" if need <= 10 else "red")
    lead = (
        "予定と実績は全件一致しています。実績側の掃除は不要です。"
        if need == 0
        else f"要確認 <b>{need}</b> 件（うち予定のみ {c.get(CAT_PLAN_ONLY, 0)} 件・"
        f"実績のみ {c.get(CAT_ACTUAL_ONLY, 0)} 件）。請求前にカイポケの実績側を整えてください。"
    )
    title = f"カイポケ 予定×実績 突合 {report.month}"
    office = f" / 事業所: {_esc(report.office_name)}" if report.office_name else ""
    chips = "".join(
        f'<span>{_esc(CATEGORY_LABELS[k])} <b class="{cls}">{c.get(k, 0)}</b></span>'
        for k, cls in (
            (CAT_MATCH, "ok"),
            (CAT_TIME, "warn"),
            (CAT_STAFF, "warn"),
            (CAT_BOTH, "ng"),
            (CAT_PLAN_ONLY, "ng"),
            (CAT_ACTUAL_ONLY, "ng"),
        )
    )
    return (
        '<!doctype html>\n<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(title)}</title>\n<style>" + REPORT_CSS + "</style>\n</head>\n<body>\n"
        '<div class="toolbar"><button onclick="window.print()">印刷 / PDF 保存</button></div>\n'
        '<div class="sheet">\n'
        '<section class="cover">\n'
        f"<h1>{_esc(title)}</h1>\n"
        f'<div class="meta"><span>対象月: {_esc(report.month)}{office}</span>'
        f"<span>作成: {_fmt_dt(report.generated_at)}</span>"
        f"<span>予定CSV: {_fmt_dt(report.plan_fetched_at)}（{report.plan_rows}行）</span>"
        f"<span>実績CSV: {_fmt_dt(report.actual_fetched_at)}（{report.actual_rows}行）</span></div>\n"
        f'<div class="lead {lead_class}">{lead}</div>\n'
        f'<div class="kpi">{chips}'
        f'<span>重複 <b class="warn">{c.get("重複", 0)}</b><small>（内数）</small></span>'
        f"<span>イベント除外 <b>{report.events_skipped}</b>"
        "<small>（対象外）</small></span></div>\n"
        "<h2>担当者別の内訳</h2>\n"
        + _by_staff_table(report)
        + _caveat_box(report)
        + "<h2>この表の読み方</h2>\n<ol>"
        "<li><b>一致</b> … 予定と実績で 担当・時刻 が揃っている行。対処不要。</li>"
        "<li><b>時刻ズレ</b> … 担当は同じで時刻が違う。実績の時刻が実態なので、"
        "予定を合わせるか実績を直すかを決める。</li>"
        "<li><b>担当違い</b> … 時刻は同じで担当が違う。急な代替訪問なら実績が正。</li>"
        "<li><b>相違（時刻・担当）</b> … 同じ利用者の同じ日で、担当も時刻も揃わない組。</li>"
        "<li><b>予定のみ</b> … 実績が入っていない。実施したなら実績を登録、"
        "未実施なら予定を取り消す。</li>"
        "<li><b>実績のみ（予定外）</b> … 予定に無い実績。誤登録なら実績側を削除する。</li>"
        "<li><b>重複（内数）</b> … 同じ日・同じ利用者・同じ担当の行が 2 本以上ある。"
        "まずこれを潰さないと他の判定が読めない。</li>"
        "<li><b>同行違い（タグ）</b> … 主担当は合っているが同行者（職員名2）が"
        "食い違う行。</li>"
        "<li><b>イベント除外（対象外）</b> … 業務種別が 医療保険／介護保険 でない行"
        "（朝会などの個別業務）は訪問ではないので突合していない。</li>"
        + _malformed_note(report)
        + "</ol>\n"
        '<div class="pfoot">らく助 — カイポケ 予定×実績 突合レポート'
        f"（{_esc(report.month)}・{_fmt_dt(report.generated_at)} 作成）</div>\n"
        "</section>\n"
        + _day_sections(report)
        + '<div class="pfoot">らく助 — カイポケ 予定×実績 突合レポート'
        f"（{_esc(report.month)}・{_fmt_dt(report.generated_at)} 作成）</div>\n"
        "</div>\n</body>\n</html>"
    )
