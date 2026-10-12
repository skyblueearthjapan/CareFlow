"""脇道の見張り（PO 2026-10-12）— 走ったコマンドを見て、本当の危うさだけを拾う。

考え方: 箱の中で考える・計算する（シェル・python・その場の script）は自由。書き込みは中継の承認番号だけ。
見張りが拾うのは次だけ（ふつうの python・シェルの計算は拾わない）:

| 印 | 何 | 重さ |
|---|---|---|
| ``secret_read`` | ログイン情報・環境・他のプロセス・鍵を読む（``~/.codex/auth.json``・``/proc/<pid>``・``.env`` など） | 重い（すぐ止める） |
| ``relay_bypass`` | 中継の内側口を rk を通さずにたたく（``agent-relay:8091``・``RK_RELAY_TOKEN``・``/rk/apply`` など） | ふつう |
| ``net_out`` | 外へ出る道具・python などからの直接の通信（curl・wget・urllib など） | ふつう |
| ``agent_dir`` | 読み取り専用の ``/srv/agent``（スキル・rk・SQL）を書き換えようとする | ふつう |
| ``loop`` | 同じコマンドが 3 回続けて失敗 | ふつう |
| ``long_loop`` | 話さずに作業だけが長く続く（既定 25 件） | ふつう |

動き: ターンで最初の印 → モデルへ短い道案内（``steer``・叱らない）。ターンで 3 つ目の印、または重い印 → ターンを
止めて（``stop``）画面に琥珀の 1 行（``card``）。印はすべて監査の記録へ（``log``）。
入力は正規化した共通イベント（``_cmd``・``message``・``turn.end``）。Claude の Bash も Codex のコマンドも同じ。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from relay import codex_config as cx

SEVERE = {"secret_read"}

#: 道案内（モデルへ送る文）。叱らず、正しい道を示す。
STEER = {
    "relay_bypass": ("らく助の記録は rk で読むと確実です（例: rk sql --list で調べ方の一覧、rk get /visits …）。"
                     "案は rk plan submit で画面に出せます。中継の口を直接たたく必要はありません。"
                     "計算やその場の script は作業フォルダーで自由に使ってください。"),
    "net_out": ("この箱の外へはつながりません。らく助の記録は rk で読めます（rk sql --list・rk get …）。"
                "計算は作業フォルダーで python を自由に使ってください。"),
    "agent_dir": ("/srv/agent の中（スキル・rk・SQL）は読み取り専用です。メモや途中のファイルは作業フォルダー"
                  "（$RK_WORK_DIR、その中の scratch）に置いてください。"),
    "loop": ("同じコマンドが続けてうまくいっていません。rk whoami でつながりを確かめるか、別の調べ方"
             "（rk sql --list）を試してください。それでも難しければ、いまの状況を管理者に短く伝えて相談してください。"),
    "long_loop": "作業が長くなっています。ここまでで分かったことを管理者に短く伝えて、続けるか相談してください。",
}
STEER_PREFIX = "【らく助の中継から】"

CARD_SECRET = "らく助が触れてはいけない所を読もうとしたので、作業を止めました。もう一度送ると続きから始めます。"
CARD_REPEAT = "らく助の作業がうまく進まなかったので、いったん止めました。もう一度送ると続きから始めます。"

_RELAY_WRITE_PATH = re.compile(r"/rk/(?:apply|undo|plans|backup|progress)|/rk/api/v1/", re.I)


def classify(command: str | None) -> str | None:
    """1 つのコマンドの印（無ければ None）。物差しは承認の答え（relay.codex_config.command_risk）と同じ。"""
    c = command or ""
    if cx.is_rk_command(c):
        return None
    risk = cx.command_risk(c)
    if risk == "secret_read":
        return "secret_read"
    if risk == "relay_direct" or (risk in ("raw_http", "net_tool") and _RELAY_WRITE_PATH.search(c)):
        return "relay_bypass"
    if risk in ("raw_http", "net_tool"):
        return "net_out"
    if risk == "agent_dir_write":
        return "agent_dir"
    return None


@dataclass
class Action:
    kind: str            # "log" | "steer" | "stop" | "card"
    flag: str = ""
    text: str = ""
    detail: str = ""


@dataclass
class Watcher:
    stop_after: int = 3
    loop_failures: int = 3
    long_steps: int = 25
    flags_in_turn: int = 0
    steered: bool = False
    stopped: bool = False
    steps_since_message: int = 0
    _cmds: dict[str, str] = field(default_factory=dict)       # step_id → command
    _fails: dict[str, int] = field(default_factory=dict)      # command → 続けて失敗した回数
    _looped: set[str] = field(default_factory=set)
    _long_flagged: bool = False
    history: list[dict] = field(default_factory=list)

    def reset_turn(self) -> None:
        self.flags_in_turn = 0
        self.steered = False
        self.stopped = False
        self.steps_since_message = 0
        self._long_flagged = False

    def observe(self, e: dict) -> list[Action]:
        t = e.get("type")
        if t == "turn.end":
            self.reset_turn()
            return []
        if t in ("message", "question"):
            self.steps_since_message = 0
            return []
        if t != "_cmd":
            return []
        if e.get("phase") == "start":
            cmd = str(e.get("command") or "")
            self._cmds[str(e.get("step_id"))] = cmd
            self.steps_since_message += 1
            flag = classify(cmd)
            if flag:
                return self._flag(flag, cmd)
            if self.steps_since_message >= self.long_steps and not self._long_flagged:
                self._long_flagged = True
                return self._flag("long_loop", f"{self.steps_since_message} steps")
            return []
        cmd = self._cmds.pop(str(e.get("step_id")), "")
        if not cmd:
            return []
        if e.get("ok"):
            self._fails.pop(cmd, None)
            return []
        n = self._fails[cmd] = self._fails.get(cmd, 0) + 1
        if n >= self.loop_failures and cmd not in self._looped:
            self._looped.add(cmd)
            return self._flag("loop", cmd)
        return []

    def _flag(self, flag: str, detail: str) -> list[Action]:
        self.flags_in_turn += 1
        rec = {"flag": flag, "detail": detail[:300]}
        self.history.append(rec)
        acts = [Action("log", flag, detail=detail[:300])]
        if self.stopped:
            return acts
        if flag in SEVERE or self.flags_in_turn >= self.stop_after:
            self.stopped = True
            acts += [Action("stop", flag), Action("card", flag, CARD_SECRET if flag in SEVERE else CARD_REPEAT)]
            return acts
        if not self.steered:
            self.steered = True
            acts.append(Action("steer", flag, STEER_PREFIX + STEER[flag]))
        return acts
