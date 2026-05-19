"""VPS 内 (container) で xlsx を読み込み、CareFlow の replace_all (dry_run) を実行.

入力:
  - --target {patient,staff}: どのマスタを処理するか
  - --file PATH: xlsx file (container 内のパス)
  - --apply: 指定時は本実行 (デフォルト dry_run)

出力:
  - 標準出力に summary (件数、エラー、警告)

使い方:
  docker exec -e PYTHONPATH=/app carelink-backend python /opt/import_helper.py \\
    --target patient --file /opt/_user_master_patient.xlsx
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


async def run(target: str, file_path: Path, apply: bool) -> None:
    from app.db.session import get_session_factory

    if target == "patient":
        from app.services.patient_excel.replace_all import parse_and_diff_replace_all
    elif target == "staff":
        from app.services.staff_excel.replace_all import parse_and_diff_replace_all
    else:
        print(f"ERROR: unknown target {target}", file=sys.stderr)
        sys.exit(1)

    content = file_path.read_bytes()
    session_factory = get_session_factory()
    async with session_factory() as db:
        try:
            result_tuple = await parse_and_diff_replace_all(db, file_bytes=content)
        except Exception as e:
            print(f"PARSE_ERROR: {type(e).__name__}: {e}", file=sys.stderr)
            import traceback

            traceback.print_exc()
            sys.exit(2)
        # patient: (patient_rows, pfv_rows, summary, patient_ops, pfv_ops, soft_delete_preview)
        # staff: similar
        if target == "patient":
            (patient_rows, pfv_rows, summary, patient_ops, pfv_ops, soft_delete_preview) = (
                result_tuple
            )
            print("=" * 60)
            print(f"target=patient apply={apply}")
            print(f"summary: {summary}")
            print(f"patient rows parsed: {len(patient_rows)}")
            print(f"pfv rows parsed: {len(pfv_rows)}")
            print(f"patient apply ops: {len(patient_ops)}")
            print(f"pfv apply ops: {len(pfv_ops)}")
            print(f"patients to soft-delete: {len(soft_delete_preview)}")
            # patient errors
            print("\n=== PATIENT ERRORS ===")
            err_count = 0
            for r in patient_rows:
                if hasattr(r, "operation") and r.operation == "error":
                    err_count += 1
                    if err_count <= 15:
                        print(f"  ! {r}")
            print(f"  total patient errors: {err_count}")
            # pfv errors
            print("\n=== PFV ERRORS ===")
            err_count = 0
            for r in pfv_rows:
                if hasattr(r, "operation") and r.operation == "error":
                    err_count += 1
                    if err_count <= 15:
                        print(f"  ! {r}")
            print(f"  total pfv errors: {err_count}")
            print("=" * 60)
        else:
            # staff: similar tuple
            print("=" * 60)
            print(f"target=staff apply={apply}")
            print(f"raw result: {len(result_tuple)} elements")
            for i, item in enumerate(result_tuple):
                if hasattr(item, "__len__"):
                    print(f"  [{i}] len={len(item)} type={type(item).__name__}")
                else:
                    print(f"  [{i}] {item}")
            print("=" * 60)

        if apply:
            try:
                if target == "patient":
                    from uuid import UUID

                    from app.services.patient_excel.replace_all import apply_replace_all

                    soft_delete_ids = [UUID(sd["patient_id"]) for sd in soft_delete_preview]
                    await apply_replace_all(
                        db,
                        patient_ops=patient_ops,
                        pfv_ops=pfv_ops,
                        patient_ids_to_soft_delete=soft_delete_ids,
                    )
                elif target == "staff":
                    from app.services.staff_excel.replace_all import apply_replace_all

                    # staff の apply 引数構造は別途確認必要
                    print("WARN: staff apply not yet supported in helper")
                    return
                await db.commit()
                print(">>> APPLIED (db commit done) <<<")
            except Exception as e:
                await db.rollback()
                print(f"APPLY_ERROR: {type(e).__name__}: {e}", file=sys.stderr)
                import traceback

                traceback.print_exc()
                sys.exit(3)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["patient", "staff"], required=True)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Actually apply (default: dry_run)")
    args = parser.parse_args()
    asyncio.run(run(args.target, args.file, args.apply))


if __name__ == "__main__":
    main()
