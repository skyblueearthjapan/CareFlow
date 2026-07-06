"""CareFlow 内で完結するローカル差分 (K-2b・心臓部の統合).

従来の ``/api/diff`` は kaipoke-api 側で「現況 vs 旧GAS由来の最適化CSV」を比較して
いた。K-2b では最適化CSVを CareFlow の確定 visits から生成 (csv_builder) し、差分も
CareFlow 内 (diff/engine.compare_schedules_from_content) で取る。これにより差分の
「正」が CareFlow に一本化される (設計書 kaipoke-csv-generation-design.md §3/§5)。

フロー:
  1. kaipoke から現況CSVを同期エクスポート (csv_content を取得)
  2. CareFlow visits から最適化CSVを生成 (build_month_csv)
  3. compare_schedules_from_content(現況, 最適化) → Correction リスト
     (= 現況をCareFlow確定形へ寄せるための修正 = apply でカイポケへ押す内容)
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models.office import Office
from app.services.diff.engine import Correction, compare_schedules_from_content
from app.services.kaipoke.csv_builder import BuildOptions, build_month_csv

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.kaipoke_client import KaipokeClient

# 同期 export のブロック時間 (~50s) に耐える timeout。
_SYNC_EXPORT_TIMEOUT = 90.0

# カイポケ18列CSV の「事業所名」列インデックス。
_OFFICE_COL = 8


def _filter_current_by_office(current_csv: str, office_name: str) -> str:
    """現況CSVを対象事業所名の行だけに絞る (ヘッダーは保持)。

    optimized 側 (build_month_csv) は office_id で絞られるため、current 側も
    同じ事業所に揃えないと、他拠点の全エントリが誤って delete 差分になる。
    """
    rows = list(csv.reader(io.StringIO(current_csv)))
    if not rows:
        return current_csv
    header, body = rows[0], rows[1:]
    kept = [r for r in body if len(r) > _OFFICE_COL and r[_OFFICE_COL].strip() == office_name]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(kept)
    return buf.getvalue()


async def build_local_diff(
    db: AsyncSession,
    *,
    month: str,
    kaipoke: KaipokeClient,
    office_id: uuid.UUID | None = None,
    week_start: date | None = None,
    week_end: date | None = None,
    direction: str = "outbound",
) -> tuple[list[Correction], dict[str, Any]]:
    """現況(kaipoke) と 最適化(CareFlow生成) の差分を CareFlow 内で計算する。

    week_start 指定時は「週スコープ」: 現況(月まるごと)と最適化の両方を対象週の
    日(1-31)で絞ってから比較する。これにより **対象週外のカイポケ既存予定は比較
    集合から消え、delete 差分にならない** (旧GASの週次運用と同じ安全設計)。
    週外を消さないための核心はこの両側フィルタ (diff/engine が target_week_* で実施)。

    direction:
      * "outbound" (既定) — current=カイポケ現況 / optimized=CareFlow。
        Correction は「カイポケを CareFlow 確定形へ寄せる修正」(= /apply で押す内容)。
      * "inbound" — 引数を入れ替えて比較する。Correction は「CareFlow をカイポケ現況へ
        寄せる修正」= 提供中の週にカイポケ側で入った直し込みの取り込み内容になる。
        (delete=CareFlow にだけ残っている→キャンセル扱い / add=カイポケにだけある)

    Returns ``(corrections, meta)``。同期 export は csv_content を直接返す (async=false)。
    """
    year, mon = int(month[:4]), int(month[5:7])

    resp = await kaipoke.export({"month": month, "async": False}, timeout=_SYNC_EXPORT_TIMEOUT)
    result = resp.get("result") or {}
    current_csv = result.get("csv_content") or ""

    # office_id 指定時: optimized は当該拠点のみ生成されるため、current も同じ拠点に
    # 絞る (揃えないと他拠点が全て delete 差分になり非対称化する)。
    if office_id is not None:
        office = await db.scalar(select(Office).where(Office.id == office_id))
        if office is not None:
            office_name = office.kaipoke_name or office.name
            current_csv = _filter_current_by_office(current_csv, office_name)

    optimized_bytes = await build_month_csv(
        db, BuildOptions(year=year, month=mon, office_id=office_id), encoding="utf-8-sig"
    )
    optimized_csv = optimized_bytes.decode("utf-8-sig")

    # 週スコープ: 対象週の「日」(1-31) を diff/engine の週フィルタへ渡す。
    # diff/engine は current/optimized の両方をこのレンジに絞り、月境界の折返し
    # (start>end) も処理する。CSVの日付列が「日のみ」の設計なので day 比較で成立。
    week_start_day = week_start.day if week_start else None
    if week_start and not week_end:
        week_end = week_start + timedelta(days=6)  # 月曜起点の7日 (旧GAS getWeekRange_ 相当)
    week_end_day = week_end.day if week_end else None

    # inbound は current/optimized を入れ替える (「正」がカイポケ側に移った週の取り込み)。
    if direction == "inbound":
        compare_current, compare_target = optimized_csv, current_csv
    else:
        compare_current, compare_target = current_csv, optimized_csv

    corrections = compare_schedules_from_content(
        compare_current,
        compare_target,
        target_week_start=week_start_day,
        target_week_end=week_end_day,
    )

    meta = {
        "current_row_count": max(0, current_csv.count("\n") - 1),
        "optimized_row_count": max(0, optimized_csv.count("\n") - 1),
        "correction_count": len(corrections),
        "scope": "week" if week_start else "month",
        "direction": direction,
    }
    if week_start:
        meta["week_start"] = week_start.isoformat()
        meta["week_end"] = week_end.isoformat() if week_end else None
    return corrections, meta


def correction_before_after(c: Correction) -> tuple[dict[str, str], dict[str, str]]:
    """Correction を before/after の dict へ (CorrectionSheetItem 用)。"""
    # user_name/remarks を含める: patient_id 未解決(name_match失敗)でも管理画面で
    # どの利用者の修正か特定でき、イベント系のイベント名(remarks)も保持される。
    before = {
        "user_name": c.user_name,
        "date": c.date_from,
        "start_time": c.start_time_from,
        "end_time": c.end_time_from,
        "staff1": c.staff1_from,
        "staff2": c.staff2_from,
        "service_type": c.service_type,
        "business_type": c.business_type,
        "remarks": c.remarks,
    }
    after = {
        "user_name": c.user_name,
        "date": c.date_to,
        "start_time": c.start_time_to,
        "end_time": c.end_time_to,
        "staff1": c.staff1_to,
        "staff2": c.staff2_to,
        "service_type": c.service_type,
        "business_type": c.business_type,
        "remarks": c.remarks,
    }
    return before, after


# CorrectionSheetItem の before/after キー → カイポケ Correction の *_from/*_to キー。
# correction_before_after() の逆変換。apply でカイポケへ送る平坦形式を作る。
def item_to_kaipoke_correction(
    action: str, before: dict[str, Any] | None, after: dict[str, Any] | None
) -> dict[str, str]:
    """CorrectionSheetItem(before/after dict) → カイポケ /api/apply の Correction dict。

    カイポケ側は ``Correction(**item)`` で復元するため、キーは Correction dataclass の
    フィールド名 (user_name / date_from / date_to / *_from / *_to / action /
    business_type / service_type / remarks) と厳密一致させる。
    """
    b = before or {}
    a = after or {}

    def pick(key: str) -> str:
        """after 優先で取得 (空なら before)。user_name/service_type 等の共通フィールド用。"""
        val = a.get(key)
        if val is not None and val != "":
            return str(val)
        return str(b.get(key) or "")

    return {
        "user_name": pick("user_name"),
        "date_from": str(b.get("date") or ""),
        "date_to": str(a.get("date") or ""),
        "start_time_from": str(b.get("start_time") or ""),
        "start_time_to": str(a.get("start_time") or ""),
        "end_time_from": str(b.get("end_time") or ""),
        "end_time_to": str(a.get("end_time") or ""),
        "staff1_from": str(b.get("staff1") or ""),
        "staff1_to": str(a.get("staff1") or ""),
        "staff2_from": str(b.get("staff2") or ""),
        "staff2_to": str(a.get("staff2") or ""),
        "service_type": pick("service_type"),
        "action": action,
        "business_type": pick("business_type"),
        "remarks": pick("remarks"),
    }
