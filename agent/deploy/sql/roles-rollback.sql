-- roles.sql を戻す（ロール 3 つと、その権限を消す）。冪等。
--   docker exec -i carelink-postgres psql -X -v ON_ERROR_STOP=1 -U carelink -d carelink < agent/deploy/sql/roles-rollback.sql
--
-- 先に中継を止めること（docker compose -p carelink-agent ... down）。接続が残っていると DROP ROLE が失敗する
-- （下で該当ロールの接続を切ってから消す）。
-- スキーマ agent とその表（会話の記録）は消さない。消すのは migration 0091 の downgrade（PO 了承のうえ）。

\set ON_ERROR_STOP on

-- 残っている接続を切る
SELECT pg_terminate_backend(pid)
  FROM pg_stat_activity
 WHERE usename IN ('agent_ro', 'agent_dump', 'agent_relay');

BEGIN;

DO $$
DECLARE
  r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['agent_ro', 'agent_dump', 'agent_relay'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      -- 既定の権限（ALTER DEFAULT PRIVILEGES）を外す
      EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE carelink IN SCHEMA public REVOKE ALL ON TABLES FROM %I', r);
      IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'agent') THEN
        EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE carelink IN SCHEMA agent REVOKE ALL ON TABLES FROM %I', r);
        EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE carelink IN SCHEMA agent REVOKE ALL ON SEQUENCES FROM %I', r);
      END IF;
      -- このロールに付いた権限をすべて外し、持ち物があれば carelink へ（通常は無い）
      EXECUTE format('REASSIGN OWNED BY %I TO carelink', r);
      EXECUTE format('DROP OWNED BY %I', r);
      EXECUTE format('REVOKE pg_read_all_data FROM %I', r);
      EXECUTE format('REVOKE ALL ON DATABASE carelink FROM %I', r);
      EXECUTE format('DROP ROLE %I', r);
      RAISE NOTICE 'dropped role %', r;
    END IF;
  END LOOP;
END
$$;

COMMIT;

SELECT count(*) AS remaining_agent_roles
  FROM pg_roles WHERE rolname IN ('agent_ro', 'agent_dump', 'agent_relay');
