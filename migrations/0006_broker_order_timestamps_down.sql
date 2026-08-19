DROP TRIGGER broker_orders_guard_broker_updated_at ON public.broker_orders;

DROP FUNCTION public.guard_broker_updated_at();

ALTER TABLE public.broker_orders
    DROP COLUMN broker_updated_at;

DELETE FROM public.schema_migrations WHERE version = 6;
