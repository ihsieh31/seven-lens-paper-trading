-- 0008 down migration: revert account/ledger hardening.
-- Applied only against a disposable/restore-drill database.

DROP TRIGGER IF EXISTS account_baselines_guard_write ON public.account_baselines;
DROP FUNCTION IF EXISTS public.guard_account_baseline_write();
DROP TABLE IF EXISTS public.account_baselines;

-- The older 0007 checks cannot represent the account-level mismatch kinds
-- introduced by this migration.  A downgrade is explicitly a destructive
-- restore-drill operation, so remove any affected run and its child evidence
-- before narrowing either check.  The append-only triggers are disabled only
-- for this bounded cleanup; their historical protection is restored before
-- the downgrade commits.
ALTER TABLE public.reconciliation_mismatches
    DISABLE TRIGGER reconciliation_mismatches_append_only;

ALTER TABLE public.reconciliation_runs
    DISABLE TRIGGER reconciliation_runs_append_only;

CREATE TEMPORARY TABLE p2_0008_incompatible_runs (
    run_id UUID PRIMARY KEY
) ON COMMIT DROP;

INSERT INTO p2_0008_incompatible_runs (run_id)
SELECT run_id
FROM public.reconciliation_runs
WHERE mismatch_kinds && ARRAY[
    'LOCAL_LEDGER_INVARIANT', 'ACCOUNT_ID_MISMATCH', 'CASH_MISMATCH',
    'NAV_MISMATCH', 'BUYING_POWER_MISMATCH',
    'ACCOUNT_RECONCILIATION_UNAVAILABLE'
]::TEXT[]
UNION
SELECT run_id
FROM public.reconciliation_mismatches
WHERE kind = ANY (ARRAY[
    'LOCAL_LEDGER_INVARIANT', 'ACCOUNT_ID_MISMATCH', 'CASH_MISMATCH',
    'NAV_MISMATCH', 'BUYING_POWER_MISMATCH',
    'ACCOUNT_RECONCILIATION_UNAVAILABLE'
]::TEXT[]);

DELETE FROM public.reconciliation_mismatches
WHERE run_id IN (SELECT run_id FROM p2_0008_incompatible_runs);

DELETE FROM public.reconciliation_runs
WHERE run_id IN (SELECT run_id FROM p2_0008_incompatible_runs);

ALTER TABLE public.reconciliation_mismatches
    ENABLE TRIGGER reconciliation_mismatches_append_only;

ALTER TABLE public.reconciliation_runs
    ENABLE TRIGGER reconciliation_runs_append_only;

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
