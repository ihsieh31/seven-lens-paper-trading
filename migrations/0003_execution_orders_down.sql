-- P2-A down migration: remove execution order state added by 0003.
-- Applied only against a disposable/restore-drill database.

DROP TABLE IF EXISTS public.fills;
DROP TABLE IF EXISTS public.broker_orders;
DROP TABLE IF EXISTS public.order_intents;

DROP FUNCTION IF EXISTS public.guard_broker_order_write();
DROP FUNCTION IF EXISTS public.broker_order_status_transition_is_valid(TEXT, TEXT);
DROP FUNCTION IF EXISTS public.guard_order_intent_write();
DROP FUNCTION IF EXISTS public.order_status_transition_is_valid(TEXT, TEXT);

DELETE FROM public.schema_migrations WHERE version = 3;
