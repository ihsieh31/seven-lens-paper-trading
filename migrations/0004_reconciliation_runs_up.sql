-- P2-C reconciliation runs.  Append-only evidence of every broker comparison.

CREATE TABLE public.reconciliation_runs (
    run_id UUID PRIMARY KEY,
    trading_date DATE NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('CLEAN', 'MISMATCH')),
    mismatch_count INTEGER NOT NULL CHECK (mismatch_count >= 0),
    mismatch_kinds TEXT[] NOT NULL DEFAULT '{}' CHECK (
        mismatch_kinds <@ ARRAY[
            'NON_PAPER_ACCOUNT', 'UNKNOWN_BROKER_ORDER', 'MISSING_BROKER_ORDER',
            'PARAMETER_MISMATCH', 'STATUS_MISMATCH', 'BROKER_QUERY_FAILURE',
            'MISSING_LOCAL_FILL',
            'POSITION_QUANTITY_MISMATCH', 'POSITION_SYMBOL_MISMATCH'
        ]::TEXT[]
    ),
    checked_orders INTEGER NOT NULL CHECK (checked_orders >= 0),
    checked_fills INTEGER NOT NULL CHECK (checked_fills >= 0),
    observed_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CHECK (
        (status = 'CLEAN' AND mismatch_count = 0 AND mismatch_kinds = '{}')
        OR (status = 'MISMATCH' AND mismatch_count > 0)
    ),
    CHECK (mismatch_count = cardinality(mismatch_kinds))
);

CREATE TRIGGER reconciliation_runs_append_only
BEFORE UPDATE OR DELETE ON public.reconciliation_runs
FOR EACH ROW
EXECUTE FUNCTION public.prevent_append_only_mutation();

REVOKE UPDATE, DELETE ON TABLE public.reconciliation_runs FROM PUBLIC;

COMMENT ON TABLE public.reconciliation_runs IS
    'Append-only reconciliation evidence; a MISMATCH run must pause new entries.';
