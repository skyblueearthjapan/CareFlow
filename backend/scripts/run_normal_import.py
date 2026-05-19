"""通常 import (partial commit) helper.

VPS の backend container 内で実行:

    docker exec -e PYTHONPATH=/app carelink-backend \\
        python /opt/run_normal_import.py --file /opt/_user_0519_weekly_update.xlsx [--apply]

通常 import 仕様:
  - patient master シートに行がある patient のみ INSERT/UPDATE
  - シートに無い patient は維持
  - 同様に weekly_pattern / PFV シート
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


async def run(file_path: Path, apply: bool) -> None:
    from app.db.session import get_session_factory
    from app.services.patient_excel.importer import apply_changes, parse_and_diff

    content = file_path.read_bytes()
    factory = get_session_factory()
    async with factory() as db:
        try:
            patient_rows, pfv_rows, summary, patient_ops, pfv_ops = await parse_and_diff(
                db, file_bytes=content
            )
        except Exception as e:
            print(f"PARSE_ERROR: {type(e).__name__}: {e}", file=sys.stderr)
            import traceback

            traceback.print_exc()
            sys.exit(2)

        print("=" * 60)
        print(f"target=patient (normal/partial import) apply={apply}")
        print(f"summary: {summary}")
        print(f"patient rows parsed: {len(patient_rows)}")
        print(f"pfv rows parsed: {len(pfv_rows)}")
        print(f"patient ops: {len(patient_ops)}")
        print(f"pfv ops: {len(pfv_ops)}")

        # operation 別カウント
        from collections import Counter

        p_op_count = Counter(op.get("_op", "?") for op in patient_ops)
        print(f"  patient by op: {dict(p_op_count)}")
        p_row_op = Counter(getattr(r, "operation", "?") for r in patient_rows)
        print(f"  patient_rows op: {dict(p_row_op)}")

        # error 行
        err_count = 0
        for r in patient_rows:
            if getattr(r, "operation", None) == "error":
                err_count += 1
                if err_count <= 20:
                    print(f"  ! {r}")
        print(f"  total patient errors: {err_count}")

        for r in pfv_rows:
            if getattr(r, "operation", None) == "error":
                print(f"  ! pfv {r}")

        print("=" * 60)

        if apply:
            try:
                await apply_changes(db, patient_ops=patient_ops, pfv_ops=pfv_ops)
                await db.commit()
                print(">>> APPLIED (db commit done) <<<")
            except Exception as e:
                await db.rollback()
                print(f"APPLY_ERROR: {type(e).__name__}: {e}", file=sys.stderr)
                import traceback

                traceback.print_exc()
                sys.exit(3)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=Path, required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    asyncio.run(run(args.file, args.apply))


if __name__ == "__main__":
    main()
