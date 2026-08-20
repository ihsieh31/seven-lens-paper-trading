-- 0008 down migration: revert account/ledger hardening.
-- Applied only against a disposable/restore-drill database.

DROP TRIGGER IF EXISTS account_baselines_guard_write ON public.account_baselines;
DROP FUNCTION IF EXISTS public.guard_account_baseline_write();
DROP TABLE IF EXISTS public.account_baselines;

ALTER TABLE public.reconciliation_mismatches
    DROP CONSTRAINT IF EXISTS reconciliation_mismatches_kind_check;

ALTER TABLE public.reconciliation_mismatches
    ADD CONSTRAINT reconciliation_mismatches_kind_check CHECK (kind IN (
        'NON_PAPER_ACCOUNT', 'UNKNOWN_BROKER_ORDER', 'MISSING_BROKER_ORDER',
        'PARAMETER_MISMATCH', 'STATUS_MISMATCH', 'INTENT_STATUS_MISMATCH',
        'BROKER_QUERY_FAILURE',
        'MISSING_LOCAL_FILL', 'POSITION_QUANTITY_MISMATCH',
        'POSITION_SYMBOL_MISMATCH'
    ));

ALTER TABLE public.reconciliation_runs
    DROP CONSTRAINT reconciliation_runs_mismatch_kinds_check;

ALTER TABLE public.reconciliation_runs
    ADD CONSTRAINT reconciliation_runs_mismatch_kinds_check CHECK (
        mismatch_kinds <@ ARRAY[
            'NON_PAPER_ACCOUNT', 'UNKNOWN_BROKER_ORDER', 'MISSING_BROKER_ORDER',
            'PARAMETER_MISMATCH', 'STATUS_MISMATCH', 'INTENT_STATUS_MISMATCH',
            'BROKER_QUERY_FAILURE',
            'MISSING_LOCAL_FILL', 'POSITION_QUANTITY_MISMATCH',
            'POSITION_SYMBOL_MISMATCH'
        ]::TEXT[]
    );

DELETE FROM public.schema_migrations WHERE version = 8;
