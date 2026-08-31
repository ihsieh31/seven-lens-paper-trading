-- 0024 down: remove P4-C market snapshots, universe snapshots, feature vectors, and
-- candidate sets.
--
-- The down migration is irreversible beyond the migration version rollback.  All
-- append-only data is dropped.

DROP TRIGGER IF EXISTS market_snapshots_guard_write ON public.market_snapshots;
DROP TRIGGER IF EXISTS market_snapshots_guard_truncate ON public.market_snapshots;
DROP TRIGGER IF EXISTS universe_snapshots_guard_write ON public.universe_snapshots;
DROP TRIGGER IF EXISTS universe_snapshots_guard_truncate ON public.universe_snapshots;
DROP TRIGGER IF EXISTS universe_snapshot_entries_guard_write ON public.universe_snapshot_entries;
DROP TRIGGER IF EXISTS universe_snapshot_entries_guard_truncate ON public.universe_snapshot_entries;
DROP TRIGGER IF EXISTS feature_vectors_guard_write ON public.feature_vectors;
DROP TRIGGER IF EXISTS feature_vectors_guard_truncate ON public.feature_vectors;
DROP TRIGGER IF EXISTS sector_assignments_guard_write ON public.sector_assignments;
DROP TRIGGER IF EXISTS sector_assignments_guard_truncate ON public.sector_assignments;
DROP TRIGGER IF EXISTS candidate_sets_guard_write ON public.candidate_sets;
DROP TRIGGER IF EXISTS candidate_sets_guard_truncate ON public.candidate_sets;
DROP TRIGGER IF EXISTS candidate_set_entries_guard_write ON public.candidate_set_entries;
DROP TRIGGER IF EXISTS candidate_set_entries_guard_truncate ON public.candidate_set_entries;
DROP TRIGGER IF EXISTS cluster_results_guard_write ON public.cluster_results;
DROP TRIGGER IF EXISTS cluster_results_guard_truncate ON public.cluster_results;

DROP FUNCTION IF EXISTS public.append_market_snapshot(TEXT, JSONB);
DROP FUNCTION IF EXISTS public.append_universe_snapshot(TEXT, JSONB);
DROP FUNCTION IF EXISTS public.append_feature_vector(TEXT, JSONB);
DROP FUNCTION IF EXISTS public.append_sector_assignment(TEXT, JSONB);
DROP FUNCTION IF EXISTS public.append_candidate_set(TEXT, JSONB);
DROP FUNCTION IF EXISTS public.append_cluster_result(TEXT, JSONB);

DROP TABLE IF EXISTS public.cluster_results;
DROP TABLE IF EXISTS public.candidate_set_entries;
DROP TABLE IF EXISTS public.candidate_sets;
DROP TABLE IF EXISTS public.feature_vectors;
DROP TABLE IF EXISTS public.sector_assignments;
DROP TABLE IF EXISTS public.universe_snapshot_entries;
DROP TABLE IF EXISTS public.universe_snapshots;
DROP TABLE IF EXISTS public.market_snapshots;

DELETE FROM public.schema_migrations WHERE version = 24;
