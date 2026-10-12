"""週のならし — 「今回だけの条件」の指定（変わった注文用の部品）。標準ライブラリだけ（rk からも読み込む）。

level.py（docs）と rk level（agent）の両方が同じ指定を受けるための、引数の登録・解釈・日本語の説明。
計算での使い方は solver.py（opts のキー）。ここで作る opts は JSON にできる値だけ。

  --days thu,fri                 この曜日だけ計算（他の日は触らない・案にも出さない）
  --prefer-staff CODE[:重み]     この人へ多めに寄せる（余力がある人など・重みの既定 100）
  --avoid-staff CODE[:重み]      この人は必要なときだけ使う（既定 600・--off は「絶対に使わない」）
  --keep-current [重み]          今の担当を変えない方へ寄せる（既定 300・変える 1 件ごと）
  --cancel 訪問ID                急遽キャンセル: 案から外し、今週だけ取消の操作を案に入れる（--drop は外すだけ）
  --fast                         計算を 10 秒（今日すぐ用・最良でない案になりうる）
  --only-staff CODE[,CODE] --window HH:MM-HH:MM
                                 その時間帯は、その人たちだけが訪問する（他の人はその時間に訪問しない）
  --not-same-day 利用者A,利用者B  同じ職員が同じ日にこの 2 名を持たない
  --pair-staff 利用者:CODE        この利用者は必ずこの人
  --forbid-pair 利用者:CODE       この利用者はこの人にしない
利用者は 利用者 ID の先頭 か 名前の一部（空白なし）で指定する。
"""

from __future__ import annotations

import re

DEFAULT_PREFER = 100
DEFAULT_AVOID = 600
DEFAULT_KEEP = 300
FAST_SECONDS = 10

_WD = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_WD_JA = {"月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6}
WD_NAMES = "月火水木金土日"


def parse_weekdays(text: str) -> list[int]:
    """'thu,fri' / '木,金' → [3, 4]"""
    out: list[int] = []
    for tok in re.split(r"[,\s、]+", text.strip()):
        if not tok:
            continue
        key = tok.lower()[:3]
        wd = _WD.get(key, _WD_JA.get(tok[:1]))
        if wd is None:
            raise ValueError(f"曜日が読めません: {tok}（mon,tue,wed,thu,fri,sat,sun か 月火水木金土日）")
        if wd not in out:
            out.append(wd)
    if not out:
        raise ValueError("曜日が空です")
    return sorted(out)


def _code_weight(text: str, default: int) -> tuple[str, int]:
    code, _, w = text.partition(":")
    code = code.strip()
    if not code:
        raise ValueError(f"職員コードが空です: {text}")
    try:
        return code, int(w) if w.strip() else default
    except ValueError:
        raise ValueError(f"重みは整数で書いてください: {text}") from None


def _hm(t: str) -> int:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", t)
    if not m or int(m.group(1)) > 24 or int(m.group(2)) > 59:
        raise ValueError(f"時刻は HH:MM で書いてください: {t}")
    return int(m.group(1)) * 60 + int(m.group(2))


def parse_window(text: str) -> tuple[int, int]:
    a, _, b = text.partition("-")
    lo, hi = _hm(a), _hm(b)
    if lo >= hi:
        raise ValueError(f"時間帯の始めは終わりより前にしてください: {text}")
    return lo, hi


def _pair(text: str, sep: str, what: str) -> tuple[str, str]:
    a, s, b = text.partition(sep)
    if not s or not a.strip() or not b.strip():
        raise ValueError(f"{what} は「A{sep}B」の形で書いてください: {text}")
    return a.strip(), b.strip()


def add_args(ap) -> None:
    """level.py と rk level の両方へ同じ指定を足す。"""
    ap.add_argument("--days", help="この曜日だけ計算 例: thu,fri（他の日は触らない）")
    ap.add_argument("--prefer-staff", action="append", default=[], metavar="CODE[:重み]",
                    help=f"この人へ多めに寄せる（重みの既定 {DEFAULT_PREFER}）。何度でも")
    ap.add_argument("--avoid-staff", action="append", default=[], metavar="CODE[:重み]",
                    help=f"この人は必要なときだけ使う（既定 {DEFAULT_AVOID}）。何度でも（使わないなら --off）")
    ap.add_argument("--keep-current", nargs="?", type=int, const=DEFAULT_KEEP, default=None, metavar="重み",
                    help=f"今の担当を変えない方へ寄せる（既定 {DEFAULT_KEEP}・変える 1 件ごと）")
    ap.add_argument("--cancel", action="append", default=[], metavar="訪問IDの先頭",
                    help="急遽キャンセル: 案から外し「今週だけ取消」の操作を入れる（プールへは戻さない）")
    ap.add_argument("--fast", action="store_true", help=f"計算を {FAST_SECONDS} 秒にする（今日すぐ用）")
    ap.add_argument("--only-staff", action="append", default=[], metavar="CODE[,CODE]",
                    help="--window の時間帯は、この人たちだけが訪問する。何度でも（--window と対）")
    ap.add_argument("--window", action="append", default=[], metavar="HH:MM-HH:MM",
                    help="--only-staff と同じ数だけ、同じ順で")
    ap.add_argument("--not-same-day", action="append", default=[], metavar="利用者A,利用者B",
                    help="同じ職員が同じ日にこの 2 名を持たない。何度でも")
    ap.add_argument("--pair-staff", action="append", default=[], metavar="利用者:CODE",
                    help="この利用者は必ずこの人。何度でも")
    ap.add_argument("--forbid-pair", action="append", default=[], metavar="利用者:CODE",
                    help="この利用者はこの人にしない。何度でも")


def from_args(a) -> dict:
    """引数 → solver の opts に足すキー（JSON にできる値だけ）。使われていないキーは入れない。"""
    out: dict = {}
    if a.prefer_staff:
        out["prefer"] = dict(_code_weight(x, DEFAULT_PREFER) for x in a.prefer_staff)
    if a.avoid_staff:
        out["avoid"] = dict(_code_weight(x, DEFAULT_AVOID) for x in a.avoid_staff)
    if a.keep_current is not None:
        out["keep_current"] = int(a.keep_current)
    if a.cancel:
        out["cancel"] = [x.strip().lower() for x in a.cancel]
    if len(a.only_staff) != len(a.window):
        raise ValueError("--only-staff と --window は同じ数だけ（同じ順で）指定してください")
    if a.only_staff:
        out["only_staff"] = [
            {"codes": [c.strip() for c in s.split(",") if c.strip()], "window": list(parse_window(w))}
            for s, w in zip(a.only_staff, a.window, strict=True)
        ]
        if any(not x["codes"] for x in out["only_staff"]):
            raise ValueError("--only-staff に職員コードがありません")
    if a.not_same_day:
        out["not_same_day"] = [list(_pair(x, ",", "--not-same-day")) for x in a.not_same_day]
    if a.pair_staff:
        out["pair_staff"] = [list(_pair(x, ":", "--pair-staff")) for x in a.pair_staff]
    if a.forbid_pair:
        out["forbid_pair"] = [list(_pair(x, ":", "--forbid-pair")) for x in a.forbid_pair]
    if a.fast:
        out["fast"] = True
    return out


def to_argv(a) -> list[str]:
    """rk level → solve.py に渡す引数（--days は rk が日付にして --day で渡すのでここには入れない）。"""
    argv: list[str] = []
    for flag, vals in (("--prefer-staff", a.prefer_staff), ("--avoid-staff", a.avoid_staff), ("--cancel", a.cancel),
                       ("--only-staff", a.only_staff), ("--window", a.window), ("--not-same-day", a.not_same_day),
                       ("--pair-staff", a.pair_staff), ("--forbid-pair", a.forbid_pair)):
        for v in vals:
            argv += [flag, v]
    if a.keep_current is not None:
        argv += ["--keep-current", str(a.keep_current)]
    if a.fast:
        argv.append("--fast")
    return argv


def hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def describe(opts: dict, names: dict[str, str] | None = None, days=None, custom: list[str] | None = None
             ) -> list[dict]:
    """「今回だけの条件」を日本語の 1 行ずつにする。relax = 決まりをゆるめる条件（案の「気を付ける点」にも出す）。
    names = 職員コード → 呼び名。days = 計算した日（None = 週全部）。custom = 自作の条件の説明（API）。"""
    nm = names or {}

    def who(code: str) -> str:
        return f"{nm.get(code, code)}さん" if code in nm else code

    out: list[dict] = []

    def add(text: str, relax: bool = False) -> None:
        out.append({"text": text, "relax": relax})

    if days:
        ds = sorted(days)
        add("計算した日: " + "・".join(f"{d.month}/{d.day}（{WD_NAMES[d.weekday()]}）" for d in ds)
            + " だけ（ほかの日はそのまま・案にも出さない）")
    for code, dd in opts.get("off", []):
        add(f"{who(code)}は{(str(dd.month) + '/' + str(dd.day)) if dd else '週全部'}お休みとして計算（絶対に使わない）")
    if opts.get("allow_over"):
        add(f"正規の職員の 1 日の上限を {opts['allow_over']} 名まで超えてよい（超えた分は重み付き）", True)
    if opts.get("over_before_manager"):
        add("マネージャーに回す前に「少しオーバー」で吸収する", True)
    if opts.get("balance"):
        add("正規の職員の件数をならす（平均を超える分に重み）")
    for c, w in (opts.get("prefer") or {}).items():
        add(f"{who(c)}へ多めに寄せる（重み {w}）")
    for c, w in (opts.get("avoid") or {}).items():
        add(f"{who(c)}は必要なときだけ使う（重み {w}）")
    if opts.get("keep_current"):
        add(f"今の担当を変えない方へ寄せる（変える 1 件ごとに重み {opts['keep_current']}）")
    for x in opts.get("only_staff") or []:
        a, b = x["window"]
        add(f"{hhmm(a)}〜{hhmm(b)} は " + "・".join(who(c) for c in x["codes"]) + " だけが訪問する")
    for a, b in opts.get("not_same_day") or []:
        add(f"{a} と {b} は、同じ職員が同じ日に両方持たない")
    for p, c in opts.get("pair_staff") or []:
        add(f"{p} は必ず{who(c)}")
    for p, c in opts.get("forbid_pair") or []:
        add(f"{p} は{who(c)}にしない")
    for v in opts.get("cancel") or []:
        add(f"訪問 {v} は急遽キャンセル（案から外し、今週だけ取消にする）")
    for v in opts.get("drop") or []:
        add(f"訪問 {v} は案から外す（取消などは別に行う）")
    if opts.get("add_special"):
        add("特別訪問週間の未配置の○を、その日に訪問の無い日だけ足す")
    if opts.get("fast"):
        add("計算時間を短くした（今日すぐ用・最良の案でないことがある）", True)
    if opts.get("weights"):
        add("計算の重みを変えた: " + "・".join(f"{k}={v}" for k, v in opts["weights"].items()), True)
    for t in custom or []:
        add("自作の条件: " + t, True)
    return out
