"""偽の claude（試験用）: 取った stream-json を 1 ターンずつ吐く。stdin の user 行でターンを始め、
can_use_tool では control_response を待つ。interrupt を受けたら result(aborted) を返す。stdin が閉じたら終わる。"""
import json
import sys

lines = [json.loads(x) for x in open(sys.argv[1], encoding="utf-8") if x.strip()]
turns, cur = [], []
for m in lines:
    cur.append(m)
    if m.get("type") == "result":
        turns.append(cur)
        cur = []
out = sys.stdout
received = open(sys.argv[2], "w", encoding="utf-8") if len(sys.argv) > 2 else None


def read():
    ln = sys.stdin.readline()
    if not ln:
        sys.exit(0)
    if received:
        received.write(ln)
        received.flush()
    return json.loads(ln)


for turn in turns:
    while read().get("type") != "user":
        pass
    for m in turn:
        out.write(json.dumps(m, ensure_ascii=False) + "\n")
        out.flush()
        if m.get("type") == "control_request":
            r = read()
            assert r.get("type") == "control_response", r
while True:
    read()
