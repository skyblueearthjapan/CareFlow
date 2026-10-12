-- らく助エージェント 段 3 — DB の専用ロール 3 つ（設計 §2-3・PO 決定 D2）
--
-- 実行はまだしない。PO の了承の後、README.md §3 の手順で（何度流しても同じ結果になる＝冪等）:
--   docker exec -i carelink-postgres psql -X -v ON_ERROR_STOP=1 -U carelink -d carelink < agent/deploy/sql/roles.sql
-- パスワードはこのファイルに書かない（set-role-passwords.sh か \password で別に入れる）。
-- 戻す: roles-rollback.sql。確かめる: verify-roles.sql。
--
-- 前提（2026-10-12 本番を読むだけで確認）: PostgreSQL 16.13・DB carelink・アプリのロール carelink（superuser）・
--   スキーマは public だけ・RLS の表 0・ラージオブジェクト 0・pg_hba は host all all all scram-sha-256。
--
-- ロール:
--   agent_ro    読み取り専用（rk sql 用）。public の全表 SELECT。ただし
--                 ・kaipoke_credentials は表ごと不可（カイポケのログイン情報）
--                 ・users.password_hash は不可（列ごとの権限）
--                 ・patients.qr_token は不可（掲示 QR の合言葉。漏れると誰でも打刻できる）
--               既定で読み取り専用トランザクション・30 秒で打ち切り・接続 3 本まで。
--               ※ users・patients は列ごとの権限なので `select * from users|patients` は権限エラーになる（列を書く）。
--   agent_dump  バックアップ（pg_dump）専用。pg_read_all_data（PG14+ の既定ロール）＝全部読める。
--               ※ 限界: password_hash・カイポケの暗号化済みログイン情報も読める（pg_dump は全部読む必要がある）。
--                 このロールの鍵は中継の秘密ファイルにだけ置き、箱（モデル）には渡さない。中継の backup の道だけが使う。
--               もっと絞るなら、中継から使わず、ホストの root の cron／固定スクリプトで pg_dump する案（README §3-4）。
--   agent_relay 中継の記録（スキーマ agent: sessions・events・plans・approvals・runs・tool_calls）の読み書きだけ。
--               スキーマ agent の持ち主は carelink のまま（表は migration 0091 が carelink で作る）。agent_relay は
--               USAGE と表の SELECT/INSERT/UPDATE/DELETE だけ（DDL・DROP はできない）。public は読めない。

\set ON_ERROR_STOP on

BEGIN;

-- ------------------------------------------------------------------ 1. ロールを作る（無ければ）
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_ro') THEN
    CREATE ROLE agent_ro LOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_dump') THEN
    CREATE ROLE agent_dump LOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_relay') THEN
    CREATE ROLE agent_relay LOGIN;
  END IF;
END
$$;

-- 属性は毎回そろえ直す（だれかが変えていても戻る）
ALTER ROLE agent_ro    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 3;
ALTER ROLE agent_dump  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT   NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 1;
ALTER ROLE agent_relay LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 5;

ALTER ROLE agent_ro SET default_transaction_read_only = on;
ALTER ROLE agent_ro SET statement_timeout = '30s';
ALTER ROLE agent_ro SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE agent_ro SET search_path = public;
ALTER ROLE agent_ro SET timezone = 'Asia/Tokyo';

ALTER ROLE agent_dump SET default_transaction_read_only = on;
ALTER ROLE agent_dump SET idle_in_transaction_session_timeout = '10min';

ALTER ROLE agent_relay SET statement_timeout = '30s';
ALTER ROLE agent_relay SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE agent_relay SET search_path = agent;

-- ------------------------------------------------------------------ 2. agent_ro（読み取り専用）
GRANT CONNECT ON DATABASE carelink TO agent_ro;
GRANT USAGE ON SCHEMA public TO agent_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO agent_ro;

-- 今後 carelink（alembic）が作る表も読めるように。※ 新しい表に秘密の列が増えたら、この SQL の
--   「外す」一覧に足して流し直す（README の migration 後の点検）。
ALTER DEFAULT PRIVILEGES FOR ROLE carelink IN SCHEMA public GRANT SELECT ON TABLES TO agent_ro;

-- 外す: 表ごと
REVOKE ALL ON TABLE public.kaipoke_credentials FROM agent_ro;

-- 外す: 列ごと（表の SELECT を外し、許す列だけ列単位で付け直す）。列が増えても、流し直すまで新しい列は読めない（安全側）
DO $$
DECLARE
  deny  CONSTANT jsonb := '{"users": ["password_hash"], "patients": ["qr_token"]}';
  t     text;
  cols  text;
BEGIN
  FOR t IN SELECT jsonb_object_keys(deny) LOOP
    IF to_regclass('public.' || quote_ident(t)) IS NULL THEN
      RAISE NOTICE 'skip: public.% がありません', t;
      CONTINUE;
    END IF;
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM agent_ro', t);
    -- 前回の列の権限も消してから付け直す（列が消えた・外す列が増えた場合に備える）
    SELECT string_agg(quote_ident(column_name), ', ' ORDER BY ordinal_position)
      INTO cols
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = t;
    IF cols IS NOT NULL THEN
      EXECUTE format('REVOKE SELECT (%s) ON TABLE public.%I FROM agent_ro', cols, t);
    END IF;
    SELECT string_agg(quote_ident(column_name), ', ' ORDER BY ordinal_position)
      INTO cols
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = t
       AND NOT (column_name = ANY (SELECT jsonb_array_elements_text(deny -> t)));
    IF cols IS NOT NULL THEN
      EXECUTE format('GRANT SELECT (%s) ON TABLE public.%I TO agent_ro', cols, t);
    END IF;
  END LOOP;
END
$$;

-- agent スキーマ（中継の記録）は agent_ro から読ませない（会話の中身・承認番号があるため）
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'agent') THEN
    EXECUTE 'REVOKE ALL ON SCHEMA agent FROM agent_ro';
  END IF;
END
$$;

-- ------------------------------------------------------------------ 3. agent_dump（バックアップ専用）
GRANT CONNECT ON DATABASE carelink TO agent_dump;
GRANT pg_read_all_data TO agent_dump;

-- ------------------------------------------------------------------ 4. agent_relay（スキーマ agent だけ）
GRANT CONNECT ON DATABASE carelink TO agent_relay;
-- スキーマ agent は migration 0091 が作る（CREATE SCHEMA IF NOT EXISTS agent）。先に流したときのため、ここでも作る
CREATE SCHEMA IF NOT EXISTS agent AUTHORIZATION carelink;
REVOKE ALL ON SCHEMA agent FROM PUBLIC;
GRANT USAGE ON SCHEMA agent TO agent_relay;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA agent TO agent_relay;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA agent TO agent_relay;
ALTER DEFAULT PRIVILEGES FOR ROLE carelink IN SCHEMA agent GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agent_relay;
ALTER DEFAULT PRIVILEGES FOR ROLE carelink IN SCHEMA agent GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO agent_relay;
-- public は読ませない（中継は backend の API と agent_ro で読む）。PUBLIC から既定で来る CREATE も無い（PG15+）
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM agent_relay;

COMMIT;

-- 結果の表示（秘密は出さない）
SELECT r.rolname, r.rolcanlogin, r.rolconnlimit, r.rolsuper, (r.rolpassword IS NOT NULL) AS has_password
  FROM pg_authid r
 WHERE r.rolname IN ('agent_ro', 'agent_dump', 'agent_relay')
 ORDER BY 1;
