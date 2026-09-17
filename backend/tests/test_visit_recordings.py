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
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.middleware.audit import _skips_body_buffering
from app.models import AuditLog, Office, Patient, Staff, User, Visit
from app.models.visit_recording import VisitRecording
from app.services.checkin.judge import JST
from app.services.voice.jobs import STALE_ERROR_MESSAGE, reap_stale_jobs


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
