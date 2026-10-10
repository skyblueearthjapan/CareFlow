"""verify.sql の出力 (標準入力) と expect.txt を突き合わせる。作り直した 5 名 (visit_id 空) は 利用者・日 で引く。"""
import sys

exp = [l.rstrip("\n").split("|") for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
got, other = {}, []
for l in sys.stdin:
    p = l.strip().split("|")
    if p[0] == "V":
        got[p[1]] = p[1:]  # id, patient, date, start, staff, course
    elif p[0] not in ("BEGIN", "ROLLBACK", ""):
        other.append(p)
by_pd = {}
for g in got.values():
    by_pd.setdefault((g[1], g[2]), []).append(g)
bad, used = [], set()
for vid, pid, d, t, s, c in exp:
    if vid:
        g = got.get(vid)
    else:
        lst = by_pd.get((pid, d), [])
        g = lst[0] if len(lst) == 1 else None
    if g:
        used.add(g[0])
    if not g or g[2:] != [d, t, s, c]:
        bad.append((vid or pid, [d, t, s, c], g[2:] if g else None))
extra = [g for k, g in got.items() if k not in used and g[2] != "2026-10-17"]
print("一致", len(exp) - len(bad), "/", len(exp), "食い違い", len(bad), "案に無い月〜金の予定", len(extra))
for b in bad[:20]:
    print("  差", b)
for g in extra[:20]:
    print("  余り", g)
for p in other:
    print(" ", p)
