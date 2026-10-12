"""AI の席の割り当て（PO 2026-10-13）: だれがだれのサインインした席で相談するか。

- 振り分け: 割り当てあり → その席 / 割り当てなし → 既定の席（default_seat_owner_user_id → AGENT_LEGACY_OWNER_USER_ID）
  / どちらも無い → 自分の席。割り当て先が未サインインなら「使う AI の席がサインインしていません。管理者に連絡してください」
- 効くのはその後に始める相談から（始めたときの席を session_seat:<id> に残し、続きは同じ席）
- ⚙ の表: 行 = 管理者・列 = 席（未サインインは選べない）・1 人ずつ／全員まとめて・admin_events に記録
"""

from test_engines import H, H2, K1, K2, FakeRunner, _client, _rig

from relaykit import ADMIN, ADMIN2
from relay.service import SEAT_SIGNIN_JA

HS = {"Authorization": "Bearer tok-staff"}
ORIGIN = {"Origin": "https://carelink.kaipoke-api.net"}


class Sql:
    def __init__(self, fail_display_name=False):
        self.fail, self.queries = fail_display_name, []

    def run(self, sql, params=None):
        self.queries.append(sql)
        if self.fail and "display_name" in sql:
            raise RuntimeError("permission denied for column display_name")
        return [{"id": ADMIN.id, "name": "A004"}, {"id": ADMIN2.id, "name": "A002"}]


def _setup(tmp_path, auth=None, **cfg_kw):
    rig, fake = _rig(tmp_path, FakeRunner(auth if auth is not None else {(K1, "claude"): "ok", (K2, "claude"): "ok"}),
                     fixture="claude_ask_then_tool.jsonl", max_cli=2, **cfg_kw)
    rig.svc.sql = Sql()
    seats = []
    inner = rig.svc.runner_factory

    def factory(session, token, resume_id):
        seats.append((session["id"], session.get("seat_user_id")))
        return inner(session, token, resume_id)

    rig.svc.runner_factory = factory
    return rig, fake, seats


def _start(c, headers, provider="claude"):
    s = c.post("/agent-relay/sessions", json={"provider": provider}, headers=headers).json()
    return s, c.post(f"/agent-relay/sessions/{s['id']}/messages", json={"text": "こんにちは"}, headers=headers)


def test_no_assignment_and_no_default_uses_own_seat(tmp_path):
    rig, _, seats = _setup(tmp_path)
    with _client(rig) as c:
        s, r = _start(c, H2)
    assert r.status_code == 200, r.text
    assert seats == [(s["id"], ADMIN2.id)]
    assert rig.store.get_setting("session_seat:" + s["id"]) == ADMIN2.id


def test_default_seat_comes_from_legacy_owner_env(tmp_path):
    rig, _, seats = _setup(tmp_path, legacy_owner_user_id=ADMIN.id)
    with _client(rig) as c:
        s, r = _start(c, H2)
    assert r.status_code == 200 and seats[-1] == (s["id"], ADMIN.id)
    e = [x for x in rig.store.list_admin_events() if x["action"] == "session_seat"][0]
    assert e["user_id"] == ADMIN2.id and e["detail"] == f"session={s['id']} seat={ADMIN.id}"


def test_assignment_wins_over_default(tmp_path):
    rig, _, seats = _setup(tmp_path, legacy_owner_user_id=ADMIN.id)
    rig.store.set_setting("seat_assignment:" + ADMIN2.id, ADMIN2.id)
    with _client(rig) as c:
        s, r = _start(c, H2)
    assert r.status_code == 200 and seats[-1] == (s["id"], ADMIN2.id)


def test_assigned_seat_signed_out_tells_to_contact_admin(tmp_path):
    rig, _, seats = _setup(tmp_path, auth={(K1, "claude"): "missing", (K2, "claude"): "ok"})
    rig.store.set_setting("seat_assignment:" + ADMIN2.id, ADMIN.id)
    with _client(rig) as c:
        s, r = _start(c, H2)
    assert r.status_code == 409 and r.json()["detail"] == SEAT_SIGNIN_JA
    assert SEAT_SIGNIN_JA == "使う AI の席がサインインしていません。管理者に連絡してください"
    card = [e for e in rig.store.list_events(s["id"]) if e["type"] == "error"][-1]
    assert card["code"] == "seat_signin" and not card["retry"]
    assert seats == []  # CLI は起こしていない
    assert rig.store.get_setting("session_seat:" + s["id"]) is None  # 起こせなかった相談の席は決めない


def test_change_applies_only_to_new_conversations(tmp_path):
    rig, _, seats = _setup(tmp_path)
    rig.store.set_setting("seat_assignment:" + ADMIN2.id, ADMIN.id)
    with _client(rig) as c:
        s, r = _start(c, H2)
        assert r.status_code == 200 and seats[-1] == (s["id"], ADMIN.id)
        rig.store.set_setting("seat_assignment:" + ADMIN2.id, ADMIN2.id)
        # 続いている相談: CLI を閉じてから続きを送っても、始めたときの席のまま
        rig.svc.live.pop(s["id"], None)
        r2 = c.post(f"/agent-relay/sessions/{s['id']}/messages", json={"text": "続き"}, headers=H2)
        assert r2.status_code == 200 and seats[-1] == (s["id"], ADMIN.id)
        s2, r3 = _start(c, H2)
        assert r3.status_code == 200 and seats[-1] == (s2["id"], ADMIN2.id)


def test_conversation_started_before_assignment_keeps_owner_seat(tmp_path):
    rig, _, _ = _setup(tmp_path, legacy_owner_user_id=ADMIN.id)
    sess = rig.store.create_session(ADMIN2.id, provider="claude")
    rig.store.update_session(sess["id"], cli_session_id="0190aaaa-0000-7000-8000-000000000000")
    assert rig.svc.seat_for_session(rig.store.get_session(sess["id"])) == (ADMIN2.id, False)


# ---------------- ⚙ の表 ----------------
def test_assignments_view_rows_and_columns(tmp_path):
    rig, _, _ = _setup(tmp_path, auth={(K1, "codex"): "ok", (K2, "codex"): "missing", (K2, "claude"): "missing"},
                       legacy_owner_user_id=ADMIN2.id)
    rig.store.set_setting("seat_assignment:" + ADMIN.id, ADMIN.id)
    with _client(rig) as c:
        assert c.get("/agent-relay/seats/assignments", headers=HS).status_code == 403
        v = c.get("/agent-relay/seats/assignments", headers=H).json()
    assert [(s["owner_user_id"], s["name"], s["signed_in"]) for s in v["seats"]] == [
        (ADMIN.id, "A004", True), (ADMIN2.id, "A002", False)]
    rows = {r["user_id"]: r for r in v["rows"]}
    assert rows[ADMIN.id]["seat_owner_user_id"] == ADMIN.id and rows[ADMIN.id]["source"] == "assigned"
    # 割り当て先（既定の A002 の席）がサインインしていない → 行に出す
    assert rows[ADMIN2.id]["source"] == "default" and rows[ADMIN2.id]["seat_signed_in"] is False
    assert v["default_seat_owner_user_id"] == ADMIN2.id
    assert "display_name" in rig.svc.sql.queries[0]


def test_assign_one_validates_and_audits(tmp_path):
    rig, _, _ = _setup(tmp_path, auth={(K1, "claude"): "ok", (K2, "codex"): "missing", (K2, "claude"): "missing"})
    with _client(rig) as c:
        h = {**H, **ORIGIN}
        assert c.put("/agent-relay/seats/assignments", json={"user_id": ADMIN2.id, "seat_owner_user_id": ADMIN2.id},
                     headers=h).status_code == 409  # 未サインインの席は選べない
        assert c.put("/agent-relay/seats/assignments", json={"user_id": "u-x", "seat_owner_user_id": ADMIN.id},
                     headers=h).status_code == 400
        assert c.put("/agent-relay/seats/assignments", json={"user_id": ADMIN2.id, "seat_owner_user_id": "u-x"},
                     headers=h).status_code == 400
        v = c.put("/agent-relay/seats/assignments", json={"user_id": ADMIN2.id, "seat_owner_user_id": ADMIN.id},
                  headers=h).json()
    assert {r["user_id"]: r["seat_owner_user_id"] for r in v["rows"]}[ADMIN2.id] == ADMIN.id
    assert rig.store.get_setting("seat_assignment:" + ADMIN2.id) == ADMIN.id
    e = rig.store.list_admin_events()[0]
    assert e["action"] == "seat_assign" and e["user_id"] == ADMIN.id
    assert e["detail"] == f"user={ADMIN2.id} seat={ADMIN2.id}->{ADMIN.id}"


def test_assign_all_sets_everyone_and_default(tmp_path):
    rig, _, seats = _setup(tmp_path)
    with _client(rig) as c:
        v = c.post("/agent-relay/seats/assignments/all", json={"seat_owner_user_id": ADMIN2.id},
                   headers={**H, **ORIGIN}).json()
        assert all(r["seat_owner_user_id"] == ADMIN2.id and r["source"] == "assigned" for r in v["rows"])
        assert v["default_seat_owner_user_id"] == ADMIN2.id
        # 1 人だけ別の席に
        c.put("/agent-relay/seats/assignments", json={"user_id": ADMIN.id, "seat_owner_user_id": ADMIN.id},
              headers={**H, **ORIGIN})
        s, r = _start(c, H)
        assert r.status_code == 200 and seats[-1] == (s["id"], ADMIN.id)
    acts = [e["action"] for e in rig.store.list_admin_events()]
    assert acts.count("seat_assign") == 3 and acts.count("seat_assign_all") == 1


def test_engines_view_tells_whose_seat_is_used(tmp_path):
    rig, _, _ = _setup(tmp_path, legacy_owner_user_id=ADMIN.id)
    with _client(rig) as c:
        mine = c.get("/agent-relay/engines", headers=H).json()["consult_seat"]
        other = c.get("/agent-relay/engines", headers=H2).json()["consult_seat"]
    assert mine == {"owner_is_me": True, "source": "default", "name": None}
    assert other["owner_is_me"] is False and other["name"] == "A004" and other["source"] == "default"


def test_names_fall_back_to_login_id_when_display_name_is_unreadable(tmp_path):
    rig, _, _ = _setup(tmp_path)
    rig.svc.sql = Sql(fail_display_name=True)
    with _client(rig) as c:
        v = c.get("/agent-relay/seats/assignments", headers=H).json()
    assert [r["name"] for r in v["rows"]] == ["A004", "A002"]
    assert "display_name" not in rig.svc.sql.queries[-1]
