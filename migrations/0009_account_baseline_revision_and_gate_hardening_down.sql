-- 0009 down migration: revert baseline revision authority.
-- Applied only against a disposable/restore-drill database.

DROP TRIGGER IF EXISTS account_baseline_revisions_guard_write ON public.account_baseline_revisions;
DROP FUNCTION IF EXISTS public.guard_account_baseline_revision_write();
DROP TABLE IF EXISTS public.account_baseline_revisions;

DROP TRIGGER IF EXISTS account_baselines_guard_write ON public.account_baselines;
DROP FUNCTION IF EXISTS public.guard_account_baseline_write();

CREATE OR REPLACE FUNCTION public.guard_account_baseline_write()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.account_id <> OLD.account_id THEN
            RAISE EXCEPTION 'account baseline account_id is immutable'
                USING ERRCODE = '55000';
        END IF;
    END IF;
    NEW.updated_at := pg_catalog.statement_timestamp();
    RETURN NEW;
END;
$$;

CREATE TRIGGER account_baselines_guard_write
BEFORE UPDATE ON public.account_baselines
FOR EACH ROW
EXECUTE FUNCTION public.guard_account_baseline_write();

REVOKE ALL ON FUNCTION public.guard_account_baseline_write() FROM PUBLIC;

DELETE FROM public.schema_migrations WHERE version = 9;
