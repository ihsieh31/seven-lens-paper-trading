-- Disposable/restore-drill rollback only.
-- Relation qualification and secure SECURITY DEFINER search paths intentionally remain hardened.

ALTER TABLE public.audit_events
DROP CONSTRAINT IF EXISTS audit_events_typed_payload_check;
ALTER TABLE public.domain_events
DROP CONSTRAINT IF EXISTS domain_events_typed_payload_check;

DROP FUNCTION IF EXISTS public.audit_event_payload_is_valid(TEXT, JSONB);
DROP FUNCTION IF EXISTS public.domain_event_payload_is_valid(TEXT, JSONB);

DELETE FROM public.schema_migrations WHERE version = 2;
