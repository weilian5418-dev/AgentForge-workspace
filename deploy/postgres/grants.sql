-- AgentForge role grants for app / queue / graph / index.
-- Run as a privileged role (e.g. agentforge) after schema objects exist.
-- Migrator retains DDL; resident services must not receive migrator credentials.

BEGIN;

-- Schema usage
GRANT USAGE ON SCHEMA app TO agentforge_api, agentforge_driver;
REVOKE ALL ON SCHEMA app FROM agentforge_worker;
GRANT USAGE ON SCHEMA queue TO agentforge_driver, agentforge_worker, agentforge_migrator;
GRANT USAGE ON SCHEMA graph TO agentforge_driver, agentforge_worker, agentforge_migrator;
GRANT USAGE ON SCHEMA index TO agentforge_migrator;

-- Default privileges for future app tables created by migrator
ALTER DEFAULT PRIVILEGES FOR ROLE agentforge_migrator IN SCHEMA app
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agentforge_api, agentforge_driver;
ALTER DEFAULT PRIVILEGES FOR ROLE agentforge_migrator IN SCHEMA app
  GRANT USAGE, SELECT ON SEQUENCES TO agentforge_api, agentforge_driver;
ALTER DEFAULT PRIVILEGES FOR ROLE agentforge_migrator IN SCHEMA queue
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agentforge_driver, agentforge_worker;
ALTER DEFAULT PRIVILEGES FOR ROLE agentforge_migrator IN SCHEMA queue
  GRANT USAGE, SELECT ON SEQUENCES TO agentforge_driver, agentforge_worker;

-- Core app DML for API and Driver (explicit list; no DDL)
GRANT SELECT, INSERT, UPDATE, DELETE ON
  app.projects,
  app.runs,
  app.plans,
  app.work_packages,
  app.task_attempts,
  app.events,
  app.outbox,
  app.run_leases,
  app.idempotency_requests,
  app.async_operations,
  app.provider_profiles,
  app.model_authorization_grants,
  app.context_manifests,
  app.budget_accounts,
  app.budget_reservations,
  app.model_calls,
  app.model_attempts,
  app.usage_records,
  app.settlement_records,
  app.provider_capability_checks,
  app.agent_products,
  app.agent_installations,
  app.agent_profiles,
  app.capability_checks,
  app.approval_mappings,
  app.runner_operations,
  app.content_objects,
  app.artifact_candidates,
  app.artifact_validation_reports,
  app.artifact_publications,
  app.git_dirty_snapshots,
  app.git_workspaces,
  app.git_workspace_snapshots,
  app.git_integrations
TO agentforge_api, agentforge_driver;

-- Queue transport DML (driver + worker). API must not hold queue credentials.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA queue
  TO agentforge_driver, agentforge_worker;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA queue
  TO agentforge_driver, agentforge_worker;

-- Historical migration audit: read-only for services
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON app.schema_migrations FROM agentforge_api;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON app.schema_migrations FROM agentforge_driver;
GRANT SELECT ON app.schema_migrations TO agentforge_api, agentforge_driver;

-- Alembic version: services may SELECT for readiness; only migrator writes
REVOKE ALL ON TABLE app.alembic_version FROM agentforge_api, agentforge_driver, agentforge_worker;
GRANT SELECT ON TABLE app.alembic_version TO agentforge_api, agentforge_driver;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE app.alembic_version TO agentforge_migrator;

-- Worker must not write app business state
REVOKE ALL ON ALL TABLES IN SCHEMA app FROM agentforge_worker;

COMMIT;
