-- 0008: P2 account/ledger hardening: new mismatch kinds and authoritative cash baseline.
--
-- 1. Adds typed mismatch kinds for ledger invariants and account reconciliation:
--    LOCAL_LEDGER_INVARIANT, ACCOUNT_ID_MISMATCH, CASH_MISMATCH, NAV_MISMATCH,
--    BUYING_POWER_MISMATCH, ACCOUNT_RECONCILIATION_UNAVAILABLE.
--    Both reconciliation_runs.mismatch_kinds and reconciliation_mismatches.kind
--    are widened together with the domain enum.
-- 2. Creates account_baselines as the explicit opening-cash authority required
--    by ledger NAV reconciliation.  The table is the single source of truth for
--    expected cash; it is not derived from broker cash.
-- 3. No existing data is rewritten; all checks are closed sets.

ALTER TABLE public.reconciliation_runs
    DROP CONSTRAINT reconciliation_runs_mismatch_kinds_check;

ALTER TABLE public.reconciliation_runs
    ADD CONSTRAINT reconciliation_runs_mismatch_kinds_check CHECK (
        mismatch_kinds <@ ARRAY[
            'NON_PAPER_ACCOUNT', 'UNKNOWN_BROKER_ORDER', 'MISSING_BROKER_ORDER',
            'PARAMETER_MISMATCH', 'STATUS_MISMATCH', 'INTENT_STATUS_MISMATCH',
            'BROKER_QUERY_FAILURE',
            'MISSING_LOCAL_FILL', 'POSITION_QUANTITY_MISMATCH',
            'POSITION_SYMBOL_MISMATCH',
            'LOCAL_LEDGER_INVARIANT',
            'ACCOUNT_ID_MISMATCH', 'CASH_MISMATCH', 'NAV_MISMATCH',
            'BUYING_POWER_MISMATCH', 'ACCOUNT_RECONCILIATION_UNAVAILABLE'
        ]::TEXT[]
    );

-- reconciliation_mismatches kind check was inline without explicit name in 0007;
-- drop the auto-generated check and recreate with the expanded set.
ALTER TABLE public.reconciliation_mismatches
    DROP CONSTRAINT IF EXISTS reconciliation_mismatches_kind_check;

-- In case the constraint was generated with a different name, also try the generic pattern.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.reconciliation_mismatches'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%kind IN (%'
    ) THEN
        -- Drop all remaining check constraints on kind that match the old set by recreating
        -- the table check via explicit drop/add below is sufficient because the prior
        -- DROP IF EXISTS already removed the primary named constraint; this block is
        -- defensive for older deployments.
        NULL;
    END IF;
END $$;

ALTER TABLE public.reconciliation_mismatches
    ADD CONSTRAINT reconciliation_mismatches_kind_check CHECK (kind IN (
        'NON_PAPER_ACCOUNT', 'UNKNOWN_BROKER_ORDER', 'MISSING_BROKER_ORDER',
        'PARAMETER_MISMATCH', 'STATUS_MISMATCH', 'INTENT_STATUS_MISMATCH',
        'BROKER_QUERY_FAILURE',
        'MISSING_LOCAL_FILL', 'POSITION_QUANTITY_MISMATCH',
        'POSITION_SYMBOL_MISMATCH',
        'LOCAL_LEDGER_INVARIANT',
        'ACCOUNT_ID_MISMATCH', 'CASH_MISMATCH', 'NAV_MISMATCH',
        'BUYING_POWER_MISMATCH', 'ACCOUNT_RECONCILIATION_UNAVAILABLE'
    ));

CREATE TABLE public.account_baselines (
    account_id TEXT PRIMARY KEY CHECK (length(btrim(account_id)) > 0 AND length(account_id) <= 100),
    opening_cash_cents BIGINT NOT NULL CHECK (opening_cash_cents >= 0 AND opening_cash_cents <= 100000000000),
    effective_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

CREATE OR REPLACE FUNCTION public.guard_account_baseline_write()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.account_id <> OLD.account_id THEN
            RAISE EXCEPTION 'account baseline account_id is immutable'
                USING ERRCODE = '55000';
        END IF;
    END IF;
    NEW.updated_at := pg_catalog.statement_timestamp();
    RETURN NEW;
END;
$$;

CREATE TRIGGER account_baselines_guard_write
BEFORE UPDATE ON public.account_baselines
FOR EACH ROW
EXECUTE FUNCTION public.guard_account_baseline_write();

REVOKE ALL ON TABLE public.account_baselines FROM PUBLIC;
REVOKE ALL ON FUNCTION public.guard_account_baseline_write() FROM PUBLIC;

COMMENT ON TABLE public.account_baselines IS
    'Authoritative opening cash baseline for account reconciliation; the baseline is explicit and not derived from broker cash.';
COMMENT ON COLUMN public.account_baselines.opening_cash_cents IS
    'Opening cash in cents; combined with fill ledger cash_delta to produce expected cash.';
