import re, glob, os, unicodedata
files = sorted(glob.glob("/root/PlaywrightTest1/artifacts/debug_click_entry_*_2026090[23]_*.html"), key=os.path.getmtime)
f = files[-1]
h = open(f, encoding="utf-8", errors="replace").read()
print("file:", os.path.basename(f), "mtime:", os.path.getmtime(f))
m = re.search(r'<div[^>]*id="user_search".*?<select[^>]*>(.*?)</select>', h, flags=re.S)
if not m:
    m = re.search(r'<select[^>]*class="[^"]*form-control[^"]*"[^>]*>(.*?)</select>', h, flags=re.S)
opts = re.findall(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>', m.group(1), flags=re.S)
names = [re.sub(r"\s+", " ", unicodedata.normalize("NFKC", t)).strip() for v, t in opts]
print("利用者数:", len(names))
def norm(s): return unicodedata.normalize("NFKC", s).replace(" ", "").replace("\u3000", "")
hits = [n for n in names if any(k in norm(n) for k in ("麻生", "真理奈", "あそう", "アソウ", "マリナ", "まりな"))]
print("麻生/真理奈 の候補:", hits)
print("あ行の利用者:", [n for n in names if norm(n)[:1] in "あアいイうウえエおオ" or norm(n)[:1] in "阿安麻荒有"])
print("先頭20:", names[:20])
