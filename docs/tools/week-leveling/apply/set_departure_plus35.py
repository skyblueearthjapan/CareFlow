"""退出の打刻が無いまま「実施中」で残った過去の訪問に、退出 = 到着の実績 + N 分 を入れる (サーバー上で実行)。
アプリの「実績の時刻を合わせる」API (PUT /visits/{id}/actual-time) だけを使う。戻すときは DELETE (?kind=departure)。
使い方: TOKEN=... python3 set_departure_plus35.py ids.txt [--apply] [--minutes 35] [--reset]
  ids.txt = 訪問 ID を 1 行に 1 つ。--apply が無ければ読むだけ (入れる時刻の一覧を出す)。--reset は入れた退出を取り消す。
2026-10-06 PO 指示: 熊澤さんほか 11 件を「とりあえず 35 分」。"""
import json, os, sys, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone

BASE = "http://127.0.0.1:18001/api/v1"
TOKEN = os.environ["TOKEN"]
JST = timezone(timedelta(hours=9))
args = sys.argv[1:]
ids = [x.strip() for x in open(args[0]) if x.strip()]
APPLY, RESET = "--apply" in args, "--reset" in args
MIN = int(args[args.index("--minutes") + 1]) if "--minutes" in args else 35


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                                          "X-Client-Surface": "pc"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]


def jst(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(JST) if s else None


for vid in ids:
    st, v = call("GET", f"/visits/{vid}")
    if st != 200:
        print(vid[:8], "読めない", st, v); continue
    arr, dep = jst(v.get("actual_arrival_at")), jst(v.get("actual_departure_at"))
    label = f"{v['visit_date']} {v['start_time'][:5]} status={v['status']} 到着={arr:%H:%M}" if arr else f"{v['visit_date']} 到着なし"
    if RESET:
        st, res = call("DELETE", f"/visits/{vid}/actual-time?kind=departure")
        print(vid[:8], label, "取消", st, (res or {}).get("status") if isinstance(res, dict) else res); continue
    if arr is None:
        print(vid[:8], label, "→ 到着が無いので飛ばす"); continue
    if dep is not None:
        print(vid[:8], label, f"→ 既に退出 {dep:%H:%M} があるので飛ばす"); continue
    t = (arr.replace(second=0, microsecond=0) + timedelta(minutes=MIN)).strftime("%H:%M")
    if not APPLY:
        print(vid[:8], label, f"→ 退出 {t} を入れる予定"); continue
    st, res = call("PUT", f"/visits/{vid}/actual-time", {"kind": "departure", "time": t})
    ok = st == 200 and isinstance(res, dict)
    print(vid[:8], label, f"→ 退出 {t}", "OK" if ok else "NG", st,
          (res.get("status"), jst(res.get("actual_departure_at")).strftime("%H:%M") if res.get("actual_departure_at") else None) if ok else res)
