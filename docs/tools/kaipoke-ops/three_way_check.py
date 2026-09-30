"""カイポケ ⇔ らく助 ⇔ 訪問モニター の三者照合（読み取り専用）。

2026-09-24 の「訪問モニターがぐちゃっている」調査で使ったもの。取込時に保存された
カイポケ CSV（kaipoke_csv_snapshots）と、らく助の有効訪問、build_monitor の出力を
1 件ずつ突き合わせる。DB へは書き込まず、最後に rollback する。

照合キー: (日付, 利用者, 開始, 終了, 職員1, {職員2・3}) — 氏名は normalize_name_key。
らく助側の職員2 = 同行 (accompaniments) ∪ secondary_staff_id（2 名体制）。
secondary を落とすと 2 名体制の訪問が「不一致」に見えるので注意。

使い方（本番。ファイルをコンテナに置かず stdin で流す）:
    ssh root@72.60.211.213 'docker exec -i -w /app -e PYTHONIOENCODING=utf-8 \
        carelink-backend python - 2026-09-21' < docs/tools/kaipoke-ops/three_way_check.py

引数: 週の月曜 (YYYY-MM-DD)。その週の最新 plan スナップショットを使う。
注意: スナップショットは「最後に取込/プレビューした時点」のカイポケ。今この瞬間の
カイポケと照合したい場合は、先に取込プレビューで CSV を取り直すこと。
"""

import asyncio
import csv
import io
import sys
from collections import Counter
from datetime import date, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.db.session import get_session_factory
from app.models.staff import Staff
from app.models.visit import VISIT_STATUS_CANCELLED, Visit
from app.services.accompaniment import resolve_accompaniment_by_visit
from app.services.checkin.monitor import build_monitor
from app.services.kaipoke.name_match import normalize_name_key as K


async def main(ws: date) -> None:
    async with get_session_factory()() as db:
        snap = (
            await db.execute(
                text(
                    "SELECT csv_text, to_char(fetched_at AT TIME ZONE 'Asia/Tokyo','MM-DD HH24:MI') "
                    "FROM kaipoke_csv_snapshots WHERE week_start=:w AND division='plan' "
                    "ORDER BY fetched_at DESC LIMIT 1"
                ),
                {"w": ws},
            )
        ).first()
        if snap is None:
            print(f"週 {ws} のカイポケ CSV スナップショットがありません")
            return
        csvt, fetched = snap
        staff_name = {s.id: s.name for s in (await db.scalars(select(Staff))).all()}
        week = [ws + timedelta(days=i) for i in range(7)]

        kp: Counter = Counter()
        kd: dict = {}
        for r in csv.DictReader(io.StringIO(csvt)):
            dt = next((c for c in week if c.day == int(r["日付"])), None)
            if dt is None:
                print("  日付を解決できない行:", r)
                continue
            s2 = frozenset(K(x) for x in (r["職員名２"], r["職員名３"]) if x)
            key = (dt, K(r["利用者"]), r["開始時間"], r["終了時間"], K(r["職員名１"]), s2)
            kp[key] += 1
            kd[key] = r["利用者"]

        visits = (
            await db.scalars(
                select(Visit)
                .where(
                    Visit.visit_date >= ws,
                    Visit.visit_date < ws + timedelta(days=7),
                    Visit.deleted_at.is_(None),
                    Visit.status != VISIT_STATUS_CANCELLED,
                )
                .options(selectinload(Visit.patient))
            )
        ).all()
        acc = await resolve_accompaniment_by_visit(db, list(visits))
        rk: Counter = Counter()
        rd: dict = {}
        for v in visits:
            s2 = {K(e.staff_name) for e in acc.get(v.id, []) if e.staff_name}
            if v.secondary_staff_id:
                s2.add(K(staff_name.get(v.secondary_staff_id, "")))
            key = (
                v.visit_date,
                K(v.patient.name if v.patient else ""),
                v.start_time.strftime("%H:%M"),
                v.end_time.strftime("%H:%M"),
                K(staff_name.get(v.primary_staff_id, "")),
                frozenset(s2),
            )
            rk[key] += 1
            rd[key] = (v.patient.name if v.patient else "?", v.is_unplanned, v.source)

        print(f"週 {ws}（カイポケ CSV 取得 {fetched}）")
        print(
            f"【1】カイポケ⇔らく助: カイポケ {sum(kp.values())} / らく助 {sum(rk.values())} "
            f"/ 完全一致 {sum((kp & rk).values())}"
        )
        for k in sorted(kp - rk):
            print(f"   カイポケのみ: {k[0]} {k[2]}-{k[3]} {kd[k]} 職員1={k[4]} 職員2={set(k[5]) or '-'}")
        for k in sorted(rk - kp):
            name, unplanned, src = rd[k]
            print(
                f"   らく助のみ : {k[0]} {k[2]}-{k[3]} {name} 職員1={k[4]} 職員2={set(k[5]) or '-'} "
                f"予定外={unplanned} source={src}"
            )

        by_id = {v.id: v for v in visits}
        shown: Counter = Counter()
        diffs: list[str] = []
        for d in week:
            m = await build_monitor(db, d)
            for row in m.staff:
                for mv in row.visits:
                    shown[mv.visit_id] += 1
                    v = by_id.get(mv.visit_id)
                    if v is None:
                        diffs.append(f"モニターのみ {d} {mv.patient_name}")
                        continue
                    exp = (
                        v.patient.name,
                        v.start_time.strftime("%H:%M"),
                        v.end_time.strftime("%H:%M"),
                        staff_name.get(v.primary_staff_id),
                    )
                    got = (mv.patient_name, mv.start_time, mv.end_time, mv.staff_name)
                    if exp != got:
                        diffs.append(f"内容差 {d} {exp} → {got}")
        missing = [v for v in visits if shown[v.id] == 0]
        dup = [vid for vid, c in shown.items() if c > 1]
        print(
            f"【2】らく助⇔モニター: らく助 {len(visits)} / モニター {sum(shown.values())} "
            f"/ 未表示 {len(missing)} / 重複 {len(dup)} / 内容差 {len(diffs)}"
        )
        for v in missing:
            print(f"   モニター未表示: {v.visit_date} {v.patient.name} {v.start_time}")
        for x in diffs:
            print("   ", x)
        await db.rollback()


if __name__ == "__main__":
    asyncio.run(main(date.fromisoformat(sys.argv[1])))
