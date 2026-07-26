-- One-time setup: a SELECT-only Postgres role for the chatbot's runtime
-- queries. Run once against the target database as an owner/superuser:
--
--   psql -U <owner> -d supplychain_automation -f setup_readonly_role.sql
--
-- This is layer two of the SQL safety model (layer one is the guard in
-- backend/app/graph/guard.py). Even if the guard were somehow bypassed,
-- this role physically cannot INSERT/UPDATE/DELETE/DROP anything.

DO $$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'chatbot_ro') THEN
      CREATE ROLE chatbot_ro LOGIN PASSWORD 'chatbot_ro_local_dev';
   END IF;
END
$$;

GRANT CONNECT ON DATABASE supplychain_automation TO chatbot_ro;
GRANT USAGE ON SCHEMA public TO chatbot_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO chatbot_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO chatbot_ro;
ALTER ROLE chatbot_ro SET statement_timeout = '8000';

-- NOTE: change the password above (and CHATBOT_DATABASE_URL in .env) before
-- using this anywhere beyond local development.
