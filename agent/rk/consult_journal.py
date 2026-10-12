"""相談の引き継ぎ帳（PO 2026-10-12）。

エージェントはその場で柔軟に動き、あとで「何があったか」を申し送りのように書き残す。次の相談の始めに読む。
自動で学習したり、規則を書き換えたりはしない。**核の約束（AGENTS.md §4 安全・承認・キャラクター）はいつも帳より優先**。

置き場所（事業所ごと）:
  手元・pc/vps : <RK_JOURNAL_DIR か runs/journal>/<事業所 か default>/YYYY-MM.jsonl（追記だけ・git 管理外）
  relay        : 中継の内側口 /rk/journal/*（DB の agent.journal_entries）
「古くなった」は消さず retire の印を足す（読むときは隠す）。

名前（利用者・職員）は帳に書いてよい（サーバーの中・git 管理外だけ。docs や外部サービスへは出さない）。
"""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from rk import RkError

OUTCOMES = ("applied", "plan-only", "declined", "unanswered")
TEXT_FIELDS = ("title", "asked", "tried", "said", "next_time")
FIELD_MAX = {"title": 80, "asked": 400, "tried": 400, "said": 400, "next_time": 300}
TOTAL_MAX = 1500
TAG_MAX, TAG_LEN = 12, 40
PLAN_MAX = 6

#: 核の約束を変えようとする書き方（簡易の関所。核はもともと帳より優先なので、ここは「帳に残さない」ための印）
_GUARD = re.compile(
    r"承認なし|了承なし|確認なしで|承認を(飛ば|省|スキップ)|了承を(飛ば|省|スキップ)|バックアップ(を|は)?(省|飛ば|取らな|不要)|"
    r"禁止(を|の)?(解除|外)|ルールを(無視|無効|変え|破)|約束を(無視|破|変え)|キャラ(クター)?を(変|捨)|"
    r"ignore\s+(the\s+)?(rules?|instructions?)|disregard\s+(the\s+)?(rules?|instructions?)|without\s+approval|skip\s+(the\s+)?backup|"
    r"override\s+(the\s+)?(rules?|safety)",
    re.I)
GUARD_MESSAGE = ("この内容は、承認・バックアップ・禁止などの核の約束を変えるように読めるので、帳には残しません。"
                 "核の約束はいつも帳より優先です。言い方を変えるか、今回の出来事だけを書いてください。")
SIZE_MESSAGE = f"1 件は全部で {TOTAL_MAX} 字までです。要点だけに短くしてください（今の長さ: {{n}} 字）。"

_OFFICE = re.compile(r"[^A-Za-z0-9_-]+")
_ID = re.compile(r"^jn_[0-9a-f]{12}$")


def utc_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def office_key(office: str | None) -> str:
    return _OFFICE.sub("", str(office or "")).strip("-_")[:64] or "default"


def new_id() -> str:
    return "jn_" + secrets.token_hex(6)


# ---------- 検査・組み立て ----------
def _split(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = re.split(r"[,、\s]+", v)
    return [str(x).strip() for x in v if str(x).strip()]


def build_entry(data: dict, office: str | None = None, session_id: str | None = None,
                now: datetime | None = None) -> dict:
    """入力（dict）から 1 件を作る。長すぎ・核の約束を変える書き方は RkError（穏やかな文）。"""
    e: dict[str, Any] = {k: " ".join(str(data.get(k) or "").split()) for k in TEXT_FIELDS}
    if "next" in data and not e["next_time"]:
        e["next_time"] = " ".join(str(data["next"]).split())
    if not e["title"]:
        raise RkError("題名（--title）が要ります。何の相談だったかを一言で書いてください")
    outcome = str(data.get("outcome") or "unanswered").strip()
    if outcome not in OUTCOMES:
        raise RkError(f"結果（--outcome）は {' / '.join(OUTCOMES)} のどれかです")
    for k, mx in FIELD_MAX.items():
        if len(e[k]) > mx:
            raise RkError(f"{k} は {mx} 字までです（今 {len(e[k])} 字）。要点だけに短くしてください")
    total = sum(len(e[k]) for k in TEXT_FIELDS)
    if total > TOTAL_MAX:
        raise RkError(SIZE_MESSAGE.format(n=total))
    tags = [t[:TAG_LEN] for t in _split(data.get("tags"))][:TAG_MAX]
    plans = [p[:40] for p in _split(data.get("plans") if data.get("plans") is not None else data.get("plan"))][:PLAN_MAX]
    if _GUARD.search(" ".join(list(e.values()) + tags)):
        raise RkError(GUARD_MESSAGE)
    return {"id": new_id(), "ts": utc_iso(now), "office": office_key(office), "session_id": session_id or None,
            **e, "outcome": outcome, "tags": tags, "plans": plans}


def matches(entry: dict, words: list[str]) -> int:
    """一致の強さ（0 = 一致しない）。題名・タグは重く、本文は 1。すべての語が要る。"""
    if not words:
        return 1
    score = 0
    tags = " ".join(entry.get("tags") or [])
    for w in words:
        lw = w.lower()
        s = 0
        if lw in (entry.get("title") or "").lower() or lw in tags.lower():
            s = 3
        elif any(lw in str(entry.get(k) or "").lower() for k in ("asked", "tried", "said", "next_time")):
            s = 1
        if s == 0:
            return 0
        score += s
    return score


def rank(entries: list[dict], words: list[str], n: int) -> list[dict]:
    scored = [(matches(e, words), e) for e in entries if not e.get("retired_at")]
    scored = [x for x in scored if x[0] > 0]
    scored.sort(key=lambda x: (x[0], x[1]["ts"]), reverse=True)
    return [e for _s, e in scored[:n]]


def parse_since(text: str, now: datetime | None = None) -> datetime:
    """7d・2w・12h・YYYY-MM-DD。"""
    now = now or datetime.now(timezone.utc)
    m = re.fullmatch(r"(\d{1,4})([dwh])", str(text).strip().lower())
    if m:
        n, u = int(m.group(1)), m.group(2)
        return now - timedelta(**{{"d": "days", "w": "weeks", "h": "hours"}[u]: n})
    try:
        return datetime.strptime(str(text).strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise RkError("--since は 7d（日）・2w（週）・12h（時間）か YYYY-MM-DD で書いてください") from None


def format_entry(e: dict, full: bool = True) -> str:
    """固定の 5 行（状況／試したこと／言われたこと／結果／次回）。"""
    head = f"[{e['id']}] {str(e['ts'])[:10]} {e.get('title', '')}"
    if e.get("tags"):
        head += "  #" + " #".join(e["tags"])
    if e.get("retired_at"):
        head += f"  （古い印: {e.get('retired_reason') or ''}）"
    if not full:
        return head
    return "\n".join([
        head,
        f"  状況: {e.get('asked') or '-'}",
        f"  試したこと: {e.get('tried') or '-'}",
        f"  言われたこと: {e.get('said') or '-'}",
        f"  結果: {e.get('outcome')}",
        f"  次回: {e.get('next_time') or '-'}",
    ])


# ---------- 置き場所: ファイル ----------
class FileBackend:
    def __init__(self, root: Path, office: str | None):
        self.office = office_key(office)
        self.dir = Path(root) / self.office

    def _files(self) -> list[Path]:
        return sorted(self.dir.glob("????-??.jsonl")) if self.dir.is_dir() else []

    def _read_all(self) -> list[dict]:
        entries: dict[str, dict] = {}
        for f in self._files():
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("kind") == "retire":
                    t = entries.get(r.get("id"))
                    if t is not None:
                        t["retired_at"], t["retired_reason"] = r.get("ts"), r.get("reason")
                elif r.get("id"):
                    entries[r["id"]] = r
        return sorted(entries.values(), key=lambda e: e["ts"], reverse=True)

    def _append(self, rec: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.dir / (str(rec["ts"])[:7] + ".jsonl")
        fd = os.open(str(p), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")

    def write(self, data: dict, session_id: str | None = None) -> dict:
        e = build_entry(data, self.office, session_id or os.environ.get("RK_SESSION_ID"))
        self._append(e)
        return e

    def list(self, since: datetime | None = None, include_retired: bool = False) -> list[dict]:
        out = self._read_all()
        if since is not None:
            out = [e for e in out if e["ts"] >= utc_iso(since)]
        return out if include_retired else [e for e in out if not e.get("retired_at")]

    def recent(self, n: int) -> list[dict]:
        return self.list()[:n]

    def search(self, words: list[str], n: int) -> list[dict]:
        return rank(self.list(), words, n)

    def show(self, entry_id: str) -> dict:
        for e in self._read_all():
            if e["id"] == entry_id:
                return e
        raise RkError(f"{entry_id} という記録はありません")

    def retire(self, entry_id: str, reason: str) -> dict:
        e = self.show(entry_id)
        self._append({"kind": "retire", "id": entry_id, "ts": utc_iso(), "reason": reason[:200]})
        return dict(e, retired_at=utc_iso(), retired_reason=reason[:200])


# ---------- 置き場所: 中継 ----------
class RelayBackend:
    def __init__(self, client: Any, office: str | None):
        self.client, self.office = client, office_key(office)

    def _post(self, op: str, body: dict) -> Any:
        return self.client.request("POST", f"/rk/journal/{op}", {"office": self.office, **body})

    def write(self, data: dict, session_id: str | None = None) -> dict:
        return self._post("write", {"entry": data})

    def list(self, since: datetime | None = None, include_retired: bool = False) -> list[dict]:
        return self._post("list", {"since": utc_iso(since) if since else None, "include_retired": include_retired,
                                   "limit": 1000})

    def recent(self, n: int) -> list[dict]:
        return self._post("list", {"limit": n})

    def search(self, words: list[str], n: int) -> list[dict]:
        return self._post("search", {"words": words, "limit": n})

    def show(self, entry_id: str) -> dict:
        return self._post("show", {"id": entry_id})

    def retire(self, entry_id: str, reason: str) -> dict:
        return self._post("retire", {"id": entry_id, "reason": reason})


def journal_root(cfg: Any) -> Path:
    p = os.environ.get("RK_JOURNAL_DIR")
    return Path(p) if p else Path(cfg.runs_dir) / "journal"


def open_backend(cfg: Any, office: str | None = None):
    office = office or os.environ.get("RK_OFFICE")
    if getattr(cfg, "mode", "") == "relay":
        from rk.transport import relay_client
        return RelayBackend(relay_client(cfg), office)
    return FileBackend(journal_root(cfg), office)


# ---------- 週のまとめ（PO・責任者向け） ----------
def digest_markdown(entries: list[dict], since_label: str, office: str, now: datetime | None = None) -> str:
    from collections import Counter
    now = now or datetime.now(timezone.utc)
    by = Counter(e.get("outcome") for e in entries)
    tags = Counter(t for e in entries for t in e.get("tags") or [])
    titles = Counter(e.get("title") for e in entries)
    L = [f"# 相談の引き継ぎ帳 まとめ（{office}・{since_label} 以降・{utc_iso(now)[:10]} 作成）", "",
         f"- 記録 {len(entries)} 件（反映 {by.get('applied', 0)}・案だけ {by.get('plan-only', 0)}・"
         f"見送り {by.get('declined', 0)}・答え待ち {by.get('unanswered', 0)}）",
         "- 自動では何も変えていません。スキルや道具を直すかは、このまとめを見て決めてください。", ""]

    def section(title: str, rows: list[str], empty: str = "なし") -> None:
        L.extend([f"## {title}", ""] + (rows or [f"- {empty}"]) + [""])

    rep = [f"- 「{t}」 {c} 回" for t, c in titles.most_common() if c >= 2]
    rep += [f"- タグ #{t} が {c} 件（よく出る人・話題）" for t, c in tags.most_common() if c >= 2]
    section("くり返された頼み", rep)
    corr = [e for e in entries if e.get("said")]
    section("よく直された点（管理者の言葉）",
            [f"- {str(e['ts'])[:10]} {e['title']}: {e['said']}  [{e['id']}]" for e in corr])
    section("答えが出ていない・見送り",
            [f"- {str(e['ts'])[:10]} {e['title']}（{e['outcome']}）[{e['id']}]"
             for e in entries if e.get("outcome") in ("unanswered", "declined")])
    cand = [e for e in entries if e.get("next_time")]
    section("スキル・道具の改善の候補（次回へのメモから）",
            [f"- {e['title']}: {e['next_time']}  [{e['id']}]" for e in cand]
            + ([f"- 同じ題・同じタグが続くものは、スキルの手順か rk の部品に入れる候補です"] if rep else []))
    return "\n".join(L) + "\n"
