-- 0015: allow an unrepresentable trade update to enter the durable review gate.
--
-- REVIEW_REQUIRED is an operationally unresolved state.  A conflict can be
-- discovered after the local intent reached a broker-terminal state, so the
-- database transition map must permit the same fail-closed escape hatch as
-- the domain map.  The original terminal history is retained for review.

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
        WHEN 'FILLED' THEN ARRAY['REVIEW_REQUIRED']
        WHEN 'CANCELED' THEN ARRAY['REVIEW_REQUIRED']
        WHEN 'REJECTED' THEN ARRAY['REVIEW_REQUIRED']
        WHEN 'EXPIRED' THEN ARRAY['REVIEW_REQUIRED']
        WHEN 'UNKNOWN' THEN ARRAY[
            'ACKNOWLEDGED', 'PARTIALLY_FILLED', 'FILLED', 'CANCEL_PENDING',
            'CANCELED', 'REJECTED', 'EXPIRED', 'REVIEW_REQUIRED'
        ]
        ELSE ARRAY[]::TEXT[]
    END)
$$;
