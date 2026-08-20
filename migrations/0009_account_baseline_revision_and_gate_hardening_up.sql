-- 0009: baseline revision authority, ledger cutoff, and reconciliation gate hardening.
--
-- 1. Make account_baselines append-only (no UPDATE/DELETE) to preserve authority.
-- 2. Create account_baseline_revisions as immutable history with ledger cutoff.
-- 3. Harden reconciliation_mismatches kind check to be robust to old constraint names.
-- 4. No data in account_baselines is rewritten; existing rows are preserved and
--    migrated as initial revisions.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Fixup 0008's defensive block that was a no-op: ensure any old kind check is dropped.
ALTER TABLE public.reconciliation_mismatches DROP CONSTRAINT IF EXISTS reconciliation_mismatches_kind_check;
DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN SELECT conname FROM pg_constraint
           WHERE conrelid = 'public.reconciliation_mismatches'::regclass
             AND contype = 'c'
             AND pg_get_constraintdef(oid) ILIKE '%kind IN (%'
  LOOP
    EXECUTE format('ALTER TABLE public.reconciliation_mismatches DROP CONSTRAINT %I', r.conname);
  END LOOP;
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

-- Make account_baselines truly append-only (0008 allowed UPDATE of cash/effective_at).
DROP TRIGGER IF EXISTS account_baselines_guard_write ON public.account_baselines;
DROP FUNCTION IF EXISTS public.guard_account_baseline_write();

CREATE OR REPLACE FUNCTION public.guard_account_baseline_write()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    RAISE EXCEPTION 'account_baselines is append-only; UPDATE and DELETE are forbidden'
        USING ERRCODE = '55000';
    RETURN NULL;
END;
$$;

CREATE TRIGGER account_baselines_guard_write
BEFORE UPDATE OR DELETE ON public.account_baselines
FOR EACH ROW
EXECUTE FUNCTION public.guard_account_baseline_write();

REVOKE ALL ON FUNCTION public.guard_account_baseline_write() FROM PUBLIC;

-- Immutable revision history with explicit cutoff and provenance.
CREATE TABLE public.account_baseline_revisions (
    revision_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id TEXT NOT NULL CHECK (length(btrim(account_id)) > 0 AND length(account_id) <= 100),
    opening_cash_cents BIGINT NOT NULL CHECK (opening_cash_cents >= 0 AND opening_cash_cents <= 100000000000),
    effective_at TIMESTAMPTZ NOT NULL,
    cutoff_occurred_at TIMESTAMPTZ,
    cutoff_execution_id TEXT CHECK (cutoff_execution_id IS NULL OR (length(btrim(cutoff_execution_id)) > 0 AND length(cutoff_execution_id) <= 100)),
    reason TEXT NOT NULL CHECK (length(btrim(reason)) > 0 AND length(reason) <= 200),
    actor TEXT NOT NULL CHECK (length(btrim(actor)) > 0 AND length(actor) <= 100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CHECK (
        (cutoff_occurred_at IS NULL AND cutoff_execution_id IS NULL)
        OR (cutoff_occurred_at IS NOT NULL AND cutoff_execution_id IS NOT NULL)
    ),
    CHECK (effective_at <= created_at)
);

CREATE INDEX account_baseline_revisions_account_effective_idx
    ON public.account_baseline_revisions (account_id, effective_at DESC, created_at DESC);

CREATE OR REPLACE FUNCTION public.guard_account_baseline_revision_write()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    RAISE EXCEPTION 'account_baseline_revisions is append-only; UPDATE and DELETE are forbidden'
        USING ERRCODE = '55000';
    RETURN NULL;
END;
$$;

CREATE TRIGGER account_baseline_revisions_guard_write
BEFORE UPDATE OR DELETE ON public.account_baseline_revisions
FOR EACH ROW
EXECUTE FUNCTION public.guard_account_baseline_revision_write();

REVOKE ALL ON FUNCTION public.guard_account_baseline_revision_write() FROM PUBLIC;
REVOKE ALL ON TABLE public.account_baseline_revisions FROM PUBLIC;

COMMENT ON TABLE public.account_baseline_revisions IS
    'Append-only history of authoritative opening cash; each revision is immutable and carries its ledger cutoff.';
COMMENT ON COLUMN public.account_baseline_revisions.effective_at IS
    'Time from which this opening cash is authoritative; ledger replay must exclude fills at or before the cutoff.';
COMMENT ON COLUMN public.account_baseline_revisions.cutoff_occurred_at IS
    'Last fill included in the baseline cash; fills after this (or with greater execution_id at same timestamp) are post-baseline.';
COMMENT ON COLUMN public.account_baseline_revisions.cutoff_execution_id IS
    'Tie-breaker for same-timestamp fills at the cutoff.';

-- Migrate existing single-row baselines as initial revisions (genesis, no cutoff).
INSERT INTO public.account_baseline_revisions (revision_id, account_id, opening_cash_cents, effective_at, cutoff_occurred_at, cutoff_execution_id, reason, actor, created_at)
SELECT gen_random_uuid(), account_id, opening_cash_cents, effective_at, NULL, NULL, 'migrated from account_baselines (genesis)', 'migration-0009', created_at
FROM public.account_baselines
ON CONFLICT DO NOTHING;
