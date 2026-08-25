ALTER TABLE public.proposal_contexts
    DROP CONSTRAINT IF EXISTS proposal_contexts_superseded_proposal_fkey;
ALTER TABLE public.risk_rejection_feedback
    DROP CONSTRAINT IF EXISTS risk_rejection_feedback_proposal_fkey;
DROP FUNCTION IF EXISTS public.advance_proposal_stage(UUID, TEXT, TEXT, TEXT, TEXT);
DROP FUNCTION IF EXISTS public.create_proposal_run(UUID, UUID, UUID, TEXT);
DROP FUNCTION IF EXISTS public.register_proposal_context(UUID, UUID, INTEGER, TEXT, UUID, UUID, TEXT, UUID, TEXT, TEXT, TEXT);
DROP FUNCTION IF EXISTS public.register_risk_feedback(UUID, UUID, TEXT, TEXT);
DROP FUNCTION IF EXISTS public.register_research_bundle(UUID, UUID, TEXT, TIMESTAMPTZ, TEXT, TIMESTAMPTZ, TEXT, TEXT, JSONB, TEXT, TEXT);
DROP TABLE IF EXISTS public.proposal_stage_results;
DROP TABLE IF EXISTS public.portfolio_proposals;
DROP TABLE IF EXISTS public.risk_debates;
DROP TABLE IF EXISTS public.proposal_runs;
DROP TABLE IF EXISTS public.proposal_contexts;
DROP TABLE IF EXISTS public.risk_rejection_feedback;
DROP TABLE IF EXISTS public.research_bundle_items;
DROP TABLE IF EXISTS public.research_bundles;
DROP FUNCTION IF EXISTS public.guard_proposal_stage_result_write();
DROP FUNCTION IF EXISTS public.guard_proposal_run_write();
DROP FUNCTION IF EXISTS public.p3d_derive_run_id(TEXT, TEXT[]);
DROP FUNCTION IF EXISTS public.p3d_text_is_safe(TEXT);
DROP FUNCTION IF EXISTS public.p3d_canonical_json(JSON);

-- Migration 0011 removes PostgreSQL's default PUBLIC EXECUTE from every
-- public-schema function.  Restore only pgcrypto's extension-owned functions
-- so a 11 -> 10 rollback reproduces the v10 ACL without reopening application
-- SECURITY DEFINER functions that earlier migrations explicitly revoked.
DO $$
DECLARE
    v_signature TEXT;
BEGIN
    FOR v_signature IN
        SELECT pg_catalog.format(
            '%I.%I(%s)', namespace.nspname, procedure.proname,
            pg_catalog.pg_get_function_identity_arguments(procedure.oid)
        )
        FROM pg_catalog.pg_proc AS procedure
        JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
        JOIN pg_catalog.pg_depend AS dependency
          ON dependency.classid = 'pg_proc'::regclass
         AND dependency.objid = procedure.oid
         AND dependency.deptype = 'e'
        JOIN pg_catalog.pg_extension AS extension ON extension.oid = dependency.refobjid
        WHERE extension.extname = 'pgcrypto' AND namespace.nspname = 'public'
    LOOP
        EXECUTE pg_catalog.format('GRANT EXECUTE ON FUNCTION %s TO PUBLIC', v_signature);
    END LOOP;
END;
$$;

ALTER TABLE public.analysis_runs
    DROP CONSTRAINT IF EXISTS analysis_runs_run_input_unique;

DELETE FROM public.schema_migrations WHERE version = 11;
