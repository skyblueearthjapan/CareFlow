"""画面用の共通イベント（設計 §1-5）。Claude と Codex をこの形に揃える。

SSE の 1 件 = ``id: <seq>`` ＋ ``data: <JSON>``。JSON の共通部 ``{seq, ts, session_id, turn_id, type, ...}`` は
``relay.bus.EventBus`` が付ける。正規化（``relay.normalize``）が作るのは ``type`` と中身だけ。
``_meta`` で始まる type は中継の内部用（保存も配信もしない）。
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

State = Literal["idle", "starting", "working", "waiting_answer", "waiting_approval", "applying", "error",
                "rate_limited", "queued"]

#: 状態 → マスコットのポーズ（docs/mockups/mascot-candidates/rakusuke-pose-*）。
POSE = {
    "idle": "bow", "starting": "laptop", "working": "search", "waiting_answer": "waiting",
    "waiting_approval": "propose", "applying": "stamp", "error": "waiting", "rate_limited": "waiting",
    "queued": "waiting",
}

#: 状態 → 吹き出しの既定の 1 行（15〜25 字）。
LINE = {
    "idle": "ほかにも何かあれば、どうぞ",
    "starting": "準備しています",
    "working": "調べています",
    "waiting_answer": "教えていただけますか",
    "waiting_approval": "案を見て、決めてください",
    "applying": "反映しています",
    "error": "うまくいきませんでした",
    "rate_limited": "少し休んでから続けます",
    "queued": "順番を待っています",
}


class Common(TypedDict):
    seq: int
    ts: str
    session_id: str
    turn_id: int
    type: str


class StatusEvent(TypedDict):
    type: Literal["status"]
    state: State
    pose: str
    line: str
    resets_at: NotRequired[str]


class MessageDelta(TypedDict):
    type: Literal["message.delta"]
    text: str


class Message(TypedDict):
    type: Literal["message"]
    text: str


class UserMessage(TypedDict):
    type: Literal["user.message"]
    text: str
    user_id: str
    device: str


class Thinking(TypedDict):
    type: Literal["thinking"]
    phase: Literal["start", "end"]


class StepStart(TypedDict):
    type: Literal["step.start"]
    step_id: str
    label: str


class StepEnd(TypedDict):
    type: Literal["step.end"]
    step_id: str
    label: str
    ok: bool
    secs: float


class StepsSummary(TypedDict):
    type: Literal["steps.summary"]
    count: int
    failed: int
    secs: float
    label: str


class Progress(TypedDict):
    type: Literal["progress"]
    text: str


class QuestionOption(TypedDict):
    label: str
    description: NotRequired[str]


class QuestionItem(TypedDict):
    question: str
    header: NotRequired[str]
    options: list[QuestionOption]
    multiSelect: bool


class Question(TypedDict):
    type: Literal["question"]
    question_id: str
    questions: list[QuestionItem]


class QuestionAnswered(TypedDict):
    type: Literal["question.answered"]
    question_id: str
    answers: dict[str, str]
    user_id: str
    device: str


class Plan(TypedDict):
    type: Literal["plan"]
    plan_id: str
    version: int
    title: str
    summary: list[str]
    counts: dict[str, int]
    undoable: bool
    ready: bool
    blockers: list[str]
    html_urls: dict[str, str]
    status: str


class ApprovalRequest(TypedDict):
    type: Literal["approval.request"]
    kind: Literal["apply"]
    version: int
    plan_id: str


class ApprovalResult(TypedDict):
    type: Literal["approval.result"]
    version: int
    kind: Literal["apply", "undo"]
    decision: Literal["approve", "reject", "expired"]
    user_id: str
    device: str
    expires_at: NotRequired[str]
    run_id: NotRequired[str]


class RunProgress(TypedDict):
    type: Literal["run.progress"]
    run_kind: str
    i: int
    total: int
    label: str
    ok: bool


class RunResult(TypedDict):
    type: Literal["run.result"]
    run_kind: str
    run_id: str
    ok: bool
    applied: int
    total: int
    error: str | None
    undoable: bool
    backup: str | None


class ErrorEvent(TypedDict):
    type: Literal["error"]
    code: str
    message: str
    retry: bool


class TurnEnd(TypedDict):
    type: Literal["turn.end"]
    secs: float
    ok: bool
    reason: str


EVENT_TYPES = {
    "status", "message.delta", "message", "user.message", "thinking", "step.start", "step.end", "steps.summary",
    "progress", "question", "question.answered", "plan", "approval.request", "approval.result", "run.progress", "run.result",
    "error", "turn.end",
}


def status(state: State, line: str | None = None, **extra: Any) -> dict:
    return {"type": "status", "state": state, "pose": POSE[state], "line": line or LINE[state], **extra}


def error(code: str, message: str, retry: bool = True) -> dict:
    return {"type": "error", "code": code, "message": message, "retry": retry}


def is_internal(ev: dict) -> bool:
    return str(ev.get("type", "")).startswith("_")


def validate(ev: dict) -> list[str]:
    """画面へ出すイベントの最低限の形（試験と保存の前に使う）。"""
    t = ev.get("type")
    if t not in EVENT_TYPES:
        return [f"知らない type: {t}"]
    need = {
        "status": ("state", "pose", "line"), "message.delta": ("text",), "message": ("text",),
        "thinking": ("phase",), "step.start": ("step_id", "label"), "step.end": ("step_id", "label", "ok", "secs"),
        "question": ("question_id", "questions"), "plan": ("plan_id", "version", "status"),
        "approval.request": ("kind", "version", "plan_id"),
        "approval.result": ("version", "kind", "decision"),
        "error": ("code", "message", "retry"), "turn.end": ("secs", "ok"),
    }.get(t, ())
    return [f"{t} に {k} がありません" for k in need if k not in ev]
