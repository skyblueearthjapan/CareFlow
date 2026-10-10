"""ならし案の反映 (サーバー上で実行・週は ops.json の iso_year / iso_week)。アプリの API だけを使う。最初の想定外の応答で止まる。
使い方: W_TOKEN=... python3 apply.py ops.json log.jsonl [--from N]
戻すとき: その週の w-restore-<日時>.sql を psql で流す。"""
import json, os, subprocess, sys, urllib.error, urllib.request, uuid

BASE = "http://127.0.0.1:18001/api/v1"
TOKEN = os.environ["W_TOKEN"]
DOC = json.load(open(sys.argv[1], encoding="utf-8"))
OPS, Y, W = DOC["ops"], DOC["iso_year"], DOC["iso_week"]
LOG = open(sys.argv[2], "a", encoding="utf-8")
START = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[3] == "--from" else 0
GROUP = str(uuid.uuid5(uuid.NAMESPACE_URL, f"careflow-leveling-{Y}-W{W}-2026-10-09"))
POOL_GROUP = str(uuid.uuid5(uuid.NAMESPACE_URL, f"careflow-leveling-pool-{Y}-W{W}-2026-10-09"))


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:800]


def courses_week():
    out = subprocess.run(
        ["docker", "exec", "-i", "carelink-postgres", "psql", "-U", "carelink", "-d", "carelink", "-At", "-F", "|", "-c",
         "select c.id, o.short_label||c.code, c.weekday from courses c join offices o on o.id=c.office_id "
         f"where c.iso_year={int(Y)} and c.iso_week={int(W)} and c.deleted_at is null"],
        capture_output=True, text=True, check=True).stdout
    m = {}
    for ln in out.strip().splitlines():
        cid, code, wd = ln.split("|")
        if (code, int(wd)) in m:
            raise SystemExit(f"同じコース・曜日が 2 つあります: {code} {wd}")
        m[(code, int(wd))] = cid
    return m


cmap = courses_week()
reloaded = False
for i, op in enumerate(OPS):
    if i < START:
        continue
    if op["step"] >= 4 and not reloaded:  # 置く・移すでコースが増えていても拾えるよう、担当付けの前に読み直す
        cmap, reloaded = courses_week(), True
    k = op["kind"]
    if k == "to_pool":
        st, res = call("DELETE", f"/visits/{op['visit_id']}?op_group_id={POOL_GROUP}&cascade_partner=false")
        ok = st == 204
    elif k == "move":
        st, res = call("POST", "/schedule/v2/visit-move-week-only", {**op["body"], "op_group_id": GROUP})
        ok = st == 200 and (res or {}).get("visits_moved", 0) >= 1
    elif k == "place":
        st, res = call("POST", f"/special-visit-marks/{op['mark_id']}/place", op["body"])
        ok = st in (200, 201)
    elif k == "course_staff":
        cid = cmap.get((op["course"], op["weekday"]))
        if cid is None:
            st, res, ok = 0, f"コースが見つかりません {op['course']} {op['weekday']}", False
        else:
            st, res = call("PATCH", f"/courses/{cid}", {"assigned_staff_id": op["staff_id"], "op_group_id": GROUP})
            ok = st == 200
    else:
        raise SystemExit(f"unknown op {k}")
    LOG.write(json.dumps({"i": i, "kind": k, "label": op["label"], "status": st, "ok": ok,
                          "res": res if not ok else None}, ensure_ascii=False) + "\n")
    LOG.flush()
    print(f"{i:3d} {'OK ' if ok else 'NG '} {st} {k} {op['label']}", flush=True)
    if not ok:
        print(res)
        raise SystemExit(f"止めました (続きは --from {i})")
print("完了")
