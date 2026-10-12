"""承認番号（ap_…）の発行と検査（設計 §3-3）。

番号を作れるのは公開口の承認 API（人のログイン＝admin の Bearer）だけ。箱の中のモデルは公開口へ届かない。
/rk/apply・/rk/undo は次をすべて満たす時だけ書く:
 1. 番号が記録にあり、decision=approve・kind が一致・未使用・期限内
 2. 番号の会話が、呼んできた RK_RELAY_TOKEN の会話と同じ（承認した人がその会話の持ち主）
 3. 送られてきた案（undo は undo.json）の sha256 が承認した時の sha256 と一致
 4. apply: その版が会話の最新版で、状態が approved／undo: 対象の反映が同じ
 5. 使ったら consumed_at（同じ番号で 2 回は書けない）
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta
from typing import Any

from relay.store import Store

CODE_RE = re.compile(r"^ap_[A-Za-z0-9_-]{8,64}$")


class Refused(Exception):
    """承認を通さない（理由は平易な日本語・rk がそのまま見せる）。"""

    def __init__(self, message: str, status: int = 403):
        super().__init__(message)
        self.message = message
        self.status = status


def plan_sha256(plan: Any) -> str:
    canon = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def new_code() -> str:
    return "ap_" + secrets.token_urlsafe(18)


def issue(store: Store, *, session: dict, user_id: str, kind: str, sha: str, now: datetime, ttl_seconds: int,
          plan: dict | None = None, target_run_id: str | None = None, device: str = "pc") -> dict:
    if str(session["user_id"]) != str(user_id):
        raise Refused("この相談を始めた人だけが承認できます", 403)
    store.replace_open_approvals(session["id"], kind, plan["id"] if plan else None, target_run_id)
    return store.add_approval(
        id=new_code(), session_id=session["id"], plan_id=plan["id"] if plan else None,
        plan_version=plan["version"] if plan else None, plan_sha256=sha, kind=kind, target_run_id=target_run_id,
        user_id=str(user_id), device=device, decision="approve", expires_at=now + timedelta(seconds=ttl_seconds))


def check(store: Store, *, code: str, session: dict, kind: str, sha: str, now: datetime,
          target_run_id: str | None = None) -> tuple[dict, dict | None]:
    """番号を確かめる（まだ使用済みにはしない）。戻り値: (承認, 案)。"""
    if not CODE_RE.match(code or ""):
        raise Refused("承認番号の形が違います。人が画面で［この案で反映する］を押すと番号が届きます", 400)
    ap = store.get_approval(code)
    if ap is None:
        raise Refused("その承認番号はありません。人が画面で承認した番号だけが使えます")
    if ap["session_id"] != session["id"]:
        raise Refused("その承認番号は別の相談のものです")
    if str(ap["user_id"]) != str(session["user_id"]):
        raise Refused("その承認番号はこの相談を始めた人のものではありません")
    if ap["kind"] != kind:
        raise Refused("その承認番号は別の種類（反映／元に戻す）のものです")
    if ap["decision"] != "approve":
        raise Refused("その承認番号は使えません（新しい案が出た・取り消された）", 409)
    if ap["consumed_at"] is not None:
        raise Refused("その承認番号はもう使われています（同じ番号で 2 回は反映できません）", 409)
    if now >= ap["expires_at"]:
        raise Refused("承認番号の期限が切れました。画面でもう一度押してもらってください", 410)
    if sha != ap["plan_sha256"]:
        raise Refused("承認された案と中身が違います（承認の後に案が変わりました）。案を出し直して承認を取り直してください",
                      409)
    plan = None
    if kind == "apply":
        latest = store.latest_plan(session["id"])
        if latest is None or latest["id"] != ap["plan_id"]:
            raise Refused("承認された案は最新の版ではありません", 409)
        if latest["status"] != "approved":
            raise Refused(f"承認された案の状態が「{latest['status']}」です", 409)
        if latest["plan_sha256"] != sha:
            raise Refused("承認された案と中身が違います", 409)
        plan = latest
    else:
        if ap["target_run_id"] != target_run_id:
            raise Refused("その承認番号は別の反映を元に戻すためのものです")
    return ap, plan
