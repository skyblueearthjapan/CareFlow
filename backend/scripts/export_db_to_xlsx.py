"""Phase F-2: 現在の DB の patient + PFV + weekly_pattern を Excel に export.

VPS の backend container 内で実行する想定:

    docker exec -e PYTHONPATH=/app carelink-backend \\
        python /opt/export_db_to_xlsx.py --out /opt/_current_db_export.xlsx

build_workbook をそのまま呼んで file に書き出す.
auth 不要 (container 内で直接 db 接続).
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path


async def run(out: Path) -> None:
    from sqlalchemy import select

    from app.db.session import get_session_factory
    from app.models.course_template import CourseTemplate
    from app.models.office import Office
    from app.models.patient import Patient
    from app.models.patient_fixed_visit import PatientFixedVisit
    from app.services.patient_excel.exporter import build_workbook, workbook_to_bytes

    factory = get_session_factory()
    async with factory() as db:
        patients = (
            await db.scalars(
                select(Patient).where(Patient.deleted_at.is_(None)).order_by(Patient.code)
            )
        ).all()
        pids = [p.id for p in patients]
        pfvs = []
        if pids:
            pfvs = (
                await db.scalars(
                    select(PatientFixedVisit)
                    .where(PatientFixedVisit.patient_id.in_(pids))
                    .order_by(
                        PatientFixedVisit.patient_id,
                        PatientFixedVisit.mode,
                        PatientFixedVisit.weekday,
                        PatientFixedVisit.slot_index,
                    )
                )
            ).all()
        offices = (await db.scalars(select(Office).where(Office.deleted_at.is_(None)))).all()
        course_templates = (
            await db.scalars(select(CourseTemplate).where(CourseTemplate.deleted_at.is_(None)))
        ).all()

        warnings: list[str] = []
        wb = build_workbook(
            patients=list(patients),
            pfvs=list(pfvs),
            offices=list(offices),
            course_templates=list(course_templates),
            crossoffice_warnings_out=warnings,
        )
        content = workbook_to_bytes(wb)
        out.write_bytes(content)
        print(f"patients: {len(patients)}")
        print(f"pfvs: {len(pfvs)}")
        print(f"offices: {len(offices)}")
        print(f"course_templates: {len(course_templates)}")
        print(f"crossoffice warnings: {len(warnings)}")
        print(f"OUT: {out} ({out.stat().st_size:,} bytes)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    asyncio.run(run(args.out))


if __name__ == "__main__":
    main()
