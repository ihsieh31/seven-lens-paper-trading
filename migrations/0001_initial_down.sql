-- The P1-B initial schema has no in-place destructive downgrade.
-- Use this only against a disposable or restored database.

DROP TRIGGER IF EXISTS job_instances_guard_status_write ON job_instances;
DROP TRIGGER IF EXISTS audit_events_append_only ON audit_events;
DROP TRIGGER IF EXISTS audit_events_validate_and_stamp ON audit_events;
DROP TRIGGER IF EXISTS domain_events_append_only ON domain_events;
DROP TRIGGER IF EXISTS domain_events_enforce_sequence ON domain_events;

DROP FUNCTION IF EXISTS transition_job_status(TEXT, TEXT, BIGINT, TEXT);
DROP FUNCTION IF EXISTS release_job_lease(TEXT, TEXT, BIGINT, TEXT);
DROP FUNCTION IF EXISTS renew_job_lease(TEXT, TEXT, BIGINT, INTERVAL);
DROP FUNCTION IF EXISTS acquire_job_lease(TEXT, TEXT, INTERVAL);
DROP FUNCTION IF EXISTS guard_job_instance_status_write();
DROP FUNCTION IF EXISTS prevent_append_only_mutation();
DROP FUNCTION IF EXISTS validate_and_stamp_audit_event();

DROP TABLE IF EXISTS job_leases;
DROP TABLE IF EXISTS job_instances;
DROP TABLE IF EXISTS audit_events;
DROP TABLE IF EXISTS domain_events;

DROP FUNCTION IF EXISTS audit_payload_contains_secret(JSONB);
DROP FUNCTION IF EXISTS enforce_domain_event_sequence_and_timestamp();

DROP TABLE IF EXISTS schema_migrations;
DROP TABLE IF EXISTS schema_metadata;
