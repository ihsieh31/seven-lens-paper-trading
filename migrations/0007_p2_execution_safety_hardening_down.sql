-- 0007 down migration: revert the P2 second remediation hardening.
-- Applied only against a disposable/restore-drill database.

DROP TRIGGER broker_orders_guard_insert ON public.broker_orders;

DROP FUNCTION public.guard_broker_order_insert();

UPDATE public.broker_orders
SET broker_updated_at = updated_at
WHERE broker_updated_at IS NULL;

ALTER TABLE public.broker_orders
    ALTER COLUMN broker_updated_at SET NOT NULL;

CREATE OR REPLACE FUNCTION public.guard_broker_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF NEW.broker_updated_at < OLD.broker_updated_at THEN
        RAISE EXCEPTION 'broker_updated_at must never move backwards'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
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

    NEW.updated_at := pg_catalog.statement_timestamp();
    RETURN NEW;
END;
$$;

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
            'ACCEPTED', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'EXPIRED', 'REJECTED'
        ]
        WHEN 'ACCEPTED' THEN ARRAY['PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'EXPIRED']
        WHEN 'PARTIALLY_FILLED' THEN ARRAY[
            'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'EXPIRED'
        ]
        ELSE ARRAY[]::TEXT[]
    END)
$$;

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
            'ACKNOWLEDGED', 'PARTIALLY_FILLED', 'FILLED', 'REJECTED', 'EXPIRED', 'UNKNOWN'
        ]
        WHEN 'ACKNOWLEDGED' THEN ARRAY[
            'PARTIALLY_FILLED', 'FILLED', 'CANCEL_PENDING', 'EXPIRED'
        ]
        WHEN 'PARTIALLY_FILLED' THEN ARRAY[
            'PARTIALLY_FILLED', 'FILLED', 'CANCEL_PENDING', 'EXPIRED'
        ]
        WHEN 'CANCEL_PENDING' THEN ARRAY[
            'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'EXPIRED'
        ]
        WHEN 'UNKNOWN' THEN ARRAY[
            'ACKNOWLEDGED', 'PARTIALLY_FILLED', 'FILLED', 'CANCEL_PENDING',
            'CANCELED', 'REJECTED', 'EXPIRED'
        ]
        ELSE ARRAY[]::TEXT[]
    END)
$$;

ALTER TABLE public.broker_orders
    DROP CONSTRAINT broker_orders_status_check;

ALTER TABLE public.broker_orders
    ADD CONSTRAINT broker_orders_status_check CHECK (status IN (
        'RECEIVED', 'ACCEPTED', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'EXPIRED', 'REJECTED'
    ));

ALTER TABLE public.order_intents
    DROP CONSTRAINT order_intents_status_check;

ALTER TABLE public.order_intents
    ADD CONSTRAINT order_intents_status_check CHECK (status IN (
        'CREATED', 'RISK_APPROVED', 'OUTBOX_PENDING', 'SUBMITTING',
        'ACKNOWLEDGED', 'PARTIALLY_FILLED', 'FILLED', 'CANCEL_PENDING',
        'CANCELED', 'REJECTED', 'EXPIRED', 'UNKNOWN'
    ));

DELETE FROM public.schema_migrations WHERE version = 7;

ALTER TABLE public.control_state
    DROP COLUMN IF EXISTS flatten_generation;

DROP TABLE IF EXISTS public.reconciliation_mismatches;

ALTER TABLE public.reconciliation_runs
    DROP CONSTRAINT IF EXISTS reconciliation_runs_mismatch_kinds_check;

ALTER TABLE public.reconciliation_runs
    ADD CONSTRAINT reconciliation_runs_mismatch_kinds_check CHECK (
        mismatch_kinds <@ ARRAY[
            'NON_PAPER_ACCOUNT', 'UNKNOWN_BROKER_ORDER', 'MISSING_BROKER_ORDER',
            'PARAMETER_MISMATCH', 'STATUS_MISMATCH', 'BROKER_QUERY_FAILURE',
            'MISSING_LOCAL_FILL',
            'POSITION_QUANTITY_MISMATCH', 'POSITION_SYMBOL_MISMATCH'
        ]::TEXT[]
    );
