-- P2-A execution order state.  Extends the authoritative schema with order
-- intents, broker order mirrors, and append-only fills.  Migrations 0001 and
-- 0002 remain immutable.

CREATE TABLE public.order_intents (
    intent_id UUID PRIMARY KEY,
    client_order_id TEXT NOT NULL UNIQUE
        CHECK (length(btrim(client_order_id)) > 0 AND length(client_order_id) <= 128),
    strategy TEXT NOT NULL CHECK (strategy ~ '^[a-z0-9][a-z0-9_-]{0,31}$'),
    trading_date DATE NOT NULL,
    window_name TEXT NOT NULL CHECK (window_name ~ '^[a-z0-9][a-z0-9_-]{0,63}$'),
    target_version BIGINT NOT NULL CHECK (target_version >= 1),
    symbol TEXT NOT NULL CHECK (symbol ~ '^[A-Z][A-Z0-9.\-]{0,9}$'),
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity BIGINT NOT NULL CHECK (quantity >= 1),
    intent_type TEXT NOT NULL CHECK (intent_type IN ('REBALANCE', 'RISK_EXIT')),
    limit_price NUMERIC(12,2) NOT NULL CHECK (limit_price > 0),
    collar_reference_price NUMERIC(12,2) NOT NULL CHECK (collar_reference_price > 0),
    collar_offset_bps INTEGER NOT NULL CHECK (collar_offset_bps BETWEEN 1 AND 500),
    earliest_submit_at TIMESTAMPTZ NOT NULL,
    cancel_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'CREATED', 'RISK_APPROVED', 'OUTBOX_PENDING', 'SUBMITTING',
        'ACKNOWLEDGED', 'PARTIALLY_FILLED', 'FILLED', 'CANCEL_PENDING',
        'CANCELED', 'REJECTED', 'EXPIRED', 'UNKNOWN'
    )),
    run_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CHECK (cancel_at > earliest_submit_at),
    CHECK (
        client_order_id = 'slv1-' || strategy
            || '-' || to_char(trading_date, 'YYYY-MM-DD')
            || '-' || window_name
            || '-t' || target_version::TEXT
            || '-' || symbol
            || '-' || lower(side)
    )
);

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

CREATE OR REPLACE FUNCTION public.guard_order_intent_write()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF NEW.intent_id <> OLD.intent_id
        OR NEW.client_order_id <> OLD.client_order_id
        OR NEW.strategy <> OLD.strategy
        OR NEW.trading_date <> OLD.trading_date
        OR NEW.window_name <> OLD.window_name
        OR NEW.target_version <> OLD.target_version
        OR NEW.symbol <> OLD.symbol
        OR NEW.side <> OLD.side
        OR NEW.quantity <> OLD.quantity
        OR NEW.intent_type <> OLD.intent_type
        OR NEW.limit_price <> OLD.limit_price
        OR NEW.collar_reference_price <> OLD.collar_reference_price
        OR NEW.collar_offset_bps <> OLD.collar_offset_bps
        OR NEW.earliest_submit_at <> OLD.earliest_submit_at
        OR NEW.cancel_at <> OLD.cancel_at
        OR NEW.run_id <> OLD.run_id
        OR NEW.created_at <> OLD.created_at
    THEN
        RAISE EXCEPTION 'order intent identity fields are immutable'
            USING ERRCODE = '55000';
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status
        AND NOT public.order_status_transition_is_valid(OLD.status, NEW.status)
    THEN
        RAISE EXCEPTION 'order intent status transition is not permitted'
            USING ERRCODE = '55000';
    END IF;

    NEW.updated_at := pg_catalog.statement_timestamp();
    RETURN NEW;
END;
$$;

CREATE TRIGGER order_intents_guard_write
BEFORE UPDATE ON public.order_intents
FOR EACH ROW
EXECUTE FUNCTION public.guard_order_intent_write();

CREATE TABLE public.broker_orders (
    broker_order_id TEXT PRIMARY KEY
        CHECK (length(btrim(broker_order_id)) > 0 AND length(broker_order_id) <= 100),
    client_order_id TEXT NOT NULL UNIQUE REFERENCES public.order_intents (client_order_id),
    symbol TEXT NOT NULL CHECK (symbol ~ '^[A-Z][A-Z0-9.\-]{0,9}$'),
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity BIGINT NOT NULL CHECK (quantity >= 1),
    filled_quantity BIGINT NOT NULL DEFAULT 0 CHECK (
        filled_quantity >= 0 AND filled_quantity <= quantity
    ),
    limit_price NUMERIC(12,2) NOT NULL CHECK (limit_price > 0),
    status TEXT NOT NULL CHECK (status IN (
        'RECEIVED', 'ACCEPTED', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'EXPIRED', 'REJECTED'
    )),
    submitted_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CHECK (client_order_id LIKE 'slv1-%')
);

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

CREATE TRIGGER broker_orders_guard_write
BEFORE UPDATE ON public.broker_orders
FOR EACH ROW
EXECUTE FUNCTION public.guard_broker_order_write();

CREATE TABLE public.fills (
    fill_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    execution_id TEXT NOT NULL UNIQUE
        CHECK (length(btrim(execution_id)) > 0 AND length(execution_id) <= 100),
    broker_order_id TEXT NOT NULL REFERENCES public.broker_orders (broker_order_id),
    quantity BIGINT NOT NULL CHECK (quantity >= 1),
    price NUMERIC(12,2) NOT NULL CHECK (price > 0),
    occurred_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    UNIQUE (broker_order_id, execution_id)
);

CREATE TRIGGER fills_append_only
BEFORE UPDATE OR DELETE ON public.fills
FOR EACH ROW
EXECUTE FUNCTION public.prevent_append_only_mutation();

CREATE INDEX order_intents_status_idx ON public.order_intents (status, trading_date);
CREATE INDEX broker_orders_status_idx ON public.broker_orders (status);
CREATE INDEX fills_broker_order_idx ON public.fills (broker_order_id, fill_id);

REVOKE UPDATE, DELETE ON TABLE public.order_intents, public.broker_orders FROM PUBLIC;
REVOKE INSERT, UPDATE, DELETE ON TABLE public.fills FROM PUBLIC;
REVOKE ALL ON FUNCTION public.order_status_transition_is_valid(TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.broker_order_status_transition_is_valid(TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.guard_order_intent_write() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.guard_broker_order_write() FROM PUBLIC;

COMMENT ON TABLE public.order_intents IS
    'Deterministic order intents; identity immutable, status transitions validated against the closed map.';
COMMENT ON TABLE public.broker_orders IS
    'Local mirror of broker-side Paper orders; never authoritative for research decisions.';
COMMENT ON TABLE public.fills IS
    'Append-only fill ledger; UPDATE and DELETE are forbidden.';
