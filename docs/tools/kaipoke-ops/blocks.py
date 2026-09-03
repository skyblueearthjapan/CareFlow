import re,sys
lines=open("/tmp/run.log",encoding="utf-8",errors="replace").read().splitlines()
hdr=re.compile(r"^=== (変更|削除|追加|日付変更): (.+?) (\d+)日")
blocks=[];cur=None
for ln in lines:
    if re.match(r"^\d{4}-\d\d-\d\d .*werkzeug",ln): continue
    m=hdr.match(ln)
    if m:
        cur={"act":m.group(1),"user":m.group(2),"day":int(m.group(3)),"lines":[ln]}
        blocks.append(cur)
    elif cur is not None and not ln.startswith("--- 利用者"):
        cur["lines"].append(ln)
want={("山岡　由美子",9),("松戸　きよ",10),("森田　美穂子",10),("清水　洋之",9),("井川　裕太",9)}
for b in blocks:
    if (b["user"],b["day"]) in want:
        print("\n".join(l for l in b["lines"] if "【診断】" not in l and "スクリーンショット" not in l and "options=" not in l)[:2600])
        print("#"*60)
