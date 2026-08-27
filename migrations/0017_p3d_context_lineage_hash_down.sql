-- 0017 down: restore the pre-0017 context identity function on a disposable DB.

DO $migration$
DECLARE
    v_definition TEXT;
    v_old TEXT := $old$COALESCE(p_superseded_proposal_id::text, ''),
        COALESCE(p_superseded_proposal_hash, '')$old$;
    v_new TEXT := $new$COALESCE(p_superseded_proposal_id::text, '')$new$;
BEGIN
    SELECT pg_catalog.pg_get_functiondef(
        'public.register_proposal_context(
            uuid, uuid, integer, text, uuid, uuid, text, uuid, text, text, text
        )'::regprocedure
    )
    INTO v_definition;
    IF v_definition IS NULL
       OR (
           length(v_definition) - length(pg_catalog.replace(v_definition, v_old, ''))
       ) <> length(v_old) THEN
        RAISE EXCEPTION
            'P3-D context authority function is not at the expected 0017 definition'
            USING ERRCODE = '23514';
    END IF;
    EXECUTE pg_catalog.replace(v_definition, v_old, v_new);
    DELETE FROM public.schema_migrations WHERE version = 17;
END;
$migration$;
