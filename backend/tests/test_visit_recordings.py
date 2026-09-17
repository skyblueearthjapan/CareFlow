"""訪問の音声記録 (Phase 1 BE) のテスト.

正典設計書: ``docs/plans/visit-voice-record-design-2026-09-17.md`` §2-4〜§2-6 / §10。

網羅:

* 受領 (202・ファイル生成・status・``unlinked``)、413 / 415 / 422 (同意)。
* staff 担当外の ``visit_id`` は 404 (存在ごと秘匿)。
* 一覧は staff に対して ``staff_id`` を強制 (指定しても他人は出ない) + ``total``。
* 詳細 / 音声配信 (Range 無しで 200・削除後 410・``audit_logs`` に ``audio_read``)。
* 受領の入口ガード: Content-Length で読む前に 413 / ``recorded_at`` の範囲 /
  ``duration_sec`` の範囲 / ``client_id`` の冪等 (2 回目は 200 + 同一行)。
* PATCH の紐付け: **訪問は作らない** (設計変更 2026-09-18)。既存訪問があれば
  再利用し (帰属は ``_staff_visibility_filter`` = 主担当だけではない)、無ければ
  ``visit_id`` は NULL のまま患者にだけ紐付く。非稼働患者にも紐付けられる (録音は事実の記録)。付け替え /
  ``visit_id: null`` は紐付けを外すだけで **訪問には触らない**。
  24 時間を過ぎた紐付け変更は staff に 403。
* 監査ミドルウェアの body バイパス規則 (``_skips_body_buffering``) の単体。
* admin の retry / delete、保持期間パージ (**created_at 基準**・冪等・下限は 422)、
  ``transcribing`` 残骸の stale reap。

``VISIT_AUDIO_DIR`` は ``tmp_path`` へモンキーパッチする (本番既定の
``/opt/carelink/data/visit_audio`` に書かせない)。``VOICE_AI_PROVIDER='none'``
にして BackgroundTasks の中身を no-op にする。
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.middleware.audit import _skips_body_buffering
from app.models import AuditLog, Office, Patient, Staff, User, Visit
from app.models.visit_recording import VisitRecording
from app.services.checkin.judge import JST
from app.services.voice.jobs import STALE_ERROR_MESSAGE, _save_result, reap_stale_jobs
from app.services.voice.vertex_client import VoiceAiResult


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def audio_dir(tmp_path, monkeypatch) -> Path:
    """``VISIT_AUDIO_DIR`` を tmp_path に差し替える (設定は lru_cache なので clear)。"""
    root = tmp_path / "visit_audio"
    monkeypatch.setenv("VISIT_AUDIO_DIR", str(root))
    monkeypatch.setenv("VOICE_AI_PROVIDER", "none")
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def staff_user(db) -> tuple[Staff, User]:
    staff = Staff(name="録音 花子")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    user = User(
        email="voice-staff@example.com",
        password_hash=hash_password("x"),
        role="staff",
        staff_id=staff.id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return staff, user


@pytest_asyncio.fixture
async def admin_user(db) -> User:
    user = User(email="voice-admin@example.com", password_hash=hash_password("x"), role="admin")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_patient(db, code: str, *, status: str = "active", **kwargs) -> Patient:
    p = Patient(code=code, name=f"利用者{code}", status=status, **kwargs)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


def _jst_today():
    """録音の日付境界は JST。UTC の日付を使うと夕方以降に 1 日ずれる。"""
    return datetime.now(UTC).astimezone(JST).date()


async def _make_visit(db, patient_id, staff_id, **overrides) -> Visit:
    fields = {
        "patient_id": patient_id,
        "primary_staff_id": staff_id,
        "visit_date": _jst_today(),
        "start_time": time(9, 0),
        "end_time": time(10, 0),
        "type": "regular",
        "status": "planned",
    }
    fields.update(overrides)
    visit = Visit(**fields)
    db.add(visit)
    await db.commit()
    await db.refresh(visit)
    return visit


def _upload_files(payload: bytes = b"fake-audio-bytes", mime: str = "audio/webm") -> dict:
    return {"audio": ("rec.webm", payload, mime)}


def _upload_form(**overrides) -> dict:
    data = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "duration_sec": "120",
        "consent": "true",
    }
    data.update({k: v for k, v in overrides.items() if v is not None})
    return data


async def _post_recording(client, user, *, files=None, **form) -> object:
    return await client.post(
        "/api/v1/visit-recordings",
        headers=_bearer(user),
        files=files or _upload_files(),
        data=_upload_form(**form),
    )


# ---------------------------------------------------------------------------
# 受領 (POST)


@pytest.mark.asyncio
async def test_upload_creates_row_and_file(client, db, audio_dir, staff_user) -> None:
    """202 + ファイル生成 + status=uploaded (visit 紐付きは患者も引き継ぐ)."""
    staff, user = staff_user
    patient = await _make_patient(db, "VR-1")
    visit = await _make_visit(db, patient.id, staff.id)

    res = await _post_recording(client, user, visit_id=str(visit.id))
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["status"] == "uploaded"
    assert body["visit_id"] == str(visit.id)
    assert body["patient_id"] == str(patient.id)
    assert body["staff_id"] == str(staff.id)
    assert body["staff_name"] == "録音 花子"
    assert body["has_audio"] is True
    assert body["audio_mime"] == "audio/webm"
    assert body["audio_bytes"] == len(b"fake-audio-bytes")
    assert body["consent_confirmed"] is True
    # ended_at = recorded_at + duration
    assert body["ended_at"] is not None

    row = await db.scalar(select(VisitRecording).where(VisitRecording.id == UUID(body["id"])))
    stored = Path(row.audio_path)
    assert stored.exists()
    assert stored.read_bytes() == b"fake-audio-bytes"
    # {VISIT_AUDIO_DIR}/{yyyy}/{mm}/{id}.{ext}
    assert stored.suffix == ".webm"
    assert stored.parent.parent.parent == audio_dir
    # 書きかけの .part は残さない。
    assert list(stored.parent.glob("*.part")) == []
    await db.rollback()


@pytest.mark.asyncio
async def test_upload_without_patient_is_unlinked(client, db, audio_dir, staff_user) -> None:
    """visit も patient も無い受領は許可され status=unlinked になる."""
    _staff, user = staff_user
    res = await _post_recording(client, user)
    assert res.status_code == 202, res.text
    assert res.json()["status"] == "unlinked"
    assert res.json()["patient_id"] is None
    await db.rollback()


@pytest.mark.asyncio
async def test_upload_too_large_returns_413(client, db, audio_dir, staff_user, monkeypatch) -> None:
    """累積が VISIT_AUDIO_MAX_BYTES を超えたら 413 で打ち切り、ファイルも残さない."""
    _staff, user = staff_user
    monkeypatch.setenv("VISIT_AUDIO_MAX_BYTES", "1024")
    get_settings.cache_clear()

    res = await _post_recording(client, user, files=_upload_files(b"x" * 4096))
    assert res.status_code == 413, res.text
    assert list(audio_dir.rglob("*")) == [] or not any(p.is_file() for p in audio_dir.rglob("*"))
    assert (await db.scalar(select(VisitRecording))) is None
    await db.rollback()


@pytest.mark.asyncio
async def test_upload_non_audio_mime_returns_415(client, db, audio_dir, staff_user) -> None:
    _staff, user = staff_user
    res = await _post_recording(client, user, files={"audio": ("x.png", b"png", "image/png")})
    assert res.status_code == 415, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_upload_empty_file_returns_422(client, db, audio_dir, staff_user) -> None:
    _staff, user = staff_user
    res = await _post_recording(client, user, files=_upload_files(b""))
    assert res.status_code == 422, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_upload_without_consent_returns_422(client, db, audio_dir, staff_user) -> None:
    _staff, user = staff_user
    res = await client.post(
        "/api/v1/visit-recordings",
        headers=_bearer(user),
        files=_upload_files(),
        data={
            "recorded_at": datetime.now(UTC).isoformat(),
            "duration_sec": "10",
            "consent": "false",
        },
    )
    assert res.status_code == 422, res.text
    assert (await db.scalar(select(VisitRecording))) is None
    await db.rollback()


@pytest.mark.asyncio
async def test_upload_to_foreign_visit_returns_404(client, db, audio_dir, staff_user) -> None:
    """担当外の visit_id を付けた受領は 404 (存在ごと秘匿)."""
    _staff, user = staff_user
    other = Staff(name="他人 太郎")
    db.add(other)
    await db.commit()
    await db.refresh(other)
    patient = await _make_patient(db, "VR-FOREIGN")
    visit = await _make_visit(db, patient.id, other.id)

    res = await _post_recording(client, user, visit_id=str(visit.id))
    assert res.status_code == 404, res.text
    assert (await db.scalar(select(VisitRecording))) is None
    await db.rollback()


@pytest.mark.asyncio
async def test_upload_multipart_audit_row_has_marker(client, db, audio_dir, staff_user) -> None:
    """監査は multipart の body を読まず ``_multipart`` マーカーだけ残す."""
    import asyncio

    _staff, user = staff_user
    res = await _post_recording(client, user)
    assert res.status_code == 202, res.text

    row = None
    for _ in range(25):
        row = await db.scalar(
            select(AuditLog).where(
                AuditLog.method == "POST",
                AuditLog.path.like("%/visit-recordings%"),
            )
        )
        if row is not None:
            break
        await asyncio.sleep(0.02)
    assert row is not None
    assert row.request_body == {"_multipart": True}
    await db.rollback()


# ---------------------------------------------------------------------------
# 一覧 / 詳細 / 音声


@pytest.mark.asyncio
async def test_list_forces_own_staff_id_for_staff(client, db, audio_dir, staff_user) -> None:
    """staff は staff_id を指定しても自分の録音しか見えない (total も自分の分だけ)."""
    staff, user = staff_user
    other_staff = Staff(name="他人 太郎")
    db.add(other_staff)
    await db.commit()
    await db.refresh(other_staff)

    await _post_recording(client, user)
    # 他人の録音を直接 seed する (API からは作れないため)。
    db.add(
        VisitRecording(
            staff_id=other_staff.id,
            recorded_at=datetime.now(UTC),
            duration_sec=30,
            status="unlinked",
            consent_confirmed=True,
        )
    )
    await db.commit()

    res = await client.get(
        "/api/v1/visit-recordings",
        headers=_bearer(user),
        params={"staff_id": str(other_staff.id)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 1
    assert [i["staff_id"] for i in body["items"]] == [str(staff.id)]
    # 一覧では全文を返さない。
    assert body["items"][0]["transcript"] is None
    await db.rollback()


@pytest.mark.asyncio
async def test_detail_and_audio_download(client, db, audio_dir, staff_user) -> None:
    """詳細 200 + 音声 200 (Accept-Ranges) + audit_logs に audio_read が 1 行."""
    _staff, user = staff_user
    created = (await _post_recording(client, user)).json()

    res = await client.get(f"/api/v1/visit-recordings/{created['id']}", headers=_bearer(user))
    assert res.status_code == 200, res.text
    assert res.json()["id"] == created["id"]

    res = await client.get(f"/api/v1/visit-recordings/{created['id']}/audio", headers=_bearer(user))
    assert res.status_code == 200, res.text
    assert res.headers.get("accept-ranges") == "bytes"
    assert res.content == b"fake-audio-bytes"

    rows = (await db.scalars(select(AuditLog).where(AuditLog.action == "audio_read"))).all()
    assert len(rows) == 1
    assert rows[0].target_table == "visit_recordings"
    assert rows[0].target_id == created["id"]
    await db.rollback()


@pytest.mark.asyncio
async def test_audio_after_purge_returns_410(client, db, audio_dir, staff_user) -> None:
    _staff, user = staff_user
    created = (await _post_recording(client, user)).json()
    row = await db.scalar(select(VisitRecording).where(VisitRecording.id == UUID(created["id"])))
    row.audio_path = None
    row.audio_deleted_at = datetime.now(UTC)
    await db.commit()

    res = await client.get(f"/api/v1/visit-recordings/{created['id']}/audio", headers=_bearer(user))
    assert res.status_code == 410, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_detail_of_other_staff_recording_is_404(client, db, audio_dir, staff_user) -> None:
    _staff, user = staff_user
    other_staff = Staff(name="他人 太郎")
    db.add(other_staff)
    await db.commit()
    await db.refresh(other_staff)
    row = VisitRecording(
        staff_id=other_staff.id,
        recorded_at=datetime.now(UTC),
        duration_sec=30,
        status="unlinked",
        consent_confirmed=True,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    res = await client.get(f"/api/v1/visit-recordings/{row.id}", headers=_bearer(user))
    assert res.status_code == 404, res.text
    await db.rollback()


# ---------------------------------------------------------------------------
# PATCH (紐付け — 訪問は作らない)


@pytest.mark.asyncio
async def test_patch_patient_without_visit_leaves_visit_id_null(
    client, db, audio_dir, staff_user
) -> None:
    """その日に訪問が無ければ ``visit_id`` は NULL のまま患者にだけ紐付く.

    受け皿の訪問を起こすと、モニター・通知・プール・代替提案・実現性チェック・
    Layer1 へ「その訪問を除外する」処理が波及して漏れの温床になる。録音は
    「患者は分かっているが訪問は無い」状態を素直に持てる。
    """
    _staff, user = staff_user
    patient = await _make_patient(db, "VR-LINK")
    created = (await _post_recording(client, user, duration_sec="600")).json()
    assert created["status"] == "unlinked"

    res = await client.patch(
        f"/api/v1/visit-recordings/{created['id']}",
        headers=_bearer(user),
        json={"patient_id": str(patient.id)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["patient_id"] == str(patient.id)
    assert body["patient_name"] == patient.name
    assert body["visit_id"] is None
    assert body["status"] == "uploaded"

    # 訪問は 1 件も生えていない。
    assert (await db.scalars(select(Visit))).all() == []
    await db.rollback()


@pytest.mark.asyncio
async def test_patch_inactive_patient_is_allowed(client, db, audio_dir, staff_user) -> None:
    """入院中など非稼働の患者にも紐付けられる (録音は予定ではなく事実の記録)."""
    _staff, user = staff_user
    patient = await _make_patient(db, "VR-OFF", status="admitted")
    created = (await _post_recording(client, user)).json()

    res = await client.patch(
        f"/api/v1/visit-recordings/{created['id']}",
        headers=_bearer(user),
        json={"patient_id": str(patient.id)},
    )
    assert res.status_code == 200, res.text
    assert res.json()["patient_id"] == str(patient.id)
    assert res.json()["visit_id"] is None
    await db.rollback()


@pytest.mark.asyncio
async def test_patch_unknown_patient_returns_404(client, db, audio_dir, staff_user) -> None:
    _staff, user = staff_user
    created = (await _post_recording(client, user)).json()
    res = await client.patch(
        f"/api/v1/visit-recordings/{created['id']}",
        headers=_bearer(user),
        json={"patient_id": str(uuid4())},
    )
    assert res.status_code == 404, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_patch_patient_does_not_reuse_other_staff_visit(
    client, db, audio_dir, staff_user
) -> None:
    """同じ患者・同じ日でも、録音者に帰属しない訪問は再利用しない (visit_id は NULL)."""
    _staff, user = staff_user
    other = Staff(name="他人 太郎")
    db.add(other)
    await db.commit()
    await db.refresh(other)
    patient = await _make_patient(db, "VR-NOREUSE")
    foreign = await _make_visit(db, patient.id, other.id)
    created = (await _post_recording(client, user)).json()

    res = await client.patch(
        f"/api/v1/visit-recordings/{created['id']}",
        headers=_bearer(user),
        json={"patient_id": str(patient.id)},
    )
    assert res.status_code == 200, res.text
    assert res.json()["patient_id"] == str(patient.id)
    assert res.json()["visit_id"] is None
    assert res.json()["visit_id"] != str(foreign.id)
    await db.rollback()


@pytest.mark.asyncio
async def test_patch_reviewed_and_note_append(client, db, audio_dir, staff_user) -> None:
    _staff, user = staff_user
    created = (await _post_recording(client, user)).json()

    res = await client.patch(
        f"/api/v1/visit-recordings/{created['id']}",
        headers=_bearer(user),
        json={"reviewed": True, "note_append": "血圧は安定"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["reviewed_by"] == str(user.id)
    assert body["reviewed_at"] is not None
    assert body["summary_text"] == "血圧は安定"
    await db.rollback()


# ---------------------------------------------------------------------------
# admin: retry / delete


@pytest.mark.asyncio
async def test_admin_retry_resets_status(client, db, audio_dir, staff_user, admin_user) -> None:
    _staff, user = staff_user
    created = (await _post_recording(client, user)).json()
    row = await db.scalar(select(VisitRecording).where(VisitRecording.id == UUID(created["id"])))
    row.status = "failed"
    row.error_message = "boom"
    await db.commit()

    res = await client.post(
        f"/api/v1/visit-recordings/{created['id']}/retry", headers=_bearer(admin_user)
    )
    assert res.status_code == 202, res.text
    assert res.json()["status"] == "uploaded"
    assert res.json()["error_message"] is None

    # staff は叩けない。
    res = await client.post(
        f"/api/v1/visit-recordings/{created['id']}/retry", headers=_bearer(user)
    )
    assert res.status_code == 403, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_admin_delete_soft_deletes_and_unlinks(
    client, db, audio_dir, staff_user, admin_user
) -> None:
    _staff, user = staff_user
    created = (await _post_recording(client, user)).json()
    row = await db.scalar(select(VisitRecording).where(VisitRecording.id == UUID(created["id"])))
    stored = Path(row.audio_path)
    assert stored.exists()

    res = await client.delete(
        f"/api/v1/visit-recordings/{created['id']}", headers=_bearer(admin_user)
    )
    assert res.status_code == 204, res.text

    await db.refresh(row)
    assert row.deleted_at is not None
    assert row.audio_path is None
    assert row.audio_deleted_at is not None
    assert not stored.exists()

    # soft delete 後は詳細も 404。
    res = await client.get(f"/api/v1/visit-recordings/{created['id']}", headers=_bearer(admin_user))
    assert res.status_code == 404, res.text
    await db.rollback()


# ---------------------------------------------------------------------------
# 保持期間パージ / stale reap


@pytest.mark.asyncio
async def test_purge_audio_only_removes_expired_and_is_idempotent(
    client, db, audio_dir, staff_user, admin_user, monkeypatch
) -> None:
    """保持超の音声だけ消え、文字起こしは残り、二度目は 0 件 (冪等)."""
    _staff, user = staff_user
    monkeypatch.setenv("VISIT_AUDIO_RETENTION_DAYS", "30")
    get_settings.cache_clear()

    fresh = (await _post_recording(client, user)).json()
    old = (await _post_recording(client, user)).json()

    old_row = await db.scalar(select(VisitRecording).where(VisitRecording.id == UUID(old["id"])))
    # 経過は **created_at (サーバー受領時刻)** で数える。recorded_at は端末の
    # 自己申告なので、こちらを古くしてもパージ対象にはならない (下で確かめる)。
    old_row.created_at = datetime.now(UTC) - timedelta(days=100)
    old_row.transcript = "全文テキスト"
    await db.commit()
    old_path = Path(old_row.audio_path)
    fresh_row = await db.scalar(
        select(VisitRecording).where(VisitRecording.id == UUID(fresh["id"]))
    )
    fresh_path = Path(fresh_row.audio_path)

    res = await client.post(
        "/api/v1/admin/visit-recordings/purge-audio", headers=_bearer(admin_user)
    )
    assert res.status_code == 200, res.text
    assert res.json()["purged"] == 1

    await db.refresh(old_row)
    await db.refresh(fresh_row)
    assert old_row.audio_path is None
    assert old_row.audio_deleted_at is not None
    assert old_row.transcript == "全文テキスト"
    assert not old_path.exists()
    assert fresh_row.audio_path is not None
    assert fresh_path.exists()

    # 二度目は no-op。
    res = await client.post(
        "/api/v1/admin/visit-recordings/purge-audio", headers=_bearer(admin_user)
    )
    assert res.status_code == 200, res.text
    assert res.json()["purged"] == 0
    await db.rollback()


@pytest.mark.asyncio
async def test_purge_audio_rejects_retention_below_floor(
    client, db, audio_dir, admin_user, monkeypatch
) -> None:
    """下限 (30 日) 未満の設定は記録を焼く前に落とす."""
    monkeypatch.setenv("VISIT_AUDIO_RETENTION_DAYS", "7")
    get_settings.cache_clear()

    res = await client.post(
        "/api/v1/admin/visit-recordings/purge-audio", headers=_bearer(admin_user)
    )
    # 設定値が不正 = 422 (500 ではない)。1 バイトも消していないことを区別できる。
    assert res.status_code == 422, res.text
    assert ">= 30" in res.json()["detail"]
    await db.rollback()


@pytest.mark.asyncio
async def test_purge_audio_ignores_device_clock(
    client, db, audio_dir, staff_user, admin_user, monkeypatch
) -> None:
    """端末が「2 年前」を名乗っても、受領した翌日に音声が消えたりしない."""
    _staff, user = staff_user
    monkeypatch.setenv("VISIT_AUDIO_RETENTION_DAYS", "30")
    get_settings.cache_clear()

    created = (await _post_recording(client, user)).json()
    row = await db.scalar(select(VisitRecording).where(VisitRecording.id == UUID(created["id"])))
    # created_at は今日のまま (受領した瞬間)。recorded_at だけ大きく過去へ。
    row.recorded_at = datetime.now(UTC) - timedelta(days=800)
    await db.commit()
    stored = Path(row.audio_path)

    res = await client.post(
        "/api/v1/admin/visit-recordings/purge-audio", headers=_bearer(admin_user)
    )
    assert res.status_code == 200, res.text
    assert res.json()["purged"] == 0
    await db.refresh(row)
    assert row.audio_path is not None
    assert stored.exists()
    await db.rollback()


@pytest.mark.asyncio
async def test_purge_audio_requires_admin(client, db, audio_dir, staff_user) -> None:
    _staff, user = staff_user
    res = await client.post("/api/v1/admin/visit-recordings/purge-audio", headers=_bearer(user))
    assert res.status_code == 403, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_reap_stale_jobs_fails_only_expired(db, audio_dir, staff_user, monkeypatch) -> None:
    """transcribing のまま VOICE_JOB_STALE_MINUTES を超えた行だけ failed になる."""
    staff, _user = staff_user
    monkeypatch.setenv("VOICE_JOB_STALE_MINUTES", "15")
    get_settings.cache_clear()

    now = datetime.now(UTC)
    stale = VisitRecording(
        staff_id=staff.id,
        recorded_at=now - timedelta(hours=2),
        duration_sec=60,
        status="transcribing",
        consent_confirmed=True,
        updated_at=now - timedelta(hours=1),
    )
    running = VisitRecording(
        staff_id=staff.id,
        recorded_at=now,
        duration_sec=60,
        status="transcribing",
        consent_confirmed=True,
        updated_at=now,
    )
    db.add_all([stale, running])
    await db.commit()

    reaped = await reap_stale_jobs(db, now=now)
    assert reaped == 1
    await db.refresh(stale)
    await db.refresh(running)
    assert stale.status == "failed"
    assert stale.error_message == STALE_ERROR_MESSAGE
    assert running.status == "transcribing"
    await db.rollback()


@pytest.mark.asyncio
async def test_upload_sets_office_from_patient(client, db, audio_dir, staff_user) -> None:
    """office_id は患者の主担当拠点から解決する (表示スコープ)."""
    staff, user = staff_user
    office = Office(name="都賀A")
    db.add(office)
    await db.commit()
    await db.refresh(office)
    patient = await _make_patient(db, "VR-OFFICE", primary_office_id=office.id)
    visit = await _make_visit(db, patient.id, staff.id)

    res = await _post_recording(client, user, visit_id=str(visit.id))
    assert res.status_code == 202, res.text
    assert res.json()["office_id"] == str(office.id)
    await db.rollback()


# ---------------------------------------------------------------------------
# 受領の入口ガード (Content-Length / recorded_at / duration_sec / 冪等キー)


@pytest.mark.asyncio
async def test_upload_rejects_by_content_length_before_reading(
    client, db, audio_dir, staff_user, monkeypatch
) -> None:
    """申告 (Content-Length) が上限 + 余白を超えていたら 1 バイトも読まずに 413.

    「読まなかった」証拠は **保存先ディレクトリが作られていないこと**:
    ストリーミング経路は最初に ``{yyyy}/{mm}`` を mkdir してから読み始める。
    """
    _staff, user = staff_user
    monkeypatch.setenv("VISIT_AUDIO_MAX_BYTES", "1024")
    get_settings.cache_clear()

    # 1024 + 64 KiB の余白を確実に超える申告。
    res = await _post_recording(client, user, files=_upload_files(b"x" * 100 * 1024))
    assert res.status_code == 413, res.text
    assert "32kbps" in res.json()["detail"]
    assert not audio_dir.exists()
    assert (await db.scalar(select(VisitRecording))) is None
    await db.rollback()


@pytest.mark.asyncio
async def test_upload_rejects_recorded_at_out_of_range(client, db, audio_dir, staff_user) -> None:
    """端末時計が大きく狂った録音は 422 (保存先とパージ基準を巻き込むため)."""
    _staff, user = staff_user
    too_old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    res = await _post_recording(client, user, recorded_at=too_old)
    assert res.status_code == 422, res.text

    too_new = (datetime.now(UTC) + timedelta(hours=3)).isoformat()
    res = await _post_recording(client, user, recorded_at=too_new)
    assert res.status_code == 422, res.text
    assert (await db.scalar(select(VisitRecording))) is None
    await db.rollback()


@pytest.mark.asyncio
async def test_upload_rejects_absurd_duration(client, db, audio_dir, staff_user) -> None:
    """ms を秒として送る類の事故 (6 時間超) は 422."""
    _staff, user = staff_user
    res = await _post_recording(client, user, duration_sec="120000")
    assert res.status_code == 422, res.text
    res = await _post_recording(client, user, duration_sec="-1")
    assert res.status_code == 422, res.text
    assert (await db.scalar(select(VisitRecording))) is None
    await db.rollback()


@pytest.mark.asyncio
async def test_upload_with_same_client_id_returns_existing_row(
    client, db, audio_dir, staff_user
) -> None:
    """同じ client_id の 2 回目は 200 + 同一行 (再送で二重課金しない)."""
    _staff, user = staff_user
    token = str(uuid4())

    first = await _post_recording(client, user, client_id=token)
    assert first.status_code == 202, first.text

    second = await _post_recording(client, user, client_id=token)
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]

    rows = (await db.scalars(select(VisitRecording))).all()
    assert len(rows) == 1
    # 2 回目の音声ファイルは残さない (行に紐付かないゴミを作らない)。
    assert len([p for p in audio_dir.rglob("*") if p.is_file()]) == 1
    await db.rollback()


@pytest.mark.asyncio
async def test_upload_same_client_id_hits_integrity_error_path(
    client, db, audio_dir, staff_user, monkeypatch
) -> None:
    """事前チェックをすり抜けた並走も partial unique が受け止めて 200 + 同一行.

    事前チェックの 1 回目だけを「見つからなかった」ことにして
    ``except IntegrityError`` を直接叩く。unique は ``sqlite_where`` 付きの
    partial なので SQLite でも本番と同じ形で衝突する。
    """
    import app.api.v1.visit_recordings as mod

    _staff, user = staff_user
    token = str(uuid4())
    first = await _post_recording(client, user, client_id=token)
    assert first.status_code == 202, first.text

    original = mod._find_by_client_id
    calls = {"n": 0}

    async def _blind_first(*args, **kwargs):
        calls["n"] += 1
        # 1 回目 (事前チェック) は空振り → INSERT まで進んで unique に当たる。
        # 2 回目 (IntegrityError からの復帰) は本物を返す。
        if calls["n"] == 1:
            return None
        return await original(*args, **kwargs)

    monkeypatch.setattr(mod, "_find_by_client_id", _blind_first)
    second = await _post_recording(client, user, client_id=token)
    monkeypatch.undo()

    assert calls["n"] == 2
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    rows = (await db.scalars(select(VisitRecording))).all()
    assert len(rows) == 1
    # IntegrityError 経路でも書きかけの音声は捨てる。
    assert len([p for p in audio_dir.rglob("*") if p.is_file()]) == 1
    await db.rollback()


@pytest.mark.asyncio
async def test_upload_after_delete_with_same_client_id_creates_new_row(
    client, db, audio_dir, staff_user, admin_user
) -> None:
    """削除済み録音と同じ client_id の再送は **新規作成** (墓石で詰まらない).

    unique が生きている行だけを見る (``deleted_at IS NULL``) ので、admin が
    消した録音と同じ端末キーで送り直しても 500 にならず素直に受け取れる。
    """
    _staff, user = staff_user
    token = str(uuid4())
    first = (await _post_recording(client, user, client_id=token)).json()

    res = await client.delete(
        f"/api/v1/visit-recordings/{first['id']}", headers=_bearer(admin_user)
    )
    assert res.status_code == 204, res.text

    again = await _post_recording(client, user, client_id=token)
    assert again.status_code == 202, again.text
    assert again.json()["id"] != first["id"]

    live = (
        await db.scalars(select(VisitRecording).where(VisitRecording.deleted_at.is_(None)))
    ).all()
    assert [str(r.id) for r in live] == [again.json()["id"]]
    await db.rollback()


# ---------------------------------------------------------------------------
# 監査ミドルウェアの body バイパス規則 (単体)


def test_skips_body_buffering_rules() -> None:
    """multipart のパス規則・大きい Content-Length・通常の 3 通り."""
    # 1. 既知のストリーミング経路 + multipart -> _multipart マーカー。
    assert _skips_body_buffering(
        "POST", "/api/v1/visit-recordings", "multipart/form-data; boundary=x", "512"
    ) == {"_multipart": True}
    # 2. content-type に関係なく、1 MiB 超の申告はバイパス。
    assert _skips_body_buffering(
        "PUT", "/api/v1/patients/123", "application/json", str(2 * 1024 * 1024)
    ) == {"_large_body": True}
    # 3. 通常の JSON 更新はこれまでどおりバッファする。
    assert _skips_body_buffering("PATCH", "/api/v1/visits/123", "application/json", "512") is None
    # 同じ経路でも JSON かつ小さければバッファする (multipart 規則は効かない)。
    assert (
        _skips_body_buffering("PATCH", "/api/v1/visit-recordings/1", "application/json", "20")
        is None
    )
    # 壊れた Content-Length は無視して通常どおり (読んでから判断する)。
    assert (
        _skips_body_buffering("POST", "/api/v1/visits", "application/json", "not-a-number") is None
    )


# ---------------------------------------------------------------------------
# 紐付け: 既存訪問の再利用 / 付け替え / 権限


@pytest.mark.asyncio
async def test_patch_patient_reuses_existing_visit(client, db, audio_dir, staff_user) -> None:
    """その日その患者に自分の訪問が既にあれば **再利用** する (受け皿を作らない)."""
    staff, user = staff_user
    patient = await _make_patient(db, "VR-REUSE")
    # 録音開始時刻 (= now) に近い方が選ばれる。
    far = await _make_visit(db, patient.id, staff.id, start_time=time(0, 1), end_time=time(0, 35))
    near_start = datetime.now(UTC).astimezone(JST).time().replace(second=0, microsecond=0)
    near = await _make_visit(db, patient.id, staff.id, start_time=near_start, end_time=time(23, 59))
    created = (await _post_recording(client, user)).json()

    res = await client.patch(
        f"/api/v1/visit-recordings/{created['id']}",
        headers=_bearer(user),
        json={"patient_id": str(patient.id)},
    )
    assert res.status_code == 200, res.text
    assert res.json()["visit_id"] == str(near.id)
    assert res.json()["visit_id"] != str(far.id)

    # 新しい訪問は 1 件も生えていない。
    visits = (await db.scalars(select(Visit).where(Visit.patient_id == patient.id))).all()
    assert len(visits) == 2
    await db.rollback()


@pytest.mark.asyncio
async def test_patch_patient_reuses_visit_via_course_assignment(
    client, db, audio_dir, staff_user
) -> None:
    """主担当 NULL + コース担当が自分の訪問も再利用する (帰属は一覧と同じ規則).

    主担当だけで探すと、コース担当フォールバックで回っている訪問 (=
    ``primary_staff_id`` が NULL のまま作られた訪問) を取り逃がして
    「自分の訪問なのに紐付かない」が起きる。
    """
    from app.models import Course

    staff, user = staff_user
    office = Office(name="コース拠点")
    db.add(office)
    await db.commit()
    await db.refresh(office)
    course = Course(
        iso_year=2026,
        iso_week=27,
        weekday=1,
        code="A",
        course_status="course_fixed",
        office_id=office.id,
        assigned_staff_id=staff.id,
    )
    db.add(course)
    await db.commit()
    await db.refresh(course)

    patient = await _make_patient(db, "VR-COURSE")
    visit = await _make_visit(db, patient.id, None, course_id=course.id)
    created = (await _post_recording(client, user)).json()

    res = await client.patch(
        f"/api/v1/visit-recordings/{created['id']}",
        headers=_bearer(user),
        json={"patient_id": str(patient.id)},
    )
    assert res.status_code == 200, res.text
    assert res.json()["visit_id"] == str(visit.id)
    await db.rollback()


@pytest.mark.asyncio
async def test_patch_patient_reuses_visit_via_assignment_row(
    client, db, audio_dir, staff_user
) -> None:
    """visit_staff_assignments 経由 (2 名体制の 2 人目) の訪問も再利用する."""
    from app.models.visit_staff_assignment import VisitStaffAssignment

    staff, user = staff_user
    other = Staff(name="主担当 太郎")
    db.add(other)
    await db.commit()
    await db.refresh(other)

    patient = await _make_patient(db, "VR-VSA")
    visit = await _make_visit(db, patient.id, other.id)
    db.add(VisitStaffAssignment(visit_id=visit.id, staff_id=staff.id))
    await db.commit()
    created = (await _post_recording(client, user)).json()

    res = await client.patch(
        f"/api/v1/visit-recordings/{created['id']}",
        headers=_bearer(user),
        json={"patient_id": str(patient.id)},
    )
    assert res.status_code == 200, res.text
    assert res.json()["visit_id"] == str(visit.id)
    await db.rollback()


@pytest.mark.asyncio
async def test_patch_patient_reuses_visit_as_mentor(client, db, audio_dir, staff_user) -> None:
    """新人同行の指導者 (mentor_staff_id) として入っている訪問も再利用する."""
    staff, user = staff_user
    trainee = Staff(name="新人 花子")
    db.add(trainee)
    await db.commit()
    await db.refresh(trainee)

    patient = await _make_patient(db, "VR-MENTOR")
    visit = await _make_visit(db, patient.id, trainee.id, mentor_staff_id=staff.id)
    created = (await _post_recording(client, user)).json()

    res = await client.patch(
        f"/api/v1/visit-recordings/{created['id']}",
        headers=_bearer(user),
        json={"patient_id": str(patient.id)},
    )
    assert res.status_code == 200, res.text
    assert res.json()["visit_id"] == str(visit.id)
    await db.rollback()


@pytest.mark.asyncio
async def test_patch_visit_id_null_unlinks_without_touching_visit(
    client, db, audio_dir, staff_user
) -> None:
    """``visit_id: null`` は常に許す紐付け解除 — 訪問には一切触らない."""
    staff, user = staff_user
    patient = await _make_patient(db, "VR-UNLINK")
    visit = await _make_visit(db, patient.id, staff.id)
    created = (await _post_recording(client, user, visit_id=str(visit.id))).json()
    assert created["visit_id"] == str(visit.id)

    res = await client.patch(
        f"/api/v1/visit-recordings/{created['id']}",
        headers=_bearer(user),
        json={"visit_id": None},
    )
    assert res.status_code == 200, res.text
    assert res.json()["visit_id"] is None
    # 患者の紐付けは残る (訪問だけ外した)。
    assert res.json()["patient_id"] == str(patient.id)

    await db.refresh(visit)
    assert visit.deleted_at is None
    assert visit.status == "planned"
    await db.rollback()


@pytest.mark.asyncio
async def test_patch_relink_leaves_old_visit_untouched(client, db, audio_dir, staff_user) -> None:
    """患者を選び直しても旧 ``visit_id`` は **外すだけ** (訪問は soft-delete しない)."""
    staff, user = staff_user
    patient = await _make_patient(db, "VR-HUMAN")
    other = await _make_patient(db, "VR-HUMAN2")
    visit = await _make_visit(db, patient.id, staff.id)
    created = (await _post_recording(client, user, visit_id=str(visit.id))).json()

    res = await client.patch(
        f"/api/v1/visit-recordings/{created['id']}",
        headers=_bearer(user),
        json={"patient_id": str(other.id)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["patient_id"] == str(other.id)
    # 付け替え先の患者にはその日の訪問が無いので NULL。
    assert body["visit_id"] is None

    # 旧訪問は無傷 (録音は訪問に貼った付箋であって、剥がしても訪問は残る)。
    await db.refresh(visit)
    assert visit.deleted_at is None
    assert visit.patient_id == patient.id
    assert visit.status == "planned"
    await db.rollback()


@pytest.mark.asyncio
async def test_patch_visit_and_patient_mismatch_is_422(client, db, audio_dir, staff_user) -> None:
    staff, user = staff_user
    patient = await _make_patient(db, "VR-MIX1")
    other = await _make_patient(db, "VR-MIX2")
    visit = await _make_visit(db, patient.id, staff.id)
    created = (await _post_recording(client, user)).json()

    res = await client.patch(
        f"/api/v1/visit-recordings/{created['id']}",
        headers=_bearer(user),
        json={"visit_id": str(visit.id), "patient_id": str(other.id)},
    )
    assert res.status_code == 422, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_patch_link_after_grace_is_403_for_staff(client, db, audio_dir, staff_user) -> None:
    """24 時間を過ぎた紐付けの変更は本人でも 403 (以降は admin だけ)."""
    _staff, user = staff_user
    patient = await _make_patient(db, "VR-GRACE")
    created = (await _post_recording(client, user)).json()
    row = await db.scalar(select(VisitRecording).where(VisitRecording.id == UUID(created["id"])))
    row.created_at = datetime.now(UTC) - timedelta(hours=25)
    await db.commit()

    res = await client.patch(
        f"/api/v1/visit-recordings/{created['id']}",
        headers=_bearer(user),
        json={"patient_id": str(patient.id)},
    )
    assert res.status_code == 403, res.text

    # 確認済みフラグ (紐付けではない) は猶予を過ぎても本人が触れる。
    res = await client.patch(
        f"/api/v1/visit-recordings/{created['id']}",
        headers=_bearer(user),
        json={"reviewed": True},
    )
    assert res.status_code == 200, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_patch_and_audio_of_other_staff_recording_are_404(
    client, db, audio_dir, staff_user
) -> None:
    """他人の録音は PATCH も音声も 404 (存在ごと秘匿)."""
    _staff, user = staff_user
    other_staff = Staff(name="他人 太郎")
    db.add(other_staff)
    await db.commit()
    await db.refresh(other_staff)
    patient = await _make_patient(db, "VR-404")
    row = VisitRecording(
        staff_id=other_staff.id,
        recorded_at=datetime.now(UTC),
        duration_sec=30,
        status="unlinked",
        consent_confirmed=True,
        audio_path=str(audio_dir / "other.webm"),
        audio_mime="audio/webm",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    res = await client.patch(
        f"/api/v1/visit-recordings/{row.id}",
        headers=_bearer(user),
        json={"patient_id": str(patient.id)},
    )
    assert res.status_code == 404, res.text

    res = await client.get(f"/api/v1/visit-recordings/{row.id}/audio", headers=_bearer(user))
    assert res.status_code == 404, res.text
    await db.rollback()


# ---------------------------------------------------------------------------
# Phase 2-B (PC /records): 一覧の絞り込み追加 (office_id / q / order / reviewed) と
# 要約の人手修正 (summary_text + summary_edited_by/at・migration 0087)。
# 正典: docs/plans/visit-voice-record-design-2026-09-17.md §11-2。


async def _seed_recording(db, staff_id, **overrides) -> VisitRecording:
    """API を通さずに 1 行置く (他人の録音・要約付きの行を作るため)。"""
    fields = {
        "staff_id": staff_id,
        "recorded_at": datetime.now(UTC),
        "duration_sec": 30,
        "status": "unlinked",
        "consent_confirmed": True,
    }
    fields.update(overrides)
    row = VisitRecording(**fields)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@pytest.mark.asyncio
async def test_list_filters_by_office_id(client, db, audio_dir, staff_user, admin_user) -> None:
    """office_id は「行の拠点」と「録音者の主担当拠点」の **どちらか** で当たる."""
    staff, user = staff_user
    office_a = Office(name="稲毛")
    office_b = Office(name="都賀")
    db.add_all([office_a, office_b])
    await db.commit()
    await db.refresh(office_a)
    await db.refresh(office_b)

    # 録音者は稲毛所属・患者は都賀 → 行の office_id は患者側 (都賀) が勝つ。
    staff.primary_office_id = office_a.id
    patient = await _make_patient(db, "VR-OF", primary_office_id=office_b.id)
    visit = await _make_visit(db, patient.id, staff.id)
    created = (await _post_recording(client, user, visit_id=str(visit.id))).json()
    assert created["office_id"] == str(office_b.id)
    assert created["office_name"] == "都賀"

    # どちらの拠点にも属さない録音 (絞り込みから落ちること)。
    far_staff = Staff(name="遠隔 三郎")
    db.add(far_staff)
    await db.commit()
    await db.refresh(far_staff)
    await _seed_recording(db, far_staff.id)

    async def _ids(office_id) -> list[str]:
        res = await client.get(
            "/api/v1/visit-recordings",
            headers=_bearer(admin_user),
            params={"office_id": str(office_id)},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        # total は絞り込み後。
        assert body["total"] == len(body["items"])
        return [i["id"] for i in body["items"]]

    # 行の office_id で当たる。
    assert await _ids(office_b.id) == [created["id"]]
    # 録音者の主担当拠点でも当たる (行の office_id は都賀のまま)。
    assert await _ids(office_a.id) == [created["id"]]
    # 無関係な拠点では 0 件 (遠隔 三郎の行は拠点が無い)。
    other = Office(name="幕張")
    db.add(other)
    await db.commit()
    await db.refresh(other)
    assert await _ids(other.id) == []
    await db.rollback()


@pytest.mark.asyncio
async def test_list_q_matches_patient_staff_summary_and_transcript(
    client, db, audio_dir, staff_user, admin_user
) -> None:
    """q は 患者名 / スタッフ名 / summary_text / transcript の部分一致 (ilike)."""
    staff, user = staff_user  # name = "録音 花子"
    patient = await _make_patient(db, "VR-Q")  # name = "利用者VR-Q"
    visit = await _make_visit(db, patient.id, staff.id)
    linked = (await _post_recording(client, user, visit_id=str(visit.id))).json()
    unlinked = (await _post_recording(client, user)).json()

    row = await db.scalar(select(VisitRecording).where(VisitRecording.id == UUID(unlinked["id"])))
    row.summary_text = "褥瘡の処置を実施"
    row.transcript = "看護師: 包交しました"
    await db.commit()

    async def _ids(actor, q) -> list[str]:
        res = await client.get("/api/v1/visit-recordings", headers=_bearer(actor), params={"q": q})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["total"] == len(body["items"])
        return sorted(i["id"] for i in body["items"])

    # 患者名 → 紐付いた 1 件だけ。
    assert await _ids(admin_user, "利用者VR-Q") == [linked["id"]]
    # スタッフ名 → 録音者が同じなので 2 件とも。
    assert await _ids(admin_user, "花子") == sorted([linked["id"], unlinked["id"]])
    # 要約 / 文字起こし。
    assert await _ids(admin_user, "褥瘡") == [unlinked["id"]]
    assert await _ids(admin_user, "包交") == [unlinked["id"]]

    # staff は q を使っても自分の分だけ (他人の一致行は出ない)。
    other_staff = Staff(name="他人 太郎")
    db.add(other_staff)
    await db.commit()
    await db.refresh(other_staff)
    await _seed_recording(db, other_staff.id, summary_text="褥瘡の処置を実施")
    assert await _ids(user, "褥瘡") == [unlinked["id"]]
    assert len(await _ids(admin_user, "褥瘡")) == 2

    # staff が他人の名前で引いても 0 件 (q はスタッフ名にも当たるが強制絞り込みが先)。
    assert await _ids(user, "他人 太郎") == []
    assert await _ids(user, "太郎") == []
    assert len(await _ids(admin_user, "太郎")) == 1
    await db.rollback()


@pytest.mark.asyncio
async def test_list_q_requires_two_characters(client, db, audio_dir, staff_user) -> None:
    """1 文字の q は 422 (全文にほぼ必ず当たり、絞り込みにならない)."""
    _staff, user = staff_user
    await _post_recording(client, user)

    res = await client.get("/api/v1/visit-recordings", headers=_bearer(user), params={"q": "花"})
    assert res.status_code == 422, res.text
    res = await client.get("/api/v1/visit-recordings", headers=_bearer(user), params={"q": "花子"})
    assert res.status_code == 200, res.text
    assert res.json()["total"] == 1
    await db.rollback()


@pytest.mark.asyncio
async def test_list_office_id_still_scoped_to_own_recordings_for_staff(
    client, db, audio_dir, staff_user
) -> None:
    """staff は office_id を指定しても自分の録音だけ (拠点の同僚の分は出ない)."""
    staff, user = staff_user
    office = Office(name="稲毛")
    db.add(office)
    await db.commit()
    await db.refresh(office)

    staff.primary_office_id = office.id
    colleague = Staff(name="同僚 次郎", primary_office_id=office.id)
    db.add(colleague)
    await db.commit()
    await db.refresh(colleague)
    # 同じ拠点の同僚の録音 (office_id でも当たる行)。
    await _seed_recording(db, colleague.id, office_id=office.id)
    mine = (await _post_recording(client, user)).json()

    res = await client.get(
        "/api/v1/visit-recordings",
        headers=_bearer(user),
        params={"office_id": str(office.id)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 1
    assert [i["id"] for i in body["items"]] == [mine["id"]]
    assert body["items"][0]["office_name"] == "稲毛"
    await db.rollback()


@pytest.mark.asyncio
async def test_list_order_recorded_at_asc(client, db, audio_dir, staff_user) -> None:
    """order=recorded_at_asc で古い順・既定 (desc) は新しい順."""
    _staff, user = staff_user
    now = datetime.now(UTC)
    older = (
        await _post_recording(client, user, recorded_at=(now - timedelta(hours=3)).isoformat())
    ).json()
    newer = (await _post_recording(client, user, recorded_at=now.isoformat())).json()

    async def _ids(params) -> list[str]:
        res = await client.get("/api/v1/visit-recordings", headers=_bearer(user), params=params)
        assert res.status_code == 200, res.text
        return [i["id"] for i in res.json()["items"]]

    assert await _ids({}) == [newer["id"], older["id"]]
    assert await _ids({"order": "recorded_at_desc"}) == [newer["id"], older["id"]]
    assert await _ids({"order": "recorded_at_asc"}) == [older["id"], newer["id"]]

    # 知らない並び順は 422 (静かに既定へ倒さない)。
    res = await client.get(
        "/api/v1/visit-recordings", headers=_bearer(user), params={"order": "cost_desc"}
    )
    assert res.status_code == 422, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_list_filters_by_reviewed(client, db, audio_dir, staff_user) -> None:
    """reviewed=true/false は reviewed_at の有無で切る."""
    _staff, user = staff_user
    done = (await _post_recording(client, user)).json()
    todo = (await _post_recording(client, user)).json()

    res = await client.patch(
        f"/api/v1/visit-recordings/{done['id']}", headers=_bearer(user), json={"reviewed": True}
    )
    assert res.status_code == 200, res.text

    async def _ids(reviewed) -> list[str]:
        res = await client.get(
            "/api/v1/visit-recordings", headers=_bearer(user), params={"reviewed": reviewed}
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["total"] == len(body["items"])
        return [i["id"] for i in body["items"]]

    assert await _ids("true") == [done["id"]]
    assert await _ids("false") == [todo["id"]]
    await db.rollback()


@pytest.mark.asyncio
async def test_patch_summary_text_records_editor(client, db, audio_dir, staff_user) -> None:
    """要約の人手修正は本人 OK・丸ごと差し替え・誰がいつ直したかを残す."""
    _staff, user = staff_user
    created = (await _post_recording(client, user)).json()
    assert created["summary_edited_by"] is None
    assert created["summary_edited_at"] is None

    res = await client.patch(
        f"/api/v1/visit-recordings/{created['id']}",
        headers=_bearer(user),
        json={"summary_text": "血圧 120/80。意識 清明。"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary_text"] == "血圧 120/80。意識 清明。"
    assert body["summary_edited_by"] == str(user.id)
    assert body["summary_edited_at"] is not None
    await db.rollback()


@pytest.mark.asyncio
async def test_patch_summary_text_of_other_staff_is_404(client, db, audio_dir, staff_user) -> None:
    """他人の録音の要約は直せない (存在ごと秘匿の 404)."""
    _staff, user = staff_user
    other_staff = Staff(name="他人 太郎")
    db.add(other_staff)
    await db.commit()
    await db.refresh(other_staff)
    row = await _seed_recording(db, other_staff.id, summary_text="AI の要約")

    res = await client.patch(
        f"/api/v1/visit-recordings/{row.id}",
        headers=_bearer(user),
        json={"summary_text": "書き換え"},
    )
    assert res.status_code == 404, res.text
    await db.refresh(row)
    assert row.summary_text == "AI の要約"
    assert row.summary_edited_by is None
    await db.rollback()


@pytest.mark.asyncio
async def test_patch_note_append_with_summary_text_is_422(
    client, db, audio_dir, staff_user
) -> None:
    """追記と人手修正は同じ列を奪い合うので同時指定は 422 (黙って片方を勝たせない)."""
    _staff, user = staff_user
    created = (await _post_recording(client, user)).json()

    res = await client.patch(
        f"/api/v1/visit-recordings/{created['id']}",
        headers=_bearer(user),
        json={"note_append": "追記", "summary_text": "差し替え"},
    )
    assert res.status_code == 422, res.text
    assert res.json()["detail"] == "note_append と summary_text は同時に指定できません"

    row = await db.scalar(select(VisitRecording).where(VisitRecording.id == UUID(created["id"])))
    await db.refresh(row)
    assert row.summary_text is None
    assert row.summary_edited_at is None
    await db.rollback()


@pytest.mark.asyncio
async def test_patch_summary_text_clears_reviewed(client, db, audio_dir, staff_user) -> None:
    """「確認済み」= 内容の承認。本文を直したら承認は失効する (同時 reviewed で立て直せる)."""
    _staff, user = staff_user
    created = (await _post_recording(client, user)).json()

    res = await client.patch(
        f"/api/v1/visit-recordings/{created['id']}",
        headers=_bearer(user),
        json={"reviewed": True},
    )
    assert res.status_code == 200, res.text
    assert res.json()["reviewed_at"] is not None

    # 承認済みの文を書き換える → 承認は失効。
    res = await client.patch(
        f"/api/v1/visit-recordings/{created['id']}",
        headers=_bearer(user),
        json={"summary_text": "直した要約"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary_text"] == "直した要約"
    assert body["reviewed_by"] is None
    assert body["reviewed_at"] is None

    # 直しながらその場で承認する (同じ PATCH の reviewed:true が後に効く)。
    res = await client.patch(
        f"/api/v1/visit-recordings/{created['id']}",
        headers=_bearer(user),
        json={"summary_text": "直して承認", "reviewed": True},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary_text"] == "直して承認"
    assert body["reviewed_by"] == str(user.id)
    assert body["reviewed_at"] is not None
    await db.rollback()


@pytest.mark.asyncio
async def test_retry_does_not_touch_manual_summary(
    client, db, audio_dir, staff_user, admin_user
) -> None:
    """再処理を頼んだだけでは人手修正に触らない (ジョブが失敗しても出所が壊れない)."""
    _staff, user = staff_user
    created = (await _post_recording(client, user)).json()

    res = await client.patch(
        f"/api/v1/visit-recordings/{created['id']}",
        headers=_bearer(user),
        json={"summary_text": "人が直した要約"},
    )
    assert res.status_code == 200, res.text

    # VOICE_AI_PROVIDER='none' なのでジョブは何もしない (= 失敗して終わったのと同じ)。
    res = await client.post(
        f"/api/v1/visit-recordings/{created['id']}/retry", headers=_bearer(admin_user)
    )
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["status"] == "uploaded"
    assert body["error_message"] is None
    # 人手修正も痕跡もそのまま (ここでクリアすると「人が直した文なのに AI 由来」に見える)。
    assert body["summary_text"] == "人が直した要約"
    assert body["summary_edited_by"] == str(user.id)
    assert body["summary_edited_at"] is not None
    assert (body["summary"] or {}).get("previous_manual") is None
    await db.rollback()


@pytest.mark.asyncio
async def test_save_result_stashes_manual_summary(db, audio_dir, staff_user) -> None:
    """AI が書き直す瞬間 (``_save_result``) に人手修正を previous_manual へ退避する.

    「文字起こし中に PATCH された」行 = ``summary_edited_at`` 付きで
    ``_save_result`` に入ってくる行なので、その形を直接作って当てる。
    """
    staff, user = staff_user
    edited_at = datetime.now(UTC) - timedelta(minutes=3)
    row = await _seed_recording(
        db,
        staff.id,
        status="transcribing",
        summary={"free": "AI の初回要約"},
        summary_text="人が直した要約",
        summary_edited_by=user.id,
        summary_edited_at=edited_at,
    )

    result = VoiceAiResult(
        transcript="看護師: 体調はいかがですか。",
        transcript_segments=[{"speaker": "看護師", "start_sec": 0, "text": "体調は?"}],
        # AI が previous_manual というキーを返してきても退避が勝つこと。
        summary={"free": "AI の書き直し", "previous_manual": "AI の嘘"},
        summary_text="【主訴・様子】\n・変化なし",
        tokens_in=100,
        tokens_out=20,
        cost_usd=Decimal("0.0001"),
        model="gemini-2.5-flash",
        prompt_version="v1",
    )
    row.reviewed_by = user.id
    row.reviewed_at = datetime.now(UTC)
    await db.commit()
    await _save_result(db, row, result)
    await db.refresh(row)

    # AI の結果で上書きされる。
    assert row.summary_text == "【主訴・様子】\n・変化なし"
    assert row.summary["free"] == "AI の書き直し"
    # 痕跡はここで初めて消える。
    assert row.summary_edited_by is None
    assert row.summary_edited_at is None
    # 消えた人手修正は残る (AI の同名キーより退避が勝つ)。
    previous = row.summary["previous_manual"]
    assert previous["summary_text"] == "人が直した要約"
    assert previous["edited_by"] == str(user.id)
    assert previous["edited_at"].startswith(edited_at.strftime("%Y-%m-%dT%H:%M"))
    assert previous["replaced_at"] is not None

    # 2 度目の書き直しは 1 度目の退避を「その時点の summary_text」で置き換える
    # (溜め込まない = 直近 1 件のみ)。
    row.summary_text = "2 回目の人手修正"
    row.summary_edited_by = user.id
    row.summary_edited_at = datetime.now(UTC)
    await db.commit()
    await _save_result(db, row, result)
    await db.refresh(row)
    assert row.summary["previous_manual"]["summary_text"] == "2 回目の人手修正"
    await db.rollback()
    # AI が書き直したので「確認済み」は失効する (N-2)。
    await db.refresh(row)
    assert row.reviewed_by is None and row.reviewed_at is None


@pytest.mark.asyncio
async def test_save_result_without_manual_edit_has_no_previous(db, audio_dir, staff_user) -> None:
    """人手修正が無い行では previous_manual を作らない."""
    staff, _user = staff_user
    row = await _seed_recording(db, staff.id, status="transcribing")
    await _save_result(
        db,
        row,
        VoiceAiResult(
            transcript="a",
            transcript_segments=[],
            summary={"free": "AI"},
            summary_text="AI",
            tokens_in=1,
            tokens_out=1,
            cost_usd=Decimal("0.0001"),
            model="m",
            prompt_version="v1",
        ),
    )
    await db.refresh(row)
    assert "previous_manual" not in (row.summary or {})
    await db.rollback()


# ---------------------------------------------------------------------------
# migration 0087 (summary_edited_by / summary_edited_at)


def test_migration_0087_chain_and_columns() -> None:
    """0087 が 0086 から派生し、単一 head で、2 列を add/drop する."""
    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    script = ScriptDirectory.from_config(cfg)

    rev = script.get_revision("0087_visit_recordings_summary_edit")
    assert rev is not None
    assert rev.down_revision == "0086_visit_recordings"
    heads = list(script.get_heads())
    assert heads == ["0087_visit_recordings_summary_edit"], f"heads は単一のはず: {heads}"

    src = (
        backend_root / "alembic" / "versions" / "0087_visit_recordings_summary_edit.py"
    ).read_text(encoding="utf-8")
    upgrade_src = src[src.find("def upgrade()") : src.find("def downgrade()")]
    downgrade_src = src[src.find("def downgrade()") :]
    for column in ("summary_edited_by", "summary_edited_at"):
        assert "add_column" in upgrade_src and f'"{column}"' in upgrade_src
        assert f'drop_column(_TABLE, "{column}")' in downgrade_src

    # モデル側にも生えていること (テストの DB は create_all で作られるため)。
    assert "summary_edited_by" in VisitRecording.__table__.columns
    assert "summary_edited_at" in VisitRecording.__table__.columns


# ---------------------------------------------------------------------------
# 印刷レポート (GET /{id}/report) — 設計 §11-3


@pytest.mark.asyncio
async def test_report_html_and_json(client, db, audio_dir, staff_user) -> None:
    """format=html は text/html、既定の json は記録 + html を同梱して返す."""
    staff, user = staff_user
    row = await _seed_recording(
        db,
        staff.id,
        status="summarized",
        summary={"主訴・様子": ["膝の痛み"], "バイタル": {"血圧": "128/76"}},
        summary_text="【主訴・様子】\n・膝の痛み",
        transcript="看護師: こんにちは。",
    )

    res = await client.get(
        f"/api/v1/visit-recordings/{row.id}/report",
        headers=_bearer(user),
        params={"format": "html"},
    )
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/html")
    assert res.text.startswith("<!doctype html>")
    assert "膝の痛み" in res.text
    assert "看護師: こんにちは。" in res.text

    res = await client.get(f"/api/v1/visit-recordings/{row.id}/report", headers=_bearer(user))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["recording"]["id"] == str(row.id)
    # 全文は html に入っているので JSON 側では省く (1 応答に会話を 2 回載せない)。
    assert body["recording"]["transcript"] is None
    assert body["recording"]["transcript_json"] is None
    assert body["html"].startswith("<!doctype html>")
    assert "看護師: こんにちは。" in body["html"]
    assert body["generated_at"]
    await db.rollback()


@pytest.mark.asyncio
async def test_report_is_never_cached(client, db, audio_dir, staff_user) -> None:
    """レポートも音声も個人情報そのもの → Cache-Control: no-store."""
    staff, user = staff_user
    row = await _seed_recording(db, staff.id, status="summarized", transcript="全文")

    for params in ({"format": "html"}, {}):
        res = await client.get(
            f"/api/v1/visit-recordings/{row.id}/report", headers=_bearer(user), params=params
        )
        assert res.status_code == 200, res.text
        assert res.headers["cache-control"] == "no-store"

    created = (await _post_recording(client, user)).json()
    res = await client.get(f"/api/v1/visit-recordings/{created['id']}/audio", headers=_bearer(user))
    assert res.status_code == 200, res.text
    assert res.headers["cache-control"] == "no-store"
    await db.rollback()


@pytest.mark.asyncio
async def test_report_rejects_unknown_format(client, db, audio_dir, staff_user) -> None:
    """format は html / json だけ (pdf は 422 = 黙って json を返さない)."""
    staff, user = staff_user
    row = await _seed_recording(db, staff.id, status="summarized")

    res = await client.get(
        f"/api/v1/visit-recordings/{row.id}/report",
        headers=_bearer(user),
        params={"format": "pdf"},
    )
    assert res.status_code == 422, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_audio_survives_audit_commit_failure(
    client, db, audio_dir, staff_user, monkeypatch
) -> None:
    """監査の commit が落ちても音声は 200 で返る (rollback 後の属性に触らない).

    ``_audit_read`` は失敗時に rollback する。rollback すると ORM の属性は
    expire 済みになり、そこへ触ると遅延ロードが走って async では
    MissingGreenlet で 500 になる — 配信に要る値は監査より前に退避してある。
    """
    _staff, user = staff_user
    created = (await _post_recording(client, user)).json()

    from sqlalchemy.ext.asyncio import AsyncSession

    async def _boom(self, *args, **kwargs):
        raise RuntimeError("audit commit failed")

    monkeypatch.setattr(AsyncSession, "commit", _boom)

    res = await client.get(f"/api/v1/visit-recordings/{created['id']}/audio", headers=_bearer(user))
    assert res.status_code == 200, res.text
    assert res.content == b"fake-audio-bytes"
    assert res.headers["cache-control"] == "no-store"
    await db.rollback()


@pytest.mark.asyncio
async def test_report_writes_audit_row(client, db, audio_dir, staff_user) -> None:
    """レポートは会話の全文そのものなので audit_logs に report_read を残す."""
    staff, user = staff_user
    row = await _seed_recording(db, staff.id, status="summarized", transcript="全文")

    res = await client.get(
        f"/api/v1/visit-recordings/{row.id}/report",
        headers=_bearer(user),
        params={"format": "html"},
    )
    assert res.status_code == 200, res.text

    rows = (await db.scalars(select(AuditLog).where(AuditLog.action == "report_read"))).all()
    assert len(rows) == 1
    assert rows[0].target_table == "visit_recordings"
    assert rows[0].target_id == str(row.id)
    assert rows[0].method == "GET"
    assert rows[0].path.endswith(f"/visit-recordings/{row.id}/report")
    await db.rollback()


@pytest.mark.asyncio
async def test_report_of_foreign_recording_returns_404(client, db, audio_dir, staff_user) -> None:
    """他人の録音は詳細と同じく 404 (存在ごと秘匿)."""
    _staff, user = staff_user
    other = Staff(name="他人 太郎")
    db.add(other)
    await db.commit()
    await db.refresh(other)
    row = await _seed_recording(db, other.id, status="summarized", transcript="他人の会話")

    res = await client.get(f"/api/v1/visit-recordings/{row.id}/report", headers=_bearer(user))
    assert res.status_code == 404, res.text
    # 秘匿した以上、監査にも「読んだ」行は残らない。
    assert (await db.scalar(select(AuditLog).where(AuditLog.action == "report_read"))) is None
    await db.rollback()


@pytest.mark.asyncio
async def test_report_of_foreign_recording_allowed_for_admin(
    client, db, audio_dir, staff_user, admin_user
) -> None:
    staff, _user = staff_user
    row = await _seed_recording(db, staff.id, status="summarized", transcript="全文")

    res = await client.get(f"/api/v1/visit-recordings/{row.id}/report", headers=_bearer(admin_user))
    assert res.status_code == 200, res.text
    await db.rollback()


# ---------------------------------------------------------------------------
# 利用状況 (GET /admin/visit-recordings/usage) — 設計 §11-3


def _jst_month_utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """JST の壁掛け時計を UTC の aware datetime に直す (created_at を置くため)。"""
    return datetime(year, month, day, hour, minute, tzinfo=JST).astimezone(UTC)


@pytest.mark.asyncio
async def test_usage_aggregates_month_in_jst(client, db, audio_dir, staff_user, admin_user) -> None:
    """月の境界は JST。9/30 23:00 JST は 9 月、10/1 00:30 JST は 10 月."""
    staff, _user = staff_user

    inside_first = await _seed_recording(
        db, staff.id, status="summarized", duration_sec=600, tokens_in=100, tokens_out=20
    )
    inside_last = await _seed_recording(
        db, staff.id, status="failed", duration_sec=300, tokens_in=50, tokens_out=5
    )
    outside = await _seed_recording(db, staff.id, status="summarized", duration_sec=900)
    deleted = await _seed_recording(db, staff.id, status="summarized", duration_sec=1200)

    # created_at (サーバー受領時刻) を JST の境界ぎりぎりに置く。
    inside_first.created_at = _jst_month_utc(2026, 9, 1, 0, 30)
    inside_first.cost_usd = Decimal("0.0020")
    inside_last.created_at = _jst_month_utc(2026, 9, 30, 23, 0)
    inside_last.cost_usd = Decimal("0.0010")
    # UTC では 9 月 30 日のままだが JST では 10 月 1 日 = 対象外。
    outside.created_at = _jst_month_utc(2026, 10, 1, 0, 30)
    outside.cost_usd = Decimal("9.9999")
    # 消した録音は数えない。
    deleted.created_at = _jst_month_utc(2026, 9, 15, 12, 0)
    deleted.cost_usd = Decimal("5.0000")
    deleted.deleted_at = datetime.now(UTC)
    await db.commit()

    res = await client.get(
        "/api/v1/admin/visit-recordings/usage",
        headers=_bearer(admin_user),
        params={"month": "2026-09"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["month"] == "2026-09"
    assert body["recordings"] == 2
    assert body["minutes_total"] == 15.0
    assert body["tokens_in"] == 150
    assert body["tokens_out"] == 25
    assert body["cost_usd"] == pytest.approx(0.003)
    assert body["failed"] == 1
    assert body["by_status"]["summarized"] == 1
    assert body["by_status"]["failed"] == 1
    # 0 件の status も 0 で埋める (画面のバッジが月によって消えない)。
    assert body["by_status"]["unlinked"] == 0
    await db.rollback()


@pytest.mark.asyncio
async def test_usage_by_staff(client, db, audio_dir, staff_user, admin_user) -> None:
    staff, _user = staff_user
    other = Staff(name="他人 太郎")
    db.add(other)
    await db.commit()
    await db.refresh(other)

    mine_1 = await _seed_recording(db, staff.id, status="summarized", duration_sec=600)
    mine_2 = await _seed_recording(db, staff.id, status="summarized", duration_sec=300)
    theirs = await _seed_recording(db, other.id, status="summarized", duration_sec=120)
    for row, cost in ((mine_1, "0.0020"), (mine_2, "0.0010"), (theirs, "0.0005")):
        row.created_at = _jst_month_utc(2026, 9, 10, 12, 0)
        row.cost_usd = Decimal(cost)
    await db.commit()

    res = await client.get(
        "/api/v1/admin/visit-recordings/usage",
        headers=_bearer(admin_user),
        params={"month": "2026-09"},
    )
    assert res.status_code == 200, res.text
    by_staff = res.json()["by_staff"]
    assert len(by_staff) == 2
    # 件数の多い順。
    assert by_staff[0]["staff_id"] == str(staff.id)
    assert by_staff[0]["staff_name"] == "録音 花子"
    assert by_staff[0]["recordings"] == 2
    assert by_staff[0]["minutes"] == 15.0
    assert by_staff[0]["cost_usd"] == pytest.approx(0.003)
    assert by_staff[1]["staff_name"] == "他人 太郎"
    assert by_staff[1]["recordings"] == 1

    # 総計は内訳の合計。画面の行を足して総計に合わないことがあってはならない。
    body = res.json()
    assert body["minutes_total"] == round(sum(s["minutes"] for s in by_staff), 1)
    assert body["cost_usd"] == round(sum(s["cost_usd"] for s in by_staff), 6)
    assert body["recordings"] == sum(s["recordings"] for s in by_staff)
    await db.rollback()


@pytest.mark.asyncio
async def test_usage_empty_month_is_all_zero(client, db, audio_dir, admin_user) -> None:
    """1 件も無い月は総計 0 (by_staff が空でも落ちない)."""
    res = await client.get(
        "/api/v1/admin/visit-recordings/usage",
        headers=_bearer(admin_user),
        params={"month": "2020-01"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["recordings"] == 0
    assert body["minutes_total"] == 0
    assert body["cost_usd"] == 0
    assert body["by_staff"] == []
    assert body["failed"] == 0
    await db.rollback()


@pytest.mark.asyncio
async def test_usage_rejects_bad_month_and_staff(client, db, audio_dir, staff_user, admin_user):
    """形式違いの month は 422、staff ロールは 403 (admin 専用)."""
    _staff, user = staff_user

    res = await client.get(
        "/api/v1/admin/visit-recordings/usage",
        headers=_bearer(admin_user),
        params={"month": "2026-13"},
    )
    assert res.status_code == 422, res.text

    res = await client.get(
        "/api/v1/admin/visit-recordings/usage",
        headers=_bearer(user),
        params={"month": "2026-09"},
    )
    assert res.status_code == 403, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_usage_defaults_to_current_month(client, db, audio_dir, staff_user, admin_user):
    """month 省略は JST の今月 (受領したばかりの録音が 1 件数えられる)."""
    _staff, user = staff_user
    await _post_recording(client, user)

    res = await client.get("/api/v1/admin/visit-recordings/usage", headers=_bearer(admin_user))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["month"] == datetime.now(UTC).astimezone(JST).strftime("%Y-%m")
    assert body["recordings"] == 1
    assert body["cost_usd"] == 0.0
    await db.rollback()
