"""音声記録テスト用データ投入（今日だけ）。S009 を稼働中に戻し、架空患者 8 名と今日の訪問 6 件を作る。
本番の実データには触れない（新規作成のみ・コース割当なし）。出力 = manifest JSON（後片付け用）。"""
import asyncio, json, sys
from datetime import date
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from app.core.security import create_access_token
from app.db.session import get_session_factory
from app.main import app
from app.models import User

EMAIL = "yuji.imaizumi@thousands.jp"
S009 = "9361eb66-ea2a-4dbc-9557-a984c21c60d8"
INAGE = "afa59a99-59de-466d-8c20-01e749abe02f"
TODAY = date.fromisoformat(sys.argv[1])
PATIENTS = [
    ("【検証】佐藤 花子", "さとう はなこ", "female", 35.6355, 140.1150),
    ("【検証】鈴木 一郎", "すずき いちろう", "male", 35.6372, 140.1188),
    ("【検証】高橋 幸子", "たかはし さちこ", "female", 35.6340, 140.1201),
    ("【検証】田中 健二", "たなか けんじ", "male", 35.6398, 140.1132),
    ("【検証】伊藤 美咲", "いとう みさき", "female", 35.6321, 140.1165),
    ("【検証】渡辺 正雄", "わたなべ まさお", "male", 35.6366, 140.1224),
    ("【検証】山本 律子", "やまもと りつこ", "female", 35.6384, 140.1176),
    ("【検証】中村 大輔", "なかむら だいすけ", "male", 35.6331, 140.1143),
]
SLOTS = [("09:00", "09:30"), ("10:00", "10:30"), ("11:00", "11:30"), ("13:00", "13:30"), ("14:00", "14:30"), ("15:00", "15:30")]

async def main():
    factory = get_session_factory()
    async with factory() as db:
        u = await db.scalar(select(User).where(User.email == EMAIL))
        tok = create_access_token(subject=u.id, role=u.role, staff_id=u.staff_id)
    h = {"Authorization": "Bearer " + tok, "Content-Type": "application/json"}
    man = {"date": TODAY.isoformat(), "staff_id": S009, "patients": [], "visits": []}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        r = await c.get(f"/api/v1/staff/{S009}", headers=h)
        man["staff_before"] = {k: r.json().get(k) for k in ("status", "primary_office_id", "qualification")}
        r = await c.patch(f"/api/v1/staff/{S009}", headers=h,
                          content=json.dumps({"status": "active", "primary_office_id": INAGE, "qualification": "看護師"}))
        assert r.status_code == 200, ("staff", r.status_code, r.text)
        for i, (name, kana, sex, lat, lng) in enumerate(PATIENTS):
            body = {"code": "", "name": name, "kana": kana, "sex": sex, "status": "active",
                    "address": "千葉県千葉市稲毛区小仲台（検証用・架空）", "lat": lat, "lng": lng,
                    "primary_office_id": INAGE}
            r = await c.post("/api/v1/patients", headers=h, content=json.dumps(body))
            if r.status_code == 422:
                body.pop("code"); r = await c.post("/api/v1/patients", headers=h, content=json.dumps(body))
            assert r.status_code == 201, ("patient", r.status_code, r.text)
            p = r.json(); man["patients"].append({"id": p["id"], "code": p["code"], "name": name})
            if i < len(SLOTS):
                st, en = SLOTS[i]
                v = {"patient_id": p["id"], "primary_staff_id": S009, "visit_date": TODAY.isoformat(),
                     "start_time": st, "end_time": en, "type": "regular", "status": "planned",
                     "source": "manual", "note": "【検証】音声記録テスト（カイポケ送信対象外）"}
                r = await c.post("/api/v1/visits", headers=h, content=json.dumps(v))
                assert r.status_code == 201, ("visit", r.status_code, r.text)
                man["visits"].append({"id": r.json()["id"], "patient": name, "time": f"{st}-{en}"})
    print(json.dumps(man, ensure_ascii=False, indent=1))

asyncio.run(main())
