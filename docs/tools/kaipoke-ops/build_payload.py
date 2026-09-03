import asyncio, json, sys
from sqlalchemy import select
from app.db.session import get_session_factory
from app.models.correction_sheet import CorrectionSheetItem
from app.services.kaipoke.local_diff import item_to_kaipoke_correction
SHEET = "3012f86a-d1c8-48eb-9311-2e44fea771cb"
async def main():
    f = get_session_factory()
    async with f() as db:
        rows = (await db.scalars(select(CorrectionSheetItem).where(CorrectionSheetItem.sheet_id == SHEET))).all()
    out, skipped = [], []
    for it in rows:
        c = item_to_kaipoke_correction(it.action, it.before, it.after)
        if it.action != "delete" and c["staff1_to"].strip() in ("", "-"):
            skipped.append(c); continue
        out.append(c)
    def k(c): return (int(c["date_from"] or c["date_to"] or 0), c["user_name"])
    out.sort(key=k)
    json.dump({"month": "2026-09", "dry_run": True, "headed": True, "correction_data": out}, open("/tmp/dryrun_payload.json","w"), ensure_ascii=False, indent=1)
    print("sendable", len(out), "skipped_unassigned", len(skipped))
    for c in out: print(c["action"], c["date_from"] or c["date_to"], c["user_name"], c["start_time_from"], "->", c["start_time_to"], c["staff1_from"], "->", c["staff1_to"])
asyncio.run(main())
