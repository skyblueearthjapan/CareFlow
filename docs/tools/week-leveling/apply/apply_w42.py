"""週42 ならし案の反映 (サーバー上で実行)。アプリの API だけを使う。最初の想定外の応答で止まる。
使い方: W42_TOKEN=... python3 apply_w42.py ops.json log.jsonl [--from N]
戻すとき: backups/w42-restore-20261004-2332.sql を psql で流す。"""
import json, subprocess, sys, urllib.error, urllib.request, uuid, os

BASE = "http://127.0.0.1:18001/api/v1"
TOKEN = os.environ["W42_TOKEN"]
OPS = json.load(open(sys.argv[1], encoding="utf-8"))["ops"]
LOG = open(sys.argv[2], "a", encoding="utf-8")
START = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[3] == "--from" else 0
GROUP = str(uuid.uuid5(uuid.NAMESPACE_URL, "careflow-w42-leveling-2026-10-04"))


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:800]


def courses_w42():
    out = subprocess.run(
        ["docker", "exec", "-i", "carelink-postgres", "psql", "-U", "carelink", "-d", "carelink", "-At", "-F", "|", "-c",
         "select c.id, o.short_label||c.code, c.weekday from courses c join offices o on o.id=c.office_id "
         "where c.iso_year=2026 and c.iso_week=42 and c.deleted_at is null"],
        capture_output=True, text=True, check=True).stdout
    m = {}
    for ln in out.strip().splitlines():
        cid, code, wd = ln.split("|")
        if (code, int(wd)) in m:
            raise SystemExit(f"同じコース・曜日が 2 つあります: {code} {wd}")
        m[(code, int(wd))] = cid
    return m


cmap = None
for i, op in enumerate(OPS):
    if i < START:
        continue
    k = op["kind"]
    if k == "cancel":
        st, res = call("POST", "/schedule/v2/visit-cancel-week",
                       {"visit_id": op["visit_id"], "cancel": True, "op_group_id": GROUP,
                        "reason": "週42 ならし: マスターに無い古い時刻の二重"})
        ok = st == 200
    elif k == "move":
        st, res = call("POST", "/schedule/v2/visit-move-week-only", {**op["body"], "op_group_id": GROUP})
        ok = st == 200 and isinstance(res, dict) and res.get("visits_moved") == 1
    elif k == "place":
        st, res = call("POST", f"/special-visit-marks/{op['mark_id']}/place", op["body"])
        ok = st in (200, 201)
    elif k == "course_staff":
        if cmap is None:
            cmap = courses_w42()
        cid = cmap.get((op["course"], op["weekday"]))
        if cid is None:
            st, res, ok = 0, f"コースが見つかりません {op['course']} {op['weekday']}", False
        else:
            st, res = call("PATCH", f"/courses/{cid}", {"assigned_staff_id": op["staff_id"], "op_group_id": GROUP})
            ok = st == 200
    elif k == "delete_mark":
        st, res = call("DELETE", f"/special-visit-marks/{op['mark_id']}")
        ok = st == 204
    else:
        raise SystemExit(f"unknown op {k}")
    LOG.write(json.dumps({"i": i, "kind": k, "label": op["label"], "status": st, "ok": ok,
                          "res": res if not ok else None}, ensure_ascii=False) + "\n")
    LOG.flush()
    print(f"{i:3d} {'OK ' if ok else 'NG '} {st} {k} {op['label']}", flush=True)
    if not ok:
        print("止めました:", str(res)[:800])
        sys.exit(2)
print("全部終わりました", len(OPS))
