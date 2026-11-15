-- Provision a server-only database login in a dedicated VetStock project.
-- The password is generated inside PostgreSQL and kept in Supabase Vault;
-- no password is present in this migration or in the Git repository.
DO $$
DECLARE generated_password TEXT;
BEGIN
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='vetstock_app') THEN
    RAISE EXCEPTION 'The vetstock_app role already exists; do not replace its credentials automatically';
  END IF;
  generated_password := pg_catalog.encode(extensions.gen_random_bytes(48), 'hex');
  EXECUTE pg_catalog.format(
    'CREATE ROLE vetstock_app LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT',
    generated_password
  );
  PERFORM vault.create_secret(generated_password, 'vetstock_render_db_password',
                             'Dedicated database credential for the VetStock Render service');
END $$;

GRANT USAGE ON SCHEMA vetstock TO vetstock_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA vetstock TO vetstock_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA vetstock TO vetstock_app;

-- Only the dedicated server role is granted rows; the public API roles remain denied.
DO $$
DECLARE item RECORD;
BEGIN
  FOR item IN SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='vetstock' LOOP
    EXECUTE pg_catalog.format(
      'CREATE POLICY vetstock_server_only ON vetstock.%I FOR ALL TO vetstock_app USING (true) WITH CHECK (true)',
      item.tablename
    );
  END LOOP;
END $$;
