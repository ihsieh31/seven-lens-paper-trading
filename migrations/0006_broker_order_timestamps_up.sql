-- 0006: split the broker-orders clock into broker time and local record time.
--
-- Defect E: the local mirror stored only statement_timestamp() in updated_at,
-- while the domain BrokerOrder.updated_at means the broker's own timestamp.
-- With the local clock behind the broker, a real broker event looked STALE to
-- the trade-update consumer and was silently dropped.
--
-- This migration adds broker_updated_at (the broker-observed timestamp, guarded
-- to never move backwards) and keeps updated_at as the DB-assigned local record
-- clock for auditing.

ALTER TABLE public.broker_orders
    ADD COLUMN broker_updated_at TIMESTAMPTZ;

-- Best-effort backfill: orders recorded before this migration have no broker
-- timestamp; their local record time is the only available approximation.
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

CREATE TRIGGER broker_orders_guard_broker_updated_at
BEFORE UPDATE OF broker_updated_at ON public.broker_orders
FOR EACH ROW
EXECUTE FUNCTION public.guard_broker_updated_at();
