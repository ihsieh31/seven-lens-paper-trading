-- 0020 down: remove the P4-B security master authority.
--
-- Drop order follows reverse dependency order: decision lineage, decisions,
-- event head, event lineage, identity heads, identity lineage, then the source
-- record log.  The btree_gist extension is left in place: it is shared
-- infrastructure and the up migration creates it idempotently.

DROP TRIGGER IF EXISTS security_quarantine_decision_sources_guard_truncate
    ON public.security_quarantine_decision_sources;
DROP TRIGGER IF EXISTS security_quarantine_decision_sources_guard_write
    ON public.security_quarantine_decision_sources;
DROP TRIGGER IF EXISTS security_quarantine_decisions_guard_truncate
    ON public.security_quarantine_decisions;
DROP TRIGGER IF EXISTS security_quarantine_decisions_guard_write
    ON public.security_quarantine_decisions;
DROP TRIGGER IF EXISTS corporate_action_event_sources_guard_truncate
    ON public.corporate_action_event_sources;
DROP TRIGGER IF EXISTS corporate_action_event_sources_guard_write
    ON public.corporate_action_event_sources;
DROP TRIGGER IF EXISTS corporate_action_events_guard_truncate
    ON public.corporate_action_events;
DROP TRIGGER IF EXISTS corporate_action_events_guard_write
    ON public.corporate_action_events;
DROP TRIGGER IF EXISTS security_identity_sources_guard_truncate
    ON public.security_identity_sources;
DROP TRIGGER IF EXISTS security_identity_sources_guard_write
    ON public.security_identity_sources;
DROP TRIGGER IF EXISTS security_identities_guard_truncate
    ON public.security_identities;
DROP TRIGGER IF EXISTS security_identities_guard_write
    ON public.security_identities;
DROP TRIGGER IF EXISTS p4_source_records_guard_truncate
    ON public.p4_source_records;
DROP TRIGGER IF EXISTS p4_source_records_guard_write
    ON public.p4_source_records;

DROP FUNCTION IF EXISTS public.record_quarantine_decision(TEXT, JSONB);
DROP FUNCTION IF EXISTS public.append_corporate_action_event(TEXT, TEXT, JSONB);
DROP FUNCTION IF EXISTS public.append_security_identity(TEXT, JSONB);
DROP FUNCTION IF EXISTS public.append_p4_source_record(TEXT, TEXT, TEXT, JSONB);

DROP TABLE IF EXISTS public.security_quarantine_decision_sources;
DROP TABLE IF EXISTS public.security_quarantine_decisions;
DROP TABLE IF EXISTS public.corporate_action_event_head;
DROP TABLE IF EXISTS public.corporate_action_event_sources;
DROP TABLE IF EXISTS public.corporate_action_events;
DROP TABLE IF EXISTS public.security_identity_heads;
DROP TABLE IF EXISTS public.security_identity_sources;
DROP TABLE IF EXISTS public.security_identities;
DROP TABLE IF EXISTS public.p4_source_records;

DELETE FROM public.schema_migrations WHERE version = 20;
