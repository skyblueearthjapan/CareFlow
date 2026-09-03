import re
lines=open("/tmp/run.log",encoding="utf-8",errors="replace").read().splitlines()
hdr=re.compile(r"^=== (変更|削除|追加|日付変更): (.+?) (\d+)日")
blocks=[];cur=None
for ln in lines:
    if re.match(r"^\d{4}-\d\d-\d\d .*werkzeug",ln): continue
    m=hdr.match(ln)
    if m:
        cur={"act":m.group(1),"user":m.group(2),"day":int(m.group(3)),"lines":[]}
        blocks.append(cur)
    elif cur is not None and not ln.startswith("--- 利用者"):
        cur["lines"].append(ln)
print("blocks:",len(blocks))
failed_keys={("一ノ瀬　祐里枝",11),("三浦　未久美",9),("並木　啓悦",9),("並木　啓悦",11),("久須見　高広",7),("久須見　高広",8),("久須見　高広",10),("久須見　高広",12),("井川　裕太",10),("井川　裕太",11),("前川　七海",10),("前川　心愛",10),("加藤　龍一",7),("加藤　龍一",10),("吉川　洋",9),("唐鎌　美穂",8),("唐鎌　美穂",9),("唐鎌　美穂",11),("園田　貞子",8),("安永　愛菜",9),("安永　愛菜",11),("木村　駿",7)}
for b in blocks:
    if (b["user"],b["day"]) not in failed_keys: continue
    t="\n".join(b["lines"])
    flags=[]
    if "削除→再追加" in t: flags.append("edit=削除→再追加")
    if "削除検証NG" in t: flags.append("削除検証NG")
    if "削除完了 (検証OK)" in t: flags.append("削除OK")
    if "chargeStaff1Id1 が見つかりません" in t: flags.append("職員select未表示")
    if "モーダルが開いたまま" in t: flags.append("登録失敗(モーダル残)")
    if "登録完了" in t: flags.append("登録完了あり")
    if "リカバリ" in t: flags.append("リカバリ")
    key=[l.strip() for l in b["lines"] if re.match(r"\s*(削除:|時間変更:|日付変更:|職員|予定をクリック|.*エントリを発見|.*見つかりません|.*スキップ)",l)][:5]
    print("9/%-2d %-10s %-4s | %s | %s" % (b["day"], b["user"], b["act"], ", ".join(flags), " / ".join(key)[:230]))
