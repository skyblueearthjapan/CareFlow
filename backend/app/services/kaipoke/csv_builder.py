"""CareFlow visits → カイポケ18列CSV 生成 (K-1d・差分適用の心臓部).

現状 kaipoke-api 側の「最適化CSV」は旧 GAS 由来だった。これを CareFlow の
確定 visits から生成することで、差分の「正」を CareFlow に一本化する
(設計書 `docs/plans/kaipoke-csv-generation-design.md` §3)。

構成:
  * ``KaipokeCsvRow`` — 1 訪問ぶんの「解決済み」入力 (DB 参照を済ませた素の値)。
  * ``row_to_cells`` / ``build_csv`` — 純関数。18 列の並び・cp932 を保証。DB 非依存で
    ゴールデンテストしやすい。
  * ``build_month_csv`` — DB から対象月の visits を引いて ``KaipokeCsvRow`` へ解決し
    CSV を返す薄いオーケストレータ。

18 列 (diff/engine.py `_parse_kaipoke_rows` と厳密一致):
  職員名1, 職種1, 職員名2, 職種2, 同行2, 職員名3, 職種3, 同行3,
  事業所名, 日付, 曜日, 利用者, 業務種別, サービス内容,
  開始時間, 終了時間, 提供時間（分）, 備考
"""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass
from datetime import date, time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.office import Office
    from app.models.staff import Staff

# カイポケ18列ヘッダー (実 export と同一表記)。
HEADER: list[str] = [
    "職員名１",
    "職種１",
    "職員名２",
    "職種２",
    "同行２",
    "職員名３",
    "職種３",
    "同行３",
    "事業所名",
    "日付",
    "曜日",
    "利用者",
    "業務種別",
    "サービス内容",
    "開始時間",
    "終了時間",
    "提供時間（分）",
    "備考",
]

_WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]  # date.weekday(): 月=0

# patient.insurance → カイポケ「業務種別」。
_INSURANCE_TO_BUSINESS = {"medical": "医療保険", "care": "介護保険"}

# サービス内容が未設定 (patient.kaipoke_service_content=NULL かつ事業所既定も無い) 時の
# 最終フォールバック。事業所「よりより」= 精神科訪問看護の実データ既定値。
DEFAULT_SERVICE_CONTENT = "精神基本療養費Ⅰ・正看"

COMPANION_MARK = "○"


@dataclass
class StaffCell:
    """CSV の職員名/職種 (+同行) 1 枠ぶん。"""

    name: str
    qualification: str = ""
    companion: bool = False  # True の場合、同行N 列に ○


@dataclass
class KaipokeCsvRow:
    """1 訪問ぶんの解決済み入力 (DB 参照済み)。"""

    patient_name: str
    visit_date: date
    start_time: time
    end_time: time
    office_name: str
    business_type: str  # 医療保険 / 介護保険 / イベント名
    service_content: str
    primary: StaffCell
    secondary: StaffCell | None = None
    tertiary: StaffCell | None = None
    remarks: str = ""


def _fmt_time(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def _duration_min(start: time, end: time) -> int:
    """訪問時間(分)。日跨ぎは想定外(精神科訪問看護は日中のみ)のため0下限で防御。"""
    mins = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    return max(mins, 0)


def business_type_from_insurance(insurance: str | None) -> str:
    """patient.insurance → カイポケ業務種別。未知/NULL は医療保険既定。"""
    return _INSURANCE_TO_BUSINESS.get((insurance or "").lower(), "医療保険")


def row_to_cells(row: KaipokeCsvRow) -> list[str]:
    """``KaipokeCsvRow`` を18列セルへ (HEADER と同じ並び)。"""
    sec = row.secondary
    ter = row.tertiary
    return [
        row.primary.name,
        row.primary.qualification,
        sec.name if sec else "",
        sec.qualification if sec else "",
        COMPANION_MARK if (sec and sec.companion) else "",
        ter.name if ter else "",
        ter.qualification if ter else "",
        COMPANION_MARK if (ter and ter.companion) else "",
        row.office_name,
        str(row.visit_date.day),
        _WEEKDAY_JA[row.visit_date.weekday()],
        row.patient_name,
        row.business_type,
        row.service_content,
        _fmt_time(row.start_time),
        _fmt_time(row.end_time),
        str(_duration_min(row.start_time, row.end_time)),
        row.remarks,
    ]


def build_csv(rows: list[KaipokeCsvRow], *, encoding: str = "cp932") -> bytes:
    """18列CSV をバイト列で返す (既定 cp932 = カイポケ互換)。

    cp932 で表現できない文字は '?' に置換せず ``errors='replace'`` で流す前に
    UTF-8 で保持したい場合は encoding='utf-8-sig' を指定する。
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(HEADER)
    for r in rows:
        writer.writerow(row_to_cells(r))
    text = buf.getvalue()
    return text.encode(encoding, errors="replace")


# --- DB オーケストレータ ---------------------------------------------------


@dataclass
class BuildOptions:
    year: int
    month: int
    office_id: uuid.UUID | None = None  # None = 全拠点
    default_service_content: str = DEFAULT_SERVICE_CONTENT
    # 同行判定: mentor(指導者=影) を同行○ とみなす。secondary(第2看護師) は既定で非同行。
    mentor_as_companion: bool = True


async def build_month_csv(
    db: AsyncSession, opts: BuildOptions, *, encoding: str = "cp932"
) -> bytes:
    """対象月の visits を解決して18列CSV を返す。

    薄いオーケストレータ: 対象 visits を引き、患者/スタッフ/拠点を解決して
    ``KaipokeCsvRow`` に落とし、``build_csv`` へ渡す。純関数側 (row_to_cells) を
    テストで固めてあるので、ここは「解決」の正しさに集中する。
    """
    rows = await resolve_month_rows(db, opts)
    return build_csv(rows, encoding=encoding)


async def resolve_month_rows(db: AsyncSession, opts: BuildOptions) -> list[KaipokeCsvRow]:
    """対象月の visits を ``KaipokeCsvRow`` のリストへ解決する。"""
    from calendar import monthrange

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models.office import Office
    from app.models.staff import Staff
    from app.models.visit import Visit

    first = date(opts.year, opts.month, 1)
    last = date(opts.year, opts.month, monthrange(opts.year, opts.month)[1])

    # Visit.patient は lazy="noload" のため selectinload で明示ロードする。
    stmt = (
        select(Visit)
        .where(
            Visit.deleted_at.is_(None),
            Visit.visit_date >= first,
            Visit.visit_date <= last,
            Visit.status != "cancelled",
        )
        .options(selectinload(Visit.patient))
        .order_by(Visit.visit_date, Visit.start_time)
    )
    visits = list((await db.scalars(stmt)).all())

    # スタッフを一括ロード (name/qualification 解決用)。
    staff_ids = set()
    for v in visits:
        for sid in (v.primary_staff_id, v.secondary_staff_id, v.mentor_staff_id):
            if sid:
                staff_ids.add(sid)
    staff_map: dict[uuid.UUID, Staff] = {}
    if staff_ids:
        srows = (await db.scalars(select(Staff).where(Staff.id.in_(staff_ids)))).all()
        staff_map = {s.id: s for s in srows}

    # 拠点を一括ロード (kaipoke_name/name 解決用)。Patient に primary_office
    # relationship が無いため id で引く。
    office_ids = {v.patient.primary_office_id for v in visits if v.patient}
    office_ids.discard(None)
    office_map: dict[uuid.UUID, Office] = {}
    if office_ids:
        orows = (await db.scalars(select(Office).where(Office.id.in_(office_ids)))).all()
        office_map = {o.id: o for o in orows}

    def _cell(sid, *, companion: bool) -> StaffCell | None:
        if not sid or sid not in staff_map:
            return None
        s = staff_map[sid]
        return StaffCell(name=s.name, qualification=s.qualification or "", companion=companion)

    result: list[KaipokeCsvRow] = []
    for v in visits:
        patient = v.patient
        if patient is None:
            continue
        if opts.office_id is not None and patient.primary_office_id != opts.office_id:
            continue
        office = office_map.get(patient.primary_office_id)
        office_name = (office.kaipoke_name or office.name) if office else ""
        service_content = patient.kaipoke_service_content or opts.default_service_content

        primary = _cell(v.primary_staff_id, companion=False)
        if primary is None:
            # 主担当未確定の訪問は転記対象外 (カイポケには職員必須)。
            continue
        secondary = _cell(v.secondary_staff_id, companion=False)
        mentor = _cell(v.mentor_staff_id, companion=opts.mentor_as_companion)
        # 2 枠目は secondary 優先、無ければ mentor(同行)。
        slot2 = secondary or mentor
        slot3 = mentor if (secondary and mentor) else None

        result.append(
            KaipokeCsvRow(
                patient_name=patient.name,
                visit_date=v.visit_date,
                start_time=v.start_time,
                end_time=v.end_time,
                office_name=office_name,
                business_type=business_type_from_insurance(patient.insurance),
                service_content=service_content,
                primary=primary,
                secondary=slot2,
                tertiary=slot3,
                remarks=v.note or "",
            )
        )
    return result
