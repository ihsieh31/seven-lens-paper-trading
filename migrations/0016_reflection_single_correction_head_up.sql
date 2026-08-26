-- 0016: make reflection correction lineage a single-head graph in PostgreSQL.
--
-- Do not silently choose or delete a branch created by an older schema.  The
-- upgrade must stop and require an explicit operator repair before the unique
-- authority constraint can be installed.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.reflection_corrections
        GROUP BY superseded_reflection_id
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION
            'cannot enforce single correction head: existing branched reflection lineage'
            USING ERRCODE = '23514';
    END IF;
END
$$;

ALTER TABLE public.reflection_corrections
    ADD CONSTRAINT reflection_corrections_single_head_key
    UNIQUE (superseded_reflection_id);
