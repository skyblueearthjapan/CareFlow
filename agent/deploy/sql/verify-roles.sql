-- roles.sql の後の確認（読むだけ・秘密は出さない）。carelink で流す:
--   docker exec -i carelink-postgres psql -X -U carelink -d carelink < agent/deploy/sql/verify-roles.sql
-- 期待: 1) 3 行・has_password = t  2) すべて f  3) 0 行  4) すべて t  5) t

\echo '1) ロール'
SELECT rolname, rolcanlogin, rolconnlimit, rolsuper, (rolpassword IS NOT NULL) AS has_password
  FROM pg_authid WHERE rolname IN ('agent_ro', 'agent_dump', 'agent_relay') ORDER BY 1;

\echo '2) agent_ro が読めてはいけないもの（すべて f）'
SELECT 'kaipoke_credentials' AS what, has_table_privilege('agent_ro', 'public.kaipoke_credentials', 'SELECT') AS can_read
UNION ALL SELECT 'users.password_hash', has_column_privilege('agent_ro', 'public.users', 'password_hash', 'SELECT')
UNION ALL SELECT 'patients.qr_token', has_column_privilege('agent_ro', 'public.patients', 'qr_token', 'SELECT')
UNION ALL SELECT 'agent_ro: INSERT visits', has_table_privilege('agent_ro', 'public.visits', 'INSERT')
UNION ALL SELECT 'agent_relay: SELECT visits', has_table_privilege('agent_relay', 'public.visits', 'SELECT')
UNION ALL SELECT 'agent_relay: CREATE in agent', has_schema_privilege('agent_relay', 'agent', 'CREATE');

\echo '3) agent_ro が読めない public の表（kaipoke_credentials だけのはず＝ 1 行）'
SELECT c.relname
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v', 'm', 'p')
   AND NOT has_table_privilege('agent_ro', c.oid, 'SELECT')
   AND NOT EXISTS (SELECT 1 FROM information_schema.column_privileges p
                    WHERE p.grantee = 'agent_ro' AND p.table_schema = 'public' AND p.table_name = c.relname)
 ORDER BY 1;

\echo '4) 読めるべきもの（すべて t）'
SELECT 'users.email' AS what, has_column_privilege('agent_ro', 'public.users', 'email', 'SELECT') AS ok
UNION ALL SELECT 'patients.name', has_column_privilege('agent_ro', 'public.patients', 'name', 'SELECT')
UNION ALL SELECT 'visits', has_table_privilege('agent_ro', 'public.visits', 'SELECT')
UNION ALL SELECT 'agent_dump in pg_read_all_data', pg_has_role('agent_dump', 'pg_read_all_data', 'MEMBER')
UNION ALL SELECT 'agent_relay USAGE agent', has_schema_privilege('agent_relay', 'agent', 'USAGE');

\echo '5) agent_ro の既定設定に読み取り専用が入っている'
SELECT bool_or(cfg = 'default_transaction_read_only=on') AS read_only_default
  FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole, unnest(s.setconfig) AS cfg
 WHERE r.rolname = 'agent_ro';
