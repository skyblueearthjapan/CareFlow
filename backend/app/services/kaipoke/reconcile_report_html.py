"""らく助 × カイポケ 突合レポート (read-only・自己完結 HTML).

PO 要望 (2026-09-01): 差分確認/突き合わせのときに「見やすい HTML」を出したい。
8 月実績合わせで手作りした突合一覧 (日別・らく助×カイポケ横並び・一致✓/相違赤) を
機能化したもの。

データ源:
  * らく助側 = ``csv_builder.resolve_month_rows`` (カイポケ送信 CSV と同一ロジック)
  * カイポケ側 = ``kaipoke_csv_snapshots`` の最新スナップショット (RPA は回さない)。
    鮮度はレポート冒頭に取得時刻として明示する。
突合キーは (日付, 利用者名) — 同日同患者の行同士を開始時刻の近い順にペアリングし、
時刻/担当/職員2/サービス内容の相違を判定する。時刻は前ゼロ有無を正規化する
(実績 CSV 由来の「9:50」対策・2026-09-01 実測)。
"""

from __future__ import annotations

import csv
import html
import io
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.services.kaipoke.csv_builder import BuildOptions, KaipokeCsvRow, resolve_month_rows
from app.services.kaipoke.csv_snapshot import get_latest
from app.services.kaipoke.name_match import normalize_name_key
from app.services.patient_status_sync import is_schedulable_status, status_label, today_jst

WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]


def _norm_name(s: str) -> str:
    """表示用の軽い正規化 (全角スペース→半角)。照合キーは normalize_name_key を使う。"""
    return (s or "").replace("　", " ").strip()


def _service_matches(a: str, b: str) -> bool:
    """サービス内容の一致判定 — diff エンジンと同じ「完全一致 or 双方向の前方一致」。

    接尾が伸びただけ (例: 加算表記) は同一扱い。空文字は完全一致のみ (C-9 の穴対策)。
    """
    if a == b:
        return True
    if not a or not b:
        return False
    return a.startswith(b) or b.startswith(a)


def _norm_time(s: str) -> str:
    t = (s or "").strip()[:5]
    if t and ":" in t and len(t.split(":")[0]) == 1:
        t = "0" + t
    return t


@dataclass
class ReconRow:
    """突合の 1 行 (らく助側 or カイポケ側)."""

    start: str
    end: str
    staff1: str
    staff2: str
    service: str


#: 非稼働患者の行のカテゴリ表記 (設計 §3-5「非稼働患者の行」= 削除候補)。
CATEGORY_INACTIVE = "非稼働患者"


@dataclass
class ReconPair:
    day: date
    patient: str
    local: ReconRow | None  # らく助
    remote: ReconRow | None  # カイポケ
    diffs: list[str] = field(default_factory=list)  # 空 = 一致
    # 患者ステータス連動 Phase 3 §3-5: らく助側の患者が非稼働 (入院中/解約済み等)。
    patient_status: str | None = None

    @property
    def inactive_patient(self) -> bool:
        """らく助側の患者が非稼働か (**事実だけ**。削除候補かどうかは別判定)。"""
        return self.patient_status is not None and not is_schedulable_status(self.patient_status)

    @property
    def structural_category(self) -> str:
        """行の構造だけで決まる従来どおりの判定 (一致/相違/片側のみ)。

        非稼働の重ね合わせを **含まない**。日別見出しの「相違 N」など、Phase 3 の
        前後で数え方が変わってはいけない集計はこちらを使う。
        """
        if self.local is None:
            return "カイポケのみ"
        if self.remote is None:
            return "らく助のみ"
        return "一致" if not self.diffs else "+".join(self.diffs)

    @property
    def is_delete_candidate(self) -> bool:
        """「非稼働患者の削除候補」= カイポケにだけ残った今日以降の行 (§3-5)。

        3 条件すべてを満たすときだけ True (レビュー決定):

        * ``local is None`` — らく助に対応する行が無い (= カイポケのみ)。
          一致 / 相違の行は **らく助にも実体がある** ので、消すのではなく直す話。
        * ``day >= 今日 (JST)`` — 過去日はカイポケ側の **実績**。消したら記録が飛ぶ。
        * 患者が非稼働。
        """
        return self.local is None and self.inactive_patient and self.day >= today_jst()

    @property
    def category(self) -> str:
        if self.is_delete_candidate:
            return CATEGORY_INACTIVE
        return self.structural_category


@dataclass
class SnapshotInfo:
    month: str
    fetched_at: datetime | None
    row_count: int
    source_op: str


@dataclass
class ReconcileReport:
    week_start: date
    week_end: date
    generated_at: datetime
    snapshots: list[SnapshotInfo]
    pairs: list[ReconPair]

    @property
    def counts(self) -> dict[str, int]:
        """判定別の件数。

        既存 4 キー (一致/相違/らく助のみ/カイポケのみ) は **行の構造だけ** で数える
        (合計 = 全件が常に成立する)。``非稼働患者`` はその上に重ねる **内数**
        (設計 §3-5)。行の ``category`` 表示は削除候補を優先するが、集計は内数のまま
        にして「全 N 件 = 4 キーの和」という読み方を壊さない。
        """
        c = {"一致": 0, "相違": 0, "らく助のみ": 0, "カイポケのみ": 0, CATEGORY_INACTIVE: 0}
        for p in self.pairs:
            if p.local is None:
                c["カイポケのみ"] += 1
            elif p.remote is None:
                c["らく助のみ"] += 1
            elif p.diffs:
                c["相違"] += 1
            else:
                c["一致"] += 1
            if p.is_delete_candidate:
                c[CATEGORY_INACTIVE] += 1
        return c

    @property
    def inactive_pairs(self) -> list[ReconPair]:
        """削除候補 (非稼働 × カイポケのみ × 今日以降) だけを返す (専用セクション用)。"""
        return [p for p in self.pairs if p.is_delete_candidate]


def _local_row(r: KaipokeCsvRow) -> ReconRow:
    return ReconRow(
        start=_norm_time(r.start_time.strftime("%H:%M")),
        end=_norm_time(r.end_time.strftime("%H:%M")),
        staff1=_norm_name(r.primary.name if r.primary else ""),
        staff2=_norm_name(r.secondary.name if r.secondary else ""),
        service=(r.service_content or "").strip(),
    )


def _parse_snapshot_rows(csv_text: str) -> list[tuple[int, str, ReconRow]]:
    """スナップショット CSV → (日, 利用者, ReconRow)。ヘッダ名で列を引く。"""
    rows = [r for r in csv.reader(io.StringIO(csv_text)) if any(r)]
    if not rows:
        return []
    idx = {name: k for k, name in enumerate(rows[0])}
    need = ["日付", "利用者", "開始時間", "終了時間", "職員名１", "職員名２", "サービス内容"]
    if any(n not in idx for n in need):
        return []
    hi = max(idx[n] for n in need)
    out: list[tuple[int, str, ReconRow]] = []
    for r in rows[1:]:
        if len(r) <= hi or not r[idx["日付"]].strip().isdigit():
            continue
        out.append(
            (
                int(r[idx["日付"]]),
                _norm_name(r[idx["利用者"]]),
                ReconRow(
                    start=_norm_time(r[idx["開始時間"]]),
                    end=_norm_time(r[idx["終了時間"]]),
                    staff1=_norm_name(r[idx["職員名１"]]),
                    staff2=_norm_name(r[idx["職員名２"]]),
                    service=(r[idx["サービス内容"]] or "").strip(),
                ),
            )
        )
    return out


def _tmin(t: str) -> int:
    try:
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return 10**6


def _pair_group(
    day: date, patient: str, locals_: list[ReconRow], remotes: list[ReconRow]
) -> list[ReconPair]:
    pairs: list[ReconPair] = []
    used: set[int] = set()
    for lo in sorted(locals_, key=lambda r: r.start):
        best, bd = None, 10**9
        for j, rm in enumerate(remotes):
            if j in used:
                continue
            d = abs(_tmin(lo.start) - _tmin(rm.start))
            if d < bd:
                bd, best = d, j
        if best is None:
            pairs.append(ReconPair(day=day, patient=patient, local=lo, remote=None))
            continue
        used.add(best)
        rm = remotes[best]
        diffs: list[str] = []
        if lo.start != rm.start or lo.end != rm.end:
            diffs.append("時刻")
        # 氏名は表記ゆれ (空白の有無・異体字 髙/高 等) を吸収して比較する。
        if normalize_name_key(lo.staff1) != normalize_name_key(rm.staff1):
            diffs.append("担当")
        if normalize_name_key(lo.staff2) != normalize_name_key(rm.staff2):
            diffs.append("職員2")
        if not _service_matches(lo.service, rm.service):
            diffs.append("サービス")
        pairs.append(ReconPair(day=day, patient=patient, local=lo, remote=rm, diffs=diffs))
    for j, rm in enumerate(remotes):
        if j not in used:
            pairs.append(ReconPair(day=day, patient=patient, local=None, remote=rm))
    return pairs


async def build_reconcile_report(
    db: AsyncSession, *, week_start: date, days: int = 7
) -> ReconcileReport:
    """週 (week_start から days 日) のらく助×カイポケ突合を組み立てる (read-only)。"""
    days = max(1, min(days, 7))
    week_end = week_start + timedelta(days=days - 1)
    months = sorted(
        {(d.year, d.month) for d in (week_start + timedelta(days=i) for i in range(days))}
    )

    # らく助側 (送信 CSV と同一ロジック)。キーは正規化名・表示名は別に控える。
    local_by_key: dict[tuple[date, str], list[ReconRow]] = {}
    display_names: dict[tuple[date, str], str] = {}
    for y, m in months:
        for r in await resolve_month_rows(db, BuildOptions(year=y, month=m)):
            if not (week_start <= r.visit_date <= week_end):
                continue
            # イベント行 (業務種別がイベント名) は突合対象外 — 訪問だけを見る。
            if r.business_type not in ("医療保険", "介護保険"):
                continue
            key = (r.visit_date, normalize_name_key(r.patient_name))
            display_names.setdefault(key, _norm_name(r.patient_name))
            local_by_key.setdefault(key, []).append(_local_row(r))

    # カイポケ側 (最新スナップショット)
    remote_by_key: dict[tuple[date, str], list[ReconRow]] = {}
    snapshots: list[SnapshotInfo] = []
    for y, m in months:
        month_str = f"{y:04d}-{m:02d}"
        snap = await get_latest(db, month=month_str)
        if snap is None:
            snapshots.append(
                SnapshotInfo(month=month_str, fetched_at=None, row_count=0, source_op="なし")
            )
            continue
        snapshots.append(
            SnapshotInfo(
                month=month_str,
                fetched_at=snap.fetched_at,
                row_count=snap.row_count,
                source_op=snap.source_op,
            )
        )
        for day_num, patient, row in _parse_snapshot_rows(snap.csv_text):
            try:
                d = date(y, m, day_num)
            except ValueError:
                continue
            if week_start <= d <= week_end:
                key = (d, normalize_name_key(patient))
                display_names.setdefault(key, patient)
                remote_by_key.setdefault(key, []).append(row)

    # 患者ステータス (設計 §3-5)。突合キーと同じ正規化名で 1 クエリだけ引く。
    # らく助側が非稼働の患者に対応するカイポケ行は「削除候補」として出す。
    status_by_name_key: dict[str, str] = {}
    prows = (
        await db.execute(select(Patient.name, Patient.status).where(Patient.deleted_at.is_(None)))
    ).all()
    for pname, pstatus in prows:
        nk = normalize_name_key(pname or "")
        if not nk:
            continue
        # 同名 (正規化後) が複数居たら「稼働中が 1 人でも居れば稼働中」に倒す
        # (突合行がどちらの患者のものか区別できないため、削除候補に誤って積むより
        #  保守的に扱う)。
        if nk in status_by_name_key and is_schedulable_status(status_by_name_key[nk]):
            continue
        status_by_name_key[nk] = pstatus

    pairs: list[ReconPair] = []
    for key in sorted(set(local_by_key) | set(remote_by_key)):
        d, _norm_key = key
        display = display_names.get(key, _norm_key)
        group = _pair_group(d, display, local_by_key.get(key, []), remote_by_key.get(key, []))
        pstatus = status_by_name_key.get(_norm_key)
        for pair in group:
            pair.patient_status = pstatus
        pairs.extend(group)

    def _sort_key(p: ReconPair) -> tuple[date, str, str]:
        anchor = p.local or p.remote
        return (p.day, anchor.start if anchor else "", p.patient)

    pairs.sort(key=_sort_key)

    return ReconcileReport(
        week_start=week_start,
        week_end=week_end,
        generated_at=datetime.now(UTC),
        snapshots=snapshots,
        pairs=pairs,
    )


def _esc(s: str) -> str:
    return html.escape(s or "")


def _cell(r: ReconRow | None) -> str:
    if r is None:
        return '<td class="none" colspan="3">—</td>'
    st2 = f" +{_esc(r.staff2)}" if r.staff2 else ""
    svc = _esc(r.service[-6:]) if r.service else ""
    return (
        f'<td class="t">{_esc(r.start)}–{_esc(r.end)}</td>'
        f"<td>{_esc(r.staff1)}{st2}</td>"
        f'<td class="svc">{svc}</td>'
    )


def _row_class(p: ReconPair) -> str:
    """行の色 (削除候補 > 相違 > 一致)。"""
    if p.is_delete_candidate:
        return "inact"
    return "ok" if p.structural_category == "一致" else "ng"


def _inactive_badge(p: ReconPair) -> str:
    """利用者名の横に付ける「入院中」等のバッジ (削除候補のときだけ)。

    過去日や一致行の非稼働患者には出さない — そこは実績であって削除候補ではなく、
    バッジを出すと「消していい行」に見えてしまう。
    """
    if not p.is_delete_candidate:
        return ""
    return f'<span class="badge">{_esc(status_label(p.patient_status))}</span>'


def _inactive_section(report: ReconcileReport) -> str:
    """「非稼働患者の行（削除候補）」セクション (設計 §3-5)。0 件なら出さない。"""
    rows = report.inactive_pairs
    if not rows:
        return ""
    body = "".join(
        f'<tr class="inact"><td>{p.day.month}/{p.day.day}</td>'
        f"<td>{_esc(p.patient)}{_inactive_badge(p)}</td>"
        f"{_cell(p.local)}{_cell(p.remote)}</tr>"
        for p in rows
    )
    return (
        f'<h2>非稼働患者の行（削除候補）<span class="cnt">{len(rows)}件</span></h2>'
        '<div class="warn">らく助側で「稼働中」以外になっている利用者の、'
        "<b>今日以降のカイポケのみの行</b>です（⇧送信の delete 差分＝削除候補）。"
        "過去日や、らく助にも予定がある行（一致／相違）は実績・要調整なのでここには出しません。"
        "カイポケの<b>週間パターンを停止</b>しないと翌月の展開でまた復活します。</div>"
        '<table><thead><tr><th>日付</th><th>利用者</th><th colspan="3">らく助</th>'
        f'<th colspan="3">カイポケ</th></tr></thead><tbody>{body}</tbody></table>'
    )


def render_reconcile_html(report: ReconcileReport) -> str:
    counts = report.counts
    by_day: dict[date, list[ReconPair]] = {}
    for p in report.pairs:
        by_day.setdefault(p.day, []).append(p)
    sections: list[str] = []
    for d in sorted(by_day):
        rows = by_day[d]
        # 見出しの「相違 N」は **構造判定** で数える (Phase 3 の前後で数が動かない)。
        ng = sum(1 for p in rows if p.structural_category != "一致")
        body = "".join(
            f'<tr class="{_row_class(p)}">'
            f"<td>{_esc(p.patient)}{_inactive_badge(p)}</td>{_cell(p.local)}{_cell(p.remote)}"
            f'<td class="cat">{"✓" if p.category == "一致" else _esc(p.category)}</td></tr>'
            for p in rows
        )
        sections.append(
            f"<h2>{d.month}/{d.day}（{WEEKDAY_JA[d.weekday()]}）"
            f'<span class="cnt">{len(rows)}件・相違 {ng}</span></h2>'
            f'<table><thead><tr><th>利用者</th><th colspan="3">らく助</th>'
            f'<th colspan="3">カイポケ</th><th>判定</th></tr></thead><tbody>{body}</tbody></table>'
        )
    snap_meta = " / ".join(
        f"{s.month}: "
        + (
            f"{s.fetched_at.astimezone().strftime('%m/%d %H:%M')} 取得"
            f"（{s.row_count}行・{s.source_op}）"
            if s.fetched_at
            else "スナップショット無し"
        )
        for s in report.snapshots
    )
    total = len(report.pairs)
    generated = report.generated_at.astimezone().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>らく助×カイポケ 突合 {report.week_start.month}/{report.week_start.day}週</title>
<style>
:root{{--ink:#1e2a33;--soft:#55656f;--rule:#d7dde2;--rule2:#9aa7b0;--tint:#f3f6f8;--ok-t:#f4faf7;--ng:#a8392f;--ng-t:#fbebe9;--in:#8a5a12;--in-t:#fdf4e3}}
html{{color-scheme:light}}
body{{margin:0;background:#e9ecef;color:var(--ink);font-family:"BIZ UDPGothic","Noto Sans JP","Hiragino Sans","Yu Gothic UI",Meiryo,sans-serif;font-size:8.6pt;line-height:1.45;font-feature-settings:"palt"}}
.sheet{{background:#fff;width:210mm;margin:8mm auto;padding:12mm;box-sizing:border-box;box-shadow:0 2px 14px rgba(20,30,40,.12)}}
h1{{font-size:14pt;margin:0;border-bottom:2px solid var(--ink);padding-bottom:2mm}}
.meta{{color:var(--soft);font-size:8.5pt;margin:2mm 0 1mm}}
.warn{{background:var(--tint);border-left:3px solid var(--rule2);padding:2mm 3mm;font-size:8.5pt;margin:2mm 0 3mm}}
.sum{{display:flex;gap:3mm;margin:0 0 4mm;flex-wrap:wrap}}
.sum span{{background:var(--tint);border:1px solid var(--rule);padding:1mm 3mm;border-radius:2mm}}
.sum b{{font-size:11pt}}
h2{{font-size:10pt;margin:4mm 0 1mm}} .cnt{{color:var(--soft);font-weight:400;font-size:8.5pt;margin-left:2mm}}
table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}
th{{text-align:left;font-size:7.5pt;color:var(--soft);border-bottom:1px solid var(--rule2);padding:1mm 1.5mm}}
td{{border-bottom:1px solid var(--rule);padding:1mm 1.5mm;vertical-align:top}}
td.t{{white-space:nowrap}} td.svc{{color:var(--soft)}} td.none{{color:var(--rule2);text-align:center}}
tr.ok{{background:var(--ok-t)}} tr.ok td.cat{{color:#2f7d5b}}
tr.ng{{background:var(--ng-t)}} tr.ng td.cat{{color:var(--ng);font-weight:700;white-space:nowrap}}
tr.inact{{background:var(--in-t)}} tr.inact td:not(.none):not(.svc){{color:var(--in)}}
tr.inact td.cat{{color:var(--in);font-weight:700;white-space:nowrap}}
.badge{{display:inline-block;margin-left:1.5mm;padding:0 1.5mm;border:1px solid var(--in);border-radius:1mm;color:var(--in);font-size:7pt;white-space:nowrap}}
.toolbar{{width:210mm;margin:8mm auto 0;display:flex;justify-content:flex-end}}
.toolbar button{{font:inherit;font-size:10pt;padding:5px 14px;border:1px solid var(--rule2);background:#fff;border-radius:3px;cursor:pointer}}
@media print{{body{{background:#fff}}.sheet{{margin:0;box-shadow:none;width:auto}}.toolbar{{display:none}} h2{{break-after:avoid}}}}
</style>
</head>
<body>
<div class="toolbar"><button onclick="window.print()">印刷 / PDF 保存</button></div>
<div class="sheet">
<h1>らく助 × カイポケ 突合一覧</h1>
<div class="meta">対象週: {report.week_start.isoformat()} 〜 {report.week_end.isoformat()} / 作成: {generated}</div>
<div class="warn">カイポケ側 = 保存済みの最新スナップショット（{_esc(snap_meta)}）。カイポケの「今」と比べたい場合は、先に差分確認（🔄突合）を実行してから開き直してください。</div>
<div class="sum"><span>全 <b>{total}</b> 件</span><span>一致 <b style="color:#2f7d5b">{counts["一致"]}</b></span><span>相違 <b style="color:#a8392f">{counts["相違"]}</b></span><span>らく助のみ <b>{counts["らく助のみ"]}</b></span><span>カイポケのみ <b>{counts["カイポケのみ"]}</b></span><span>非稼働患者 <b style="color:#8a5a12">{counts[CATEGORY_INACTIVE]}</b><small>（内数）</small></span></div>
{_inactive_section(report)}
{"".join(sections)}
</div>
</body>
</html>"""
