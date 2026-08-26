-- 0015 down: restore the 0007 order transition map.

-- 0007 removes REVIEW_REQUIRED from the status check.  Down migrations are
-- restore-drill operations, so preserve the unresolved safety gate as UNKNOWN
-- before that older schema is restored rather than leaving an invalid row.
ALTER TABLE public.order_intents DISABLE TRIGGER order_intents_guard_write;
UPDATE public.order_intents
SET status = 'UNKNOWN'
WHERE status = 'REVIEW_REQUIRED';
ALTER TABLE public.order_intents ENABLE TRIGGER order_intents_guard_write;

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

DELETE FROM public.schema_migrations WHERE version = 15;
