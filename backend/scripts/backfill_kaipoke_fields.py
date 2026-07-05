"""K-1c: カイポケ18列CSV生成用フィールドの backfill (dry-run 既定).

実カイポケCSV (18列) を突合キーに、CareFlow マスタへ以下を投入する:
  * office.kaipoke_name — code(INAGE/TSUGA) → 正式事業所名
  * staff.qualification — CSV「職員名×職種」を name_match で突合して設定

**既定は dry-run** (何も書かない・投入予定を表示するのみ)。実投入は ``--apply``。
突合できなかったスタッフは「要手当」として列挙する。

使い方:
    python -m scripts.backfill_kaipoke_fields --csv data/current_202607.csv          # 検算(dry-run)
    python -m scripts.backfill_kaipoke_fields --csv data/current_202607.csv --apply  # 投入

サービス内容 (patient.kaipoke_service_content) は NULL のとき事業所既定へフォールバック
するため、精神科訪問看護の均質運用では投入不要 (別療養費/介護保険の患者だけ後日個別設定)。
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from pathlib import Path

from sqlalchemy import select

from app.db.session import get_session_factory
from app.models.office import Office
from app.models.staff import Staff
from app.schemas.v2.staff import QualificationV2
from app.services.kaipoke.name_match import build_name_index, match_name

# code → カイポケ正式事業所名 (実 export 実測)。
OFFICE_KAIPOKE_NAMES = {
    "INAGE": "訪問看護ステーションよりより",
    "TSUGA": "(ST)訪問看護ステーションよりより　都賀支店",
}

# 許可職種 (schemas/v2/staff.QualificationV2 と一致)。未知値は DB に入れると
# StaffV2Read(from_attributes) が 500 になるため投入前に弾く。
VALID_QUALIFICATIONS = set(QualificationV2.__args__)


def _read_staff_qualifications(csv_path: Path) -> dict[str, str]:
    """CSV から {職員名: 職種} を抽出 (職員1/2/3 列を走査・最初に見た職種を採用)。"""
    out: dict[str, str] = {}
    with csv_path.open(encoding="cp932") as f:
        rows = list(csv.reader(f))
    for r in rows[1:]:
        for name_idx, type_idx in ((0, 1), (2, 3), (5, 6)):
            if len(r) > type_idx and r[name_idx].strip() and r[type_idx].strip():
                out.setdefault(r[name_idx].strip(), r[type_idx].strip())
    return out


async def _run(csv_path: Path, apply: bool) -> None:
    name_to_qual = _read_staff_qualifications(csv_path)
    print(f"CSV から職種を抽出: {len(name_to_qual)} 名分")

    factory = get_session_factory()
    async with factory() as db:
        # --- offices ---
        offices = (await db.scalars(select(Office).where(Office.deleted_at.is_(None)))).all()
        print("\n[事業所名]")
        for o in offices:
            target = OFFICE_KAIPOKE_NAMES.get((o.code or "").upper())
            if not target:
                print(f"  - {o.code}/{o.name}: マップ無し (スキップ)")
                continue
            if o.kaipoke_name == target:
                print(f"  = {o.code}: 既に「{target}」")
                continue
            print(f"  {'✔' if apply else '→'} {o.code}: kaipoke_name = 「{target}」")
            if apply:
                o.kaipoke_name = target

        # --- staff qualifications ---
        staff = (await db.scalars(select(Staff).where(Staff.deleted_at.is_(None)))).all()
        index = build_name_index({str(s.id): s.name for s in staff})
        by_id = {str(s.id): s for s in staff}

        matched, unmatched_csv, ambiguous, invalid_qual = 0, [], [], []
        print("\n[スタッフ職種]")
        for kai_name, qual in name_to_qual.items():
            if qual not in VALID_QUALIFICATIONS:
                invalid_qual.append(f"{kai_name}({qual})")
                continue
            sid = match_name(kai_name, index)
            if sid is None:
                # 曖昧/不一致の区別: index にキーはあるが複数当たり = ambiguous。
                from app.services.kaipoke.name_match import normalize_name_key

                hits = index.get(normalize_name_key(kai_name))
                (ambiguous if hits else unmatched_csv).append(kai_name)
                continue
            s = by_id[sid]
            matched += 1
            if s.qualification == qual:
                continue
            print(f"  {'✔' if apply else '→'} {s.name}: qualification = 「{qual}」")
            if apply:
                s.qualification = qual

        if apply:
            await db.commit()
            print("\n>>> 投入完了 (commit)")
        else:
            print("\n>>> dry-run (未投入)。--apply で実投入")

        print(
            f"\n突合: {matched} 名一致 / 不一致 {len(unmatched_csv)} / 曖昧 {len(ambiguous)}"
            f" / 未知職種 {len(invalid_qual)}"
        )
        if unmatched_csv:
            print("  [要手当・CareFlow に見つからない]:", "、".join(unmatched_csv))
        if ambiguous:
            print("  [要手当・同名複数]:", "、".join(ambiguous))
        if invalid_qual:
            print("  [要手当・許可外の職種]:", "、".join(invalid_qual))


def main() -> None:
    ap = argparse.ArgumentParser(description="カイポケ18列CSV用フィールドの backfill")
    ap.add_argument("--csv", required=True, help="カイポケ18列CSV (cp932)")
    ap.add_argument("--apply", action="store_true", help="実投入 (既定は dry-run)")
    args = ap.parse_args()
    asyncio.run(_run(Path(args.csv), args.apply))


if __name__ == "__main__":
    main()
