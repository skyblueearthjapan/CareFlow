import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rk import RkError  # noqa: E402
from rk import sqlrun  # noqa: E402
from rk.config import Config  # noqa: E402
from rk.readonly_posts import is_readonly_post  # noqa: E402
from rk.transport import normalize_path, remote_cmd  # noqa: E402


class Rec:
    def __init__(self, stdout=b'[{"a": 1}, \n {"a": 2}]\n', rc=0, stderr=b""):
        self.calls = []
        self.out = (stdout, rc, stderr)

    def __call__(self, cmd, input_bytes=None, timeout=0):
        self.calls.append((cmd, input_bytes))
        return subprocess.CompletedProcess(cmd, self.out[1], self.out[0], self.out[2])


def test_wrap_sql_readonly_and_single_statement():
    w = sqlrun.wrap_sql("select 1 as x;\n")
    assert w.startswith("set default_transaction_read_only = on;\nbegin transaction read only;\n")
    assert "select coalesce(json_agg(t), '[]'::json) from (\nselect 1 as x\n) t;" in w
    assert w.rstrip().endswith("rollback;")
    # 末尾の -- コメントでも閉じかっこが消えない
    assert "-- memo\n) t;" in sqlrun.wrap_sql("select 1 -- memo")
    with pytest.raises(RkError):
        sqlrun.wrap_sql("select 1; commit; delete from visits")
    with pytest.raises(RkError):
        sqlrun.wrap_sql("  ;  ")
    # 文字列やコメントの中の ; は許す
    sqlrun.wrap_sql("select 'a;b' as s -- x;y")


def test_param_validation():
    assert sqlrun.parse_params(["week_start=2026-10-12", "name=伊東"]) == {"week_start": "2026-10-12", "name": "伊東"}
    for bad in (["Week=1"], ["1x=1"], ["a-b=1"], ["nokey"], ["k=a\nb"], ["k=a\rb"]):
        with pytest.raises(RkError):
            sqlrun.parse_params(bad)


def test_run_sql_pc_uses_ssh_and_quotes_params():
    cfg = Config(mode="pc")
    r = Rec()
    rows = sqlrun.run_sql(cfg, "select :'d'::date as d", {"d": "2026-10-12 x'y"}, runner=r)
    assert rows == [{"a": 1}, {"a": 2}]
    cmd, stdin = r.calls[0]
    assert cmd[:4] == ["ssh", "-o", "BatchMode=yes", "root@72.60.211.213"]
    remote = cmd[4]
    assert "docker exec -i -e 'PGOPTIONS=-c default_transaction_read_only=on' carelink-postgres psql" in remote
    assert "-v ON_ERROR_STOP=1" in remote and "-At" in remote
    assert "'d=2026-10-12 x'\"'\"'y'" in remote  # シェル用に引用されている
    assert stdin.decode().startswith("set default_transaction_read_only = on;\nbegin transaction read only;")


def test_run_sql_vps_no_ssh_and_error():
    cfg = Config(mode="vps")
    r = Rec()
    sqlrun.run_sql(cfg, "select 1", runner=r)
    assert r.calls[0][0][0] == "docker"
    with pytest.raises(RkError, match="失敗"):
        sqlrun.run_sql(cfg, "select 1", runner=Rec(stdout=b"", rc=3, stderr=b"ERROR: boom"))


def test_parse_output_empty_and_noise():
    assert sqlrun.parse_output("[]\n") == []
    assert sqlrun.parse_output("SET\n[{\"x\":1}]\n") == [{"x": 1}]
    with pytest.raises(RkError):
        sqlrun.parse_output("nothing")


def test_sql_list_and_names(tmp_path):
    cfg = Config(sql_dir=tmp_path)
    (tmp_path / "visits-by-day.sql").write_text("-- 日ごとの訪問数\nselect 1", encoding="utf-8")
    (tmp_path / "plain.sql").write_text("select 2", encoding="utf-8")
    assert sqlrun.list_sql(cfg) == [{"name": "plain", "description": ""},
                                     {"name": "visits-by-day", "description": "日ごとの訪問数"}]
    assert sqlrun.sql_path(cfg, "visits-by-day").name == "visits-by-day.sql"
    for bad in ("../x", "A", "nope"):
        with pytest.raises(RkError):
            sqlrun.sql_path(cfg, bad)


def test_remote_cmd_modes():
    assert remote_cmd(Config(mode="vps"), ["ls", "a b"]) == ["ls", "a b"]
    assert remote_cmd(Config(mode="pc", ssh="u@h"), ["ls", "a b"])[-1] == "ls 'a b'"


def test_readonly_posts_allow_list():
    assert is_readonly_post("/integrations/unsent-summary")
    assert is_readonly_post("/api/v1/schedule/v2/copy-week/preview")
    assert is_readonly_post("/patients/abc-123/fixed-visits/validate")
    assert not is_readonly_post("/integrations/apply")
    assert not is_readonly_post("/schedule/v2/copy-week")
    assert not is_readonly_post("/patients/a/b/fixed-visits/validate")


def test_normalize_path():
    assert normalize_path("visits") == "/visits"
    assert normalize_path("/api/v1/visits?x=1") == "/visits?x=1"
    for bad in ("http://evil/x", "/a/../b", "//evil"):
        with pytest.raises(RkError):
            normalize_path(bad)


def test_ssh_tunnel_waits_for_port_and_terminates():
    import socket
    from rk.transport import SshTunnel

    class FakeProc:
        def __init__(self, cmd, **kw):
            self.cmd = cmd
            port = int(cmd[cmd.index("-L") + 1].split(":")[0])
            self.sock = socket.socket()
            self.sock.bind(("127.0.0.1", port))
            self.sock.listen(1)
            self.stopped = False
            self.stderr = None

        def poll(self):
            return 0 if self.stopped else None

        def terminate(self):
            self.stopped = True
            self.sock.close()

        def wait(self, t=None):
            return 0

    made = []

    def popen(cmd, **kw):
        made.append(FakeProc(cmd, **kw))
        return made[-1]
    with SshTunnel(Config(mode="pc", ssh="u@h"), popen=popen) as port:
        cmd = made[0].cmd
        assert cmd[:2] == ["ssh", "-N"] and cmd[-1] == "u@h" and f"{port}:127.0.0.1:18001" in cmd
    assert made[0].stopped


def test_ssh_tunnel_reports_ssh_failure():
    from rk.transport import SshTunnel

    class Dead:
        stderr = None

        def poll(self):
            return 255
    with pytest.raises(RkError, match="ポート転送"):
        with SshTunnel(Config(), popen=lambda cmd, **kw: Dead()):
            pass


def test_rkapi_retries_once_on_401():
    from rk.opsbase import ApiError
    from rk.session import RkApi

    class Tokens:
        n = 0

        def access_token(self):
            self.n += 1
            return f"t{self.n}"

        def invalidate_access(self):
            pass

    class H:
        calls = []

        def request(self, method, path, body=None, params=None, token=None):
            self.calls.append(token)
            if token == "t1":
                raise ApiError(401, None)
            return {"ok": token}
    api = RkApi(H(), Tokens())
    assert api.get("/auth/me") == {"ok": "t2"}



# ---------- 読み取り専用の守りを破る書き方（レビュー指摘の回帰） ----------
@pytest.mark.parametrize("bad", [
    # 文字列の中の -- をコメントと取り違えると ; が見えなくなる（指摘の文字列そのまま）
    "select '--', 1) t; commit; set default_transaction_read_only=off; delete from visits",
    "select '/*', 1) t; commit; delete from visits -- */",
    "select $$--$$, 1) t; commit; delete from visits",
    "select $x$ ' $x$, 1) t; commit; delete from visits",
    "select E'\\'--', 1) t; commit; delete from visits",
    "select \"a--\" from t; delete from visits",
    "select 1; select 2",
    "select 1;; ",
    "select 1 /* a /* nested */ still */; commit",
])
def test_semicolon_outside_literals_rejected(bad):
    with pytest.raises(RkError):
        sqlrun.wrap_sql(bad)


@pytest.mark.parametrize("ok, body", [
    ("select 'a;b' as s -- x;y", "select 'a;b' as s -- x;y"),
    ("select $$a;b$$ as s;", "select $$a;b$$ as s"),
    ("select $tag$ it's; $ $tag$ as s", "select $tag$ it's; $ $tag$ as s"),
    ("select E'it\\'s;' as s ;  -- 終わり\n", "select E'it\\'s;' as s"),
    ("select 'it''s;' as s", "select 'it''s;' as s"),
    ("select \"we;ird\" from x /* a /* b; */ c; */", "select \"we;ird\" from x /* a /* b; */ c; */"),
    ("select :'d'::date, x::text, a[1:2] from t", "select :'d'::date, x::text, a[1:2] from t"),
    ("select 'a:b \\ c' as s", "select 'a:b \\ c' as s"),
])
def test_allowed_sql_kept(ok, body):
    assert f"(\n{body}\n) t;" in sqlrun.wrap_sql(ok)


@pytest.mark.parametrize("bad", [
    "select 1 \\gexec",
    "\\! rm -rf /\nselect 1",
    "select 1\n\\set x 1",
    "\\copy visits to '/tmp/x'",
    "select :x",
    "select :\"col\" from t",
    "select :{?x}",
    "select :'Bad'",
    "select 'unterminated",
    "select $$unterminated",
    "select 1 /* open",
])
def test_meta_commands_and_unquoted_vars_rejected(bad):
    with pytest.raises(RkError):
        sqlrun.wrap_sql(bad)
