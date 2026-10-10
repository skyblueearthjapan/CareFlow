"""RPA の送信の進み具合を見張る。10 件ごと・失敗が増えたとき・終わったときに 1 行ずつ出す。"""
import json
import subprocess
import time
import urllib.request

tok = subprocess.run(["docker", "exec", "carelink-backend", "printenv", "KAIPOKE_API_TOKEN"],
                     capture_output=True, text=True).stdout.strip()


def get():
    req = urllib.request.Request("http://127.0.0.1:5000/api/apply/result", headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def now():
    return time.strftime("%H:%M")


last_step, last_failed, last_status = -1, -1, None
while True:
    try:
        d = get()
    except Exception as e:  # 一時的な失敗では止めない
        print(now(), "見張りの読み取りに失敗:", str(e)[:80], flush=True)
        time.sleep(120)
        continue
    p = d.get("progress") or {}
    st = d.get("status")
    n, tot = p.get("processed", 0), p.get("total", 0)
    ok, ng, sk = p.get("success", 0), p.get("failed", 0), p.get("skipped", 0)
    name = p.get("current_name", "")
    if n // 10 != last_step or ng != last_failed or st != last_status:
        extra = ""
        if last_failed >= 0 and ng > last_failed:
            extra = " ★失敗が増えました（" + name + " 付近）"
        print(f"{now()} {st} {n}/{tot} 件済み（成功 {ok}・失敗 {ng}・飛ばし {sk}）処理中: {name}{extra}", flush=True)
        last_step, last_failed, last_status = n // 10, ng, st
    if st not in ("running", None):
        print(now(), "終了:", json.dumps({k: d.get(k) for k in ("status", "message")}, ensure_ascii=False)[:200], flush=True)
        break
    time.sleep(60)
