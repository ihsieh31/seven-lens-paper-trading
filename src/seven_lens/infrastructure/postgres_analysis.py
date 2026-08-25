"""PostgreSQL authority adapters for P3-B evidence and P3-C stage state."""

from __future__ import annotations

import psycopg

from seven_lens.application.ports.analysis import (
    LEGAL_TRANSITIONS,
    AnalysisStage,
    StoredStageResult,
)
from seven_lens.infrastructure.content_store import ContentStoreError, FileContentStore
from seven_lens.sources.contracts import EvidencePacket, SourceRecord


class PostgresAnalysisError(RuntimeError):
    pass


class PostgresEvidenceRepository:
    def __init__(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        *,
        content_store: FileContentStore | None = None,
    ) -> None:
        if content_store is not None and type(content_store) is not FileContentStore:
            raise ValueError("content_store requires the trusted FileContentStore capability")
        self._connection = connection
        self._content_store = content_store

    def register_staged_object(self, content_hash: str, byte_size: int) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.register_source_object(%s, %s)", (content_hash, byte_size)
            )

    def publish_object(self, content_hash: str) -> None:
        if self._content_store is None:
            raise PostgresAnalysisError("content store capability is required for publication")
        try:
            content = self._content_store.get(content_hash)
        except ContentStoreError as error:
            raise PostgresAnalysisError(
                "content bytes must be verified before publication"
            ) from error
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT byte_size FROM public.source_objects WHERE content_hash = %s FOR UPDATE",
                (content_hash,),
            )
            row = cursor.fetchone()
            if row is None or row != (len(content),):
                raise PostgresAnalysisError("staged content size does not match verified bytes")
            cursor.execute("SELECT public.publish_source_object(%s)", (content_hash,))

    def add_packet(self, packet: EvidencePacket) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT public.register_evidence_packet(%s, %s, %s, %s, %s, %s)
                """,
                (
                    packet.packet_id.value,
                    packet.packet_hash,
                    packet.as_of.value,
                    packet.universe_hash,
                    packet.portfolio_snapshot_hash,
                    packet.producer_version,
                ),
            )

    def add_source_record(self, source: SourceRecord) -> None:
        if source.available_at is None:
            raise PostgresAnalysisError("source availability is unverified")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT public.register_source_record(
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    source.source_id,
                    source.canonical_url,
                    source.publisher,
                    source.source_family.value,
                    source.source_kind.value,
                    source.available_at.value,
                    source.content_hash,
                    source.primary_source,
                    source.tombstone,
                ),
            )


class PostgresAnalysisStateRepository:
    def __init__(self, connection: psycopg.Connection[tuple[object, ...]]) -> None:
        self._connection = connection

    def create_run(self, run_id: str, input_id: str, packet_hash: str, snapshot_hash: str) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.create_analysis_run(%s::uuid, %s::uuid, %s, %s)",
                (run_id, input_id, packet_hash, snapshot_hash),
            )

    def current_stage(self, run_id: str) -> AnalysisStage:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_stage FROM public.analysis_runs WHERE run_id = %s", (run_id,)
            )
            row = cursor.fetchone()
        if row is None or type(row[0]) is not str:
            raise PostgresAnalysisError("analysis run is unavailable")
        try:
            return AnalysisStage(row[0])
        except ValueError as error:
            raise PostgresAnalysisError("analysis run has an unknown stage") from error

    def run_identity(self, run_id: str) -> tuple[str, str, str]:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT input_id::text, packet_hash, snapshot_hash "
                "FROM public.analysis_runs WHERE run_id = %s",
                (run_id,),
            )
            row = cursor.fetchone()
        if row is None or any(type(value) is not str for value in row):
            raise PostgresAnalysisError("analysis run identity is unavailable")
        return str(row[0]), str(row[1]), str(row[2])

    def load(self, run_id: str, stage: AnalysisStage) -> StoredStageResult | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT result_hash, payload FROM public.analysis_stage_results
                WHERE run_id = %s AND stage = %s
                """,
                (run_id, stage.value),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        if type(row[0]) is not str or type(row[1]) is not str:
            raise PostgresAnalysisError("analysis result is malformed")
        return StoredStageResult(run_id, stage, row[0], row[1])

    def advance(self, result: StoredStageResult, expected_current: AnalysisStage) -> bool:
        if (expected_current, result.stage) not in LEGAL_TRANSITIONS:
            raise PostgresAnalysisError("analysis stage transition is not legal")
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT public.advance_analysis_stage(%s::uuid, %s, %s, %s, %s)
                """,
                (
                    result.run_id,
                    expected_current.value,
                    result.stage.value,
                    result.result_hash,
                    result.payload,
                ),
            )
            row = cursor.fetchone()
        if row is None or type(row[0]) is not bool:
            raise PostgresAnalysisError("analysis stage transition returned malformed authority")
        return row[0]
