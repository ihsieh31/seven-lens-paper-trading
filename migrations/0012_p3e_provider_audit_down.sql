DROP FUNCTION IF EXISTS public.register_model_call_attempt(
    UUID, UUID, UUID, UUID, TEXT, TEXT, INTEGER, TEXT, TEXT, TEXT, TEXT,
    INTEGER, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN, INTEGER, INTEGER,
    INTEGER, TIMESTAMPTZ, TIMESTAMPTZ, TEXT, TEXT, TEXT, TEXT, TEXT
);
DROP TABLE IF EXISTS public.model_call_audits;
DROP FUNCTION IF EXISTS public.claim_model_call_attempt(
    UUID, UUID, UUID, UUID, TEXT, TEXT, INTEGER, TEXT, TEXT, TEXT, TEXT,
    INTEGER, TEXT, TEXT, TEXT
);
DROP TABLE IF EXISTS public.model_call_claims;
DROP FUNCTION IF EXISTS public.guard_model_call_claim_write();
DELETE FROM public.schema_migrations WHERE version = 12;
