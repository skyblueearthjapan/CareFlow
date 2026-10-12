"""使うモデルの決まり（PO 2026-10-12）。モデルの名前はここ 1 か所でだけ決める。

- Claude: いつも最新の Opus。CLI の別名 ``opus`` を渡す（``--resume`` の時も毎回）。コードを変えずに新しい版へ。
  実際の版は毎ターンの system/init の ``model`` で分かる → 会話の見出しに出す（``resolved_model``）。
- Codex（ChatGPT）: いつも最新の Sol。会話の始めに Codex のモデル一覧（app-server の ``model/list`` か
  models のキャッシュ）から ``gpt-<版>-sol`` の一番新しい版を数字で選ぶ（gpt-6.1-sol > gpt-6-sol > gpt-5.6-sol。
  後ろに余分の付いた id は使わない）。一覧が読めなければ ``gpt-6.1-sol``。Codex が「このアカウントでは使えない」と
  答えたら、一つ下の Sol に下げて記録する。
- いちばん上の版（Fable・Astra）や ``best``・``default`` のような名前は決して渡さない。

参考（読むだけ）: PC-Assistant ``src/PCAssistant.Core/Agents/AgentModelChoices.cs``（SolBaseline・Family・VersionOf）。
"""

from __future__ import annotations

import re
from typing import Iterable

CLAUDE_ALIAS = "opus"
SOL_BASELINE = "gpt-6.1-sol"
CODEX_UNSUPPORTED_WORDS = "is not supported when using Codex with a ChatGPT account"

#: 渡してはいけない名前（いちばん上の版・あいまいな別名）。
_FORBIDDEN_ALIASES = {"best", "default", "fable", "astra", "opusplan", "auto"}
_TOP_TIER_WORDS = ("fable", "astra")
_SOL_RE = re.compile(r"^gpt-(\d{1,4}(?:\.\d{1,4}){0,2})-sol$")


def is_forbidden(name: str | None) -> bool:
    """いちばん上の版・best/default などの名前か。"""
    m = (name or "").strip().lower()
    if not m or m in _FORBIDDEN_ALIASES:
        return True
    return any(w in m for w in _TOP_TIER_WORDS)


# ---------- Claude ----------
def claude_model_arg() -> str:
    """CLI に渡す --model。いつも ``opus``（最新の Opus）。"""
    return CLAUDE_ALIAS


def claude_init_problem(resolved: str | None) -> str | None:
    """init の model が決まりどおりか。違えば理由（平易な日本語）、合っていれば None。"""
    m = (resolved or "").strip().lower()
    if not m:
        return None  # init に model が無い版の CLI もある。止めない
    if is_forbidden(m):
        return "思っていない種類の AI が選ばれたので止めました（らく助の係の方へご連絡ください）"
    if "opus" not in m:
        return f"思っていない種類の AI（{resolved}）で始まったので止めました（らく助の係の方へご連絡ください）"
    return None


def display_name(resolved: str | None) -> str:
    """見出し用: claude-opus-5-5 → 「Opus 5.5」。分からなければそのまま。"""
    m = (resolved or "").strip()
    g = re.match(r"^claude-(opus|sonnet|haiku)-(\d+)(?:-(\d+))?", m)
    if g:
        return f"{g.group(1).capitalize()} {g.group(2)}" + (f".{g.group(3)}" if g.group(3) else "")
    s = sol_version(m)
    if s is not None:
        return "Sol " + ".".join(str(x) for x in _trim(s))
    return m


# ---------- Codex（Sol） ----------
def sol_version(model_id: str | None) -> tuple[int, int, int] | None:
    """``gpt-6.1-sol`` → (6, 1, 0)。``gpt-<版>-sol`` の形でなければ None（余分の付いた id も None）。"""
    m = _SOL_RE.match((model_id or "").strip().lower())
    if not m:
        return None
    nums = [int(x) for x in m.group(1).split(".")]
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def _trim(v: tuple[int, int, int]) -> list[int]:
    out = list(v)
    while len(out) > 1 and out[-1] == 0:
        out.pop()
    return out


def sol_candidates(available: Iterable[str] | None) -> list[str]:
    """一覧の Sol を新しい順に（同じ版は最初のものだけ）。"""
    seen: dict[tuple[int, int, int], str] = {}
    for mid in available or []:
        if not isinstance(mid, str) or is_forbidden(mid):
            continue
        v = sol_version(mid)
        if v is not None and v not in seen:
            seen[v] = mid.strip().lower()
    return [seen[v] for v in sorted(seen, reverse=True)]


def newest_sol(available: Iterable[str] | None) -> str:
    """一覧の中で一番新しい Sol。一覧が無い・Sol が無ければ ``gpt-6.1-sol``。"""
    c = sol_candidates(available)
    return c[0] if c else SOL_BASELINE


def next_lower_sol(current: str, available: Iterable[str] | None) -> str | None:
    """``current`` が使えなかったときの次の候補（一つ下の Sol）。無ければ None。"""
    cur = sol_version(current)
    if cur is None:
        return None
    for mid in sol_candidates(available):
        v = sol_version(mid)
        if v is not None and v < cur:
            return mid
    return None


def codex_unsupported(text: str | None) -> bool:
    """Codex の「このアカウントでは使えないモデル」の答えか（2026-10-09 の文言）。"""
    return bool(text) and CODEX_UNSUPPORTED_WORDS in text


class SolPicker:
    """会話ごとの Sol の選び方と、下げた記録（``fallbacks``）。Codex の会話の係（段 3-5）が使う。"""

    def __init__(self, available: Iterable[str] | None):
        self.available = list(available or [])
        self.current = newest_sol(self.available)
        self.fallbacks: list[dict] = []

    def on_error(self, text: str | None) -> str | None:
        """エラー文を受け取り、使えないモデルなら一つ下へ下げて新しい名前を返す（下げられなければ None）。"""
        if not codex_unsupported(text):
            return None
        lower = next_lower_sol(self.current, self.available)
        if lower is None:
            return None
        self.fallbacks.append({"from": self.current, "to": lower, "reason": "unsupported_for_account"})
        self.current = lower
        return lower
