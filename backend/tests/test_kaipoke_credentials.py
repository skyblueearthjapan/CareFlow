"""カイポケ ログイン情報のアプリ内設定 (C-1) のテスト.

docs/plans/kaipoke-credentials-config-design.md:
  * PUT で暗号化保存 / GET はパスワードを絶対に返さない
  * ジョブ起動 (diff-local の export) の HTTP body に credentials が同梱される
  * KaipokeJob.params には credentials が残らない
  * /credentials/test は保存済み認証情報で login-test を呼ぶ
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import User
from app.models.kaipoke_credential import KaipokeCredential
from app.models.kaipoke_job import KaipokeJob
from app.services import kaipoke_client as kc_module
from app.services.kaipoke.credentials import decrypt_password


class StubKaipokeClient:
    def __init__(self) -> None:
        self.responses: dict[str, Any] = {}
        self.calls: list[tuple[str, Any]] = []

    async def aclose(self) -> None:  # pragma: no cover
        pass

    def _dispatch(self, name: str, payload: Any) -> dict[str, Any]:
        self.calls.append((name, payload))
        return self.responses.get(name, {})

    async def export(
        self, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        return self._dispatch("export", payload)

    async def expand(
        self, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        return self._dispatch("expand", payload)

    async def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._dispatch("apply", payload)

    async def login_test(
        self, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        return self._dispatch("login_test", payload)


@pytest.fixture
def stub_kaipoke():
    stub = StubKaipokeClient()
    kc_module.set_test_client(stub)  # type: ignore[arg-type]
    try:
        yield stub
    finally:
        kc_module.set_test_client(None)


async def _make_user(db, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("does-not-matter-here"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


CREDS = {"corpId": "123456", "userId": "test-user", "password": "s3cret-pass"}


@pytest.mark.asyncio
async def test_put_and_get_credentials_never_returns_password(client, db) -> None:
    admin = await _make_user(db, "cred-admin@example.com", "admin")

    res = await client.get("/api/v1/integrations/credentials", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json() == {
        "configured": False,
        "corpId": None,
        "userId": None,
        "updatedAt": None,
    }

    res = await client.put("/api/v1/integrations/credentials", headers=_bearer(admin), json=CREDS)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["configured"] is True
    assert body["corpId"] == "123456"
    assert body["userId"] == "test-user"
    assert "password" not in body

    res = await client.get("/api/v1/integrations/credentials", headers=_bearer(admin))
    assert res.json()["configured"] is True
    assert "password" not in res.json()

    # DB には暗号化された形でのみ保存され、復号で元に戻る。
    row = await db.scalar(select(KaipokeCredential))
    assert row is not None
    assert "s3cret-pass" not in row.password_encrypted
    assert decrypt_password(row.password_encrypted) == "s3cret-pass"


@pytest.mark.asyncio
async def test_credentials_rbac_admin_only(client, db) -> None:
    manager = await _make_user(db, "cred-manager@example.com", "manager")
    res = await client.get("/api/v1/integrations/credentials", headers=_bearer(manager))
    assert res.status_code == 403
    res = await client.put("/api/v1/integrations/credentials", headers=_bearer(manager), json=CREDS)
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_upsert_overwrites_single_row(client, db) -> None:
    admin = await _make_user(db, "cred-admin2@example.com", "admin")
    await client.put("/api/v1/integrations/credentials", headers=_bearer(admin), json=CREDS)
    await client.put(
        "/api/v1/integrations/credentials",
        headers=_bearer(admin),
        json={**CREDS, "userId": "second-user", "password": "next-pass"},
    )
    rows = (await db.scalars(select(KaipokeCredential))).all()
    assert len(rows) == 1
    assert rows[0].user_id == "second-user"
    assert decrypt_password(rows[0].password_encrypted) == "next-pass"


@pytest.mark.asyncio
async def test_diff_local_attaches_credentials_to_export_body_only(
    client, db, stub_kaipoke
) -> None:
    """export の HTTP body に credentials が載り、KaipokeJob.params には残らない。"""
    admin = await _make_user(db, "cred-admin3@example.com", "admin")
    stub_kaipoke.responses["export"] = {"result": {"csv_content": ""}}

    # 未設定時: credentials は同梱されない (RPA は env フォールバック)。
    res = await client.post(
        "/api/v1/integrations/diff-local",
        headers=_bearer(admin),
        json={"month": "2026-07"},
    )
    assert res.status_code == 202, res.text
    _, payload = stub_kaipoke.calls[-1]
    assert "credentials" not in payload

    # 設定後: export body に同梱される。
    await client.put("/api/v1/integrations/credentials", headers=_bearer(admin), json=CREDS)
    res = await client.post(
        "/api/v1/integrations/diff-local",
        headers=_bearer(admin),
        json={"month": "2026-07"},
    )
    assert res.status_code == 202, res.text
    _, payload = stub_kaipoke.calls[-1]
    assert payload["credentials"] == {
        "corp_id": "123456",
        "user_id": "test-user",
        "password": "s3cret-pass",
    }

    # KaipokeJob.params には credentials が絶対に残らない (DB平文防止)。
    jobs = (await db.scalars(select(KaipokeJob))).all()
    for job in jobs:
        assert "credentials" not in (job.params or {})
        assert "s3cret-pass" not in str(job.params)


@pytest.mark.asyncio
async def test_expand_and_apply_attach_credentials(client, db, stub_kaipoke) -> None:
    """expand も (diff-local 以外の代表経路として) credentials を同梱する。"""
    admin = await _make_user(db, "cred-admin5@example.com", "admin")
    await client.put("/api/v1/integrations/credentials", headers=_bearer(admin), json=CREDS)
    stub_kaipoke.responses["expand"] = {"jobId": "kp-1"}

    res = await client.post(
        "/api/v1/integrations/expand",
        headers=_bearer(admin),
        json={"month": "2026-07", "dryRun": True},
    )
    assert res.status_code == 202, res.text
    name, payload = stub_kaipoke.calls[-1]
    assert name == "expand"
    assert payload["credentials"]["corp_id"] == "123456"
    assert payload["credentials"]["password"] == "s3cret-pass"

    jobs = (await db.scalars(select(KaipokeJob))).all()
    for job in jobs:
        assert "credentials" not in (job.params or {})


@pytest.mark.asyncio
async def test_login_test_endpoint(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "cred-admin4@example.com", "admin")

    # 未設定 → 422。
    res = await client.post("/api/v1/integrations/credentials/test", headers=_bearer(admin))
    assert res.status_code == 422, res.text

    await client.put("/api/v1/integrations/credentials", headers=_bearer(admin), json=CREDS)
    stub_kaipoke.responses["login_test"] = {"ok": True, "message": "ログイン成功"}
    res = await client.post("/api/v1/integrations/credentials/test", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True

    # RPA へは credentials が渡っている。
    name, payload = stub_kaipoke.calls[-1]
    assert name == "login_test"
    assert payload["credentials"]["password"] == "s3cret-pass"

    # 監査ジョブ (op=login-test) が completed で残る。
    job = await db.scalar(select(KaipokeJob).order_by(KaipokeJob.created_at.desc()).limit(1))
    assert job is not None and job.params.get("op") == "login-test"
    assert job.status == "completed"
