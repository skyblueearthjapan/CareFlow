"""会話ごとのイベントの流れ: 保存（seq を付ける）→ 見ている画面（PC・スマホ）へ配る。

emit はどのスレッドからでも呼べる（反映はスレッドで動く）。配るのは各購読者の event loop へ call_soon_threadsafe。
"""

from __future__ import annotations

import asyncio
import threading
from typing import AsyncIterator

from relay import events as ev
from relay.store import Store


class EventBus:
    def __init__(self, store: Store):
        self.store = store
        self._subs: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}
        self._turn: dict[str, int] = {}
        self._lock = threading.Lock()

    def set_turn(self, session_id: str, turn_id: int) -> None:
        self._turn[session_id] = turn_id

    def emit(self, session_id: str, event: dict) -> dict | None:
        if ev.is_internal(event):
            return None
        full = self.store.append_event(session_id, self._turn.get(session_id, 0), event)
        with self._lock:
            subs = list(self._subs.get(session_id, []))
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(q.put_nowait, full)
            except RuntimeError:  # 閉じた loop
                pass
        return full

    async def subscribe(self, session_id: str, after: int = 0, follow: bool = True,
                        ping_seconds: float = 15.0, until: float | None = None) -> AsyncIterator[dict | None]:
        """保存済みの after より後を流し、follow なら新しいものを待つ。None はつなぎ止めの ping。"""
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subs.setdefault(session_id, []).append((loop, q))
        try:
            last = after
            for e in self.store.list_events(session_id, after):
                last = e["seq"]
                yield e
            if not follow:
                return
            while True:
                timeout = ping_seconds
                if until is not None:
                    left = until - loop.time()
                    if left <= 0:
                        return  # トークンの期限: 画面は新しいトークンで ?after= からつなぎ直す
                    timeout = min(timeout, left)
                try:
                    e = await asyncio.wait_for(q.get(), timeout)
                except asyncio.TimeoutError:
                    yield None
                    continue
                if e["seq"] <= last:
                    continue
                last = e["seq"]
                yield e
        finally:
            with self._lock:
                lst = self._subs.get(session_id, [])
                if (loop, q) in lst:
                    lst.remove((loop, q))
