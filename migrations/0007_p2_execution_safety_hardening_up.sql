-- 0007: P2 second remediation hardening of execution safety.
--
-- 1. REVIEW_REQUIRED joins the closed intent lifecycle, and the full Alpaca
--    status set joins the mirror lifecycle.  The SQL guard functions extend
--    exactly together with the domain maps
--    (tests/integration/test_execution_schema.py::test_sql_transition_functions_match_the_python_maps).
-- 2. The 0006 broker_updated_at backfill approximated broker time with the
--    local record clock, which can sit ABOVE the true broker watermark and
--    silently drop real broker events (the Defect E trap).  That
--    approximation is unrecoverable, so 0007 clears the watermark to NULL
--    (unknown) and the domain reads NULL as the submitted_at lower bound: a
--    lower bound can never fabricate a barrier that hides a broker event.
-- 3. filled_quantity is monotonic and a FILLED mirror is exactly filled.

ALTER TABLE public.order_intents
    DROP CONSTRAINT order_intents_status_check;

ALTER TABLE public.order_intents
    ADD CONSTRAINT order_intents_status_check CHECK (status IN (
        'CREATED', 'RISK_APPROVED', 'OUTBOX_PENDING', 'SUBMITTING',
        'ACKNOWLEDGED', 'PARTIALLY_FILLED', 'FILLED', 'CANCEL_PENDING',
        'CANCELED', 'REJECTED', 'EXPIRED', 'UNKNOWN', 'REVIEW_REQUIRED'
    ));

CREATE OR REPLACE FUNCTION public.order_status_transition_is_valid(
    p_current TEXT,
    p_target TEXT
)
RETURNS BOOLEAN
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $$
    SELECT p_target = ANY (CASE p_current
        WHEN 'CREATED' THEN ARRAY['RISK_APPROVED', 'REJECTED', 'EXPIRED']
        WHEN 'RISK_APPROVED' THEN ARRAY['OUTBOX_PENDING', 'EXPIRED']
        WHEN 'OUTBOX_PENDING' THEN ARRAY['SUBMITTING', 'EXPIRED']
        WHEN 'SUBMITTING' THEN ARRAY[
            'ACKNOWLEDGED', 'PARTIALLY_FILLED', 'FILLED', 'REJECTED', 'EXPIRED',
            'UNKNOWN', 'CANCEL_PENDING', 'CANCELED', 'REVIEW_REQUIRED'
        ]
        WHEN 'ACKNOWLEDGED' THEN ARRAY[
            'PARTIALLY_FILLED', 'FILLED', 'CANCEL_PENDING', 'EXPIRED', 'REVIEW_REQUIRED'
        ]
        WHEN 'PARTIALLY_FILLED' THEN ARRAY[
            'PARTIALLY_FILLED', 'FILLED', 'CANCEL_PENDING', 'EXPIRED', 'REVIEW_REQUIRED'
        ]
        WHEN 'CANCEL_PENDING' THEN ARRAY[
            'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'EXPIRED', 'REVIEW_REQUIRED'
        ]
        WHEN 'UNKNOWN' THEN ARRAY[
            'ACKNOWLEDGED', 'PARTIALLY_FILLED', 'FILLED', 'CANCEL_PENDING',
            'CANCELED', 'REJECTED', 'EXPIRED', 'REVIEW_REQUIRED'
        ]
        ELSE ARRAY[]::TEXT[]
    END)
$$;

ALTER TABLE public.broker_orders
    DROP CONSTRAINT broker_orders_status_check;

ALTER TABLE public.broker_orders
    ADD CONSTRAINT broker_orders_status_check CHECK (status IN (
        'RECEIVED', 'ACCEPTED', 'ACCEPTED_FOR_BIDDING', 'PARTIALLY_FILLED',
        'FILLED', 'PENDING_CANCEL', 'CANCELED', 'EXPIRED', 'REJECTED',
        'DONE_FOR_DAY', 'REPLACED', 'PENDING_REPLACE', 'STOPPED',
        'SUSPENDED', 'CALCULATED'
    ));

CREATE OR REPLACE FUNCTION public.broker_order_status_transition_is_valid(
    p_current TEXT,
    p_target TEXT
)
RETURNS BOOLEAN
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $$
    SELECT p_target = ANY (CASE p_current
        WHEN 'RECEIVED' THEN ARRAY[
            'ACCEPTED', 'ACCEPTED_FOR_BIDDING', 'PARTIALLY_FILLED', 'FILLED',
            'PENDING_CANCEL', 'CANCELED', 'EXPIRED', 'REJECTED',
            'DONE_FOR_DAY', 'REPLACED', 'PENDING_REPLACE', 'STOPPED',
            'SUSPENDED', 'CALCULATED'
        ]
        WHEN 'ACCEPTED' THEN ARRAY[
            'PARTIALLY_FILLED', 'FILLED', 'PENDING_CANCEL', 'CANCELED',
            'EXPIRED', 'REJECTED',
            'DONE_FOR_DAY', 'REPLACED', 'PENDING_REPLACE', 'STOPPED',
            'SUSPENDED', 'CALCULATED'
        ]
        WHEN 'ACCEPTED_FOR_BIDDING' THEN ARRAY[
            'PARTIALLY_FILLED', 'FILLED', 'PENDING_CANCEL', 'CANCELED',
            'EXPIRED', 'REJECTED',
            'DONE_FOR_DAY', 'REPLACED', 'PENDING_REPLACE', 'STOPPED',
            'SUSPENDED', 'CALCULATED'
        ]
        WHEN 'PARTIALLY_FILLED' THEN ARRAY[
            'PARTIALLY_FILLED', 'FILLED', 'PENDING_CANCEL', 'CANCELED',
            'EXPIRED', 'REJECTED',
            'DONE_FOR_DAY', 'REPLACED', 'PENDING_REPLACE', 'STOPPED',
            'SUSPENDED', 'CALCULATED'
        ]
        WHEN 'PENDING_CANCEL' THEN ARRAY[
            'PENDING_CANCEL', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED',
            'EXPIRED', 'REJECTED',
            'DONE_FOR_DAY', 'REPLACED', 'PENDING_REPLACE', 'STOPPED',
            'SUSPENDED', 'CALCULATED'
        ]
        ELSE ARRAY[]::TEXT[]
    END)
$$;

CREATE OR REPLACE FUNCTION public.guard_broker_order_write()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF NEW.broker_order_id <> OLD.broker_order_id
        OR NEW.client_order_id <> OLD.client_order_id
        OR NEW.symbol <> OLD.symbol
        OR NEW.side <> OLD.side
        OR NEW.quantity <> OLD.quantity
        OR NEW.limit_price <> OLD.limit_price
        OR NEW.submitted_at <> OLD.submitted_at
    THEN
        RAISE EXCEPTION 'broker order identity fields are immutable'
            USING ERRCODE = '55000';
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status
        AND NOT public.broker_order_status_transition_is_valid(OLD.status, NEW.status)
    THEN
        RAISE EXCEPTION 'broker order status transition is not representable'
            USING ERRCODE = '55000';
    END IF;

    IF NEW.filled_quantity < OLD.filled_quantity THEN
        RAISE EXCEPTION 'filled_quantity must never move backwards'
            USING ERRCODE = '55000';
    END IF;

    IF NEW.status = 'FILLED' AND NEW.filled_quantity <> NEW.quantity THEN
        RAISE EXCEPTION 'a filled mirror must be exactly filled'
            USING ERRCODE = '55000';
    END IF;

    NEW.updated_at := pg_catalog.statement_timestamp();
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.guard_broker_order_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF NEW.status = 'FILLED' AND NEW.filled_quantity <> NEW.quantity THEN
        RAISE EXCEPTION 'a filled mirror must be exactly filled'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER broker_orders_guard_insert
BEFORE INSERT ON public.broker_orders
FOR EACH ROW
EXECUTE FUNCTION public.guard_broker_order_insert();

REVOKE ALL ON FUNCTION public.guard_broker_order_insert() FROM PUBLIC;

-- The 0006 backfill watermark is suspect (local clock above broker truth):
-- clear it to NULL (unknown).  The domain maps NULL onto the submitted_at
-- lower bound, which can never hide a real broker event.
ALTER TABLE public.broker_orders
    ALTER COLUMN broker_updated_at DROP NOT NULL;

UPDATE public.broker_orders
SET broker_updated_at = NULL
WHERE broker_updated_at IS NOT NULL;

CREATE OR REPLACE FUNCTION public.guard_broker_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF OLD.broker_updated_at IS NOT NULL
        AND NEW.broker_updated_at IS NOT NULL
        AND NEW.broker_updated_at < OLD.broker_updated_at
    THEN
        RAISE EXCEPTION 'broker_updated_at must never move backwards'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON COLUMN public.broker_orders.broker_updated_at IS
    'Broker-observed watermark; NULL means unknown (0006 backfill was suspect) '
    'and the domain then uses the submitted_at lower bound.';

ALTER TABLE public.control_state
    ADD COLUMN flatten_generation BIGINT NOT NULL DEFAULT 0;

COMMENT ON COLUMN public.control_state.flatten_generation IS
    'Durable counter incremented inside every flatten transaction; the value becomes '
    'the target_version of the generated SELL intents so a repeated flatten can never '
    'collide with an earlier flatten''s client order ids.';

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

CREATE TABLE public.reconciliation_mismatches (
    run_id UUID NOT NULL REFERENCES public.reconciliation_runs (run_id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
    kind TEXT NOT NULL CHECK (kind IN (
        'NON_PAPER_ACCOUNT', 'UNKNOWN_BROKER_ORDER', 'MISSING_BROKER_ORDER',
        'PARAMETER_MISMATCH', 'STATUS_MISMATCH', 'INTENT_STATUS_MISMATCH',
        'BROKER_QUERY_FAILURE',
        'MISSING_LOCAL_FILL', 'POSITION_QUANTITY_MISMATCH',
        'POSITION_SYMBOL_MISMATCH'
    )),
    detail TEXT NOT NULL CHECK (length(btrim(detail)) > 0 AND length(detail) <= 200),
    PRIMARY KEY (run_id, ordinal)
);

CREATE TRIGGER reconciliation_mismatches_append_only
BEFORE UPDATE OR DELETE ON public.reconciliation_mismatches
FOR EACH ROW
EXECUTE FUNCTION public.prevent_append_only_mutation();

REVOKE UPDATE, DELETE ON TABLE public.reconciliation_mismatches FROM PUBLIC;

COMMENT ON TABLE public.reconciliation_mismatches IS
    'Per-mismatch audit detail for every reconciliation run, preserved verbatim '
    'with a stable ordinal so evidence can name exactly which broker order or '
    'symbol disagreed.';
