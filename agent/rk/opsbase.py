"""操作の部品の形（設計書 §3-4・担当どうしの約束）。

    from rk.opsbase import Op, register, Api, ApiError

    @register
    class AssignStaffWeek(Op):
        name = "assign-staff-week"          # plan.json の "op"
        summary = "今週だけ担当を付け替える"
        undoable = True                     # False = 戻すにはバックアップから（案の A4 に出る）
        def validate(self, op) -> list[str]: ...          # 任意: 形の検査（rk plan check）
        def request(self, op) -> tuple[str, str, dict | None]: ...  # (method, path, body)・path は /api/v1 の後ろ
        def snapshot(self, api, op) -> dict: ...          # 反映前の状態
        def inverse(self, op, before) -> list[dict] | None: ...     # 元に戻す操作（None = 戻せない）
        def verify(self, api, op) -> list[str]: ...       # 反映後の食い違い（空なら OK）
        def check_response(self, op, response) -> list[str]: ...    # 任意: 2xx でも想定外なら理由を返す（止まる）

Api は get(path, params=None) / call(method, path, body=None) を持ち、2xx 以外は ApiError(status, body) を投げる。
"""

from __future__ import annotations

import importlib
import sys
from typing import Any, Protocol, runtime_checkable


class ApiError(Exception):
    def __init__(self, status: int, body: Any = None):
        self.status = status
        self.body = body
        text = body if isinstance(body, str) else repr(body)
        super().__init__(f"HTTP {status}: {str(text)[:300]}")


@runtime_checkable
class Api(Protocol):
    def get(self, path: str, params: dict | None = None) -> Any: ...

    def call(self, method: str, path: str, body: Any = None) -> Any: ...


class Op:
    name: str = ""
    summary: str = ""
    undoable: bool = True

    def validate(self, op: dict) -> list[str]:
        return []

    def request(self, op: dict) -> tuple[str, str, dict | None]:
        raise NotImplementedError

    def snapshot(self, api: Api, op: dict) -> dict:
        return {}

    def inverse(self, op: dict, before: dict) -> list[dict] | None:
        return None

    def verify(self, api: Api, op: dict) -> list[str]:
        return []

    def undo_note(self, op: dict, before: dict | None = None) -> str | None:
        """API で戻せないときの理由（日本語）。戻せるなら None。中身で変わる操作はこれを上書きする。"""
        return None if self.undoable else "バックアップから"

    def check_response(self, op: dict, response: Any) -> list[str]:
        return []

    def execute(self, api: Api, op: dict) -> Any:
        """request() の 1 回の API 呼び出し。複数回呼ぶ操作はこれを上書きしてよい。"""
        method, path, body = self.request(op)
        if method.upper() == "GET":
            return api.get(path)
        return api.call(method.upper(), path, body)


class Registry:
    def __init__(self) -> None:
        self._ops: dict[str, Op] = {}

    def add(self, cls: type[Op]) -> None:
        name = getattr(cls, "name", "")
        if not name:
            raise ValueError(f"{cls.__name__}: name が空です")
        old = self._ops.get(name)
        if old is not None and (type(old).__module__, type(old).__qualname__) != (cls.__module__, cls.__qualname__):
            raise ValueError(f"操作名 {name} が 2 回登録されています（{type(old).__module__} と {cls.__module__}）")
        self._ops[name] = cls()

    def get(self, name: str) -> Op | None:
        return self._ops.get(name)

    def list(self) -> list[Op]:
        return [self._ops[k] for k in sorted(self._ops)]

    def names(self) -> list[str]:
        return sorted(self._ops)


registry = Registry()
IMPORT_ERRORS: list[str] = []


def register(cls: type[Op]) -> type[Op]:
    registry.add(cls)
    return cls


def get_op(name: str) -> Op | None:
    return registry.get(name)


def list_ops() -> list[Op]:
    return registry.list()


def load_all() -> Registry:
    """rk/ops/ の全部品を読み込む（rk.ops の __init__ が各モジュールを import する）。"""
    importlib.import_module("rk.ops")
    for msg in IMPORT_ERRORS:
        print(f"警告: 操作の部品を読み込めません: {msg}", file=sys.stderr)
    return registry
