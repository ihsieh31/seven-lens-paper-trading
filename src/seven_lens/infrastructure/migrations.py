"""Small, checksummed PostgreSQL migration runner for the authoritative schema."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Final, cast

import psycopg

_UP_SUFFIX: Final = "_up.sql"
_DOWN_SUFFIX: Final = "_down.sql"


class MigrationError(RuntimeError):
    """Raised when a migration cannot be safely applied or reversed."""


class MigrationIntegrityError(MigrationError):
    """Raised when an applied migration no longer matches its recorded checksum."""


@dataclass(frozen=True, slots=True)
class Migration:
    """One immutable pair of up/down SQL files."""

    version: int
    filename: str
    up_sql: str
    down_sql: str

    @property
    def checksum(self) -> str:
        """Return an unambiguous SHA-256 checksum of the exact up and down SQL bytes."""
        digest = sha256()
        digest.update(b"seven-lens-migration-v1\\0")
        for contents in (self.up_sql, self.down_sql):
            encoded = contents.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, byteorder="big"))
            digest.update(encoded)
        return digest.hexdigest()


def migrate(dsn: str) -> int:
    """Apply every pending migration in order, checking already-applied checksums."""
    migrations = _load_migrations()
    expected_version = migrations[-1].version if migrations else 0
    with psycopg.connect(dsn, autocommit=False) as connection:
        with connection.cursor() as cursor:
            if _migration_table_exists(cursor) and _current_version(cursor) > expected_version:
                raise MigrationIntegrityError(
                    "database has migrations unknown to this program version"
                )
            for migration in migrations:
                if _migration_table_exists(cursor):
                    applied_checksum = _applied_checksum(cursor, migration.version)
                    if applied_checksum is not None:
                        if applied_checksum != migration.checksum:
                            raise MigrationIntegrityError(
                                "migration "
                                f"{migration.filename} checksum does not match database history"
                            )
                        continue
                cursor.execute(migration.up_sql)
                cursor.execute(
                    """
                    INSERT INTO schema_migrations (version, filename, checksum)
                    VALUES (%s, %s, %s)
                    """,
                    (migration.version, migration.filename, migration.checksum),
                )
        connection.commit()
    return migrations[-1].version if migrations else 0


def rollback(dsn: str) -> int:
    """Reverse the latest migration; callers must use a restored/disposable database."""
    migrations = _load_migrations()
    with psycopg.connect(dsn, autocommit=False) as connection:
        with connection.cursor() as cursor:
            if not _migration_table_exists(cursor):
                return 0
            version = _current_version(cursor)
            if version == 0:
                return 0
            migration = next((item for item in migrations if item.version == version), None)
            if migration is None:
                raise MigrationError(f"database version {version} has no local rollback migration")
            applied_checksum = _applied_checksum(cursor, migration.version)
            if applied_checksum != migration.checksum:
                raise MigrationIntegrityError(
                    f"migration {migration.filename} checksum does not match database history"
                )
            cursor.execute(migration.down_sql)
        connection.commit()
    return version - 1


def current_version(dsn: str) -> int:
    """Return the latest locally recorded migration version, or zero for an empty database."""
    with psycopg.connect(dsn, autocommit=True) as connection, connection.cursor() as cursor:
        if not _migration_table_exists(cursor):
            return 0
        return _current_version(cursor)


def verify_schema(dsn: str) -> int:
    """Fail closed unless every local migration is applied with the expected checksum."""
    migrations = _load_migrations()
    expected_version = migrations[-1].version if migrations else 0
    with psycopg.connect(dsn, autocommit=True) as connection, connection.cursor() as cursor:
        if not _migration_table_exists(cursor):
            raise MigrationError("schema_migrations does not exist; run migrate() first")
        if _current_version(cursor) != expected_version:
            raise MigrationIntegrityError(
                "database migration version does not match this program version"
            )
        for migration in migrations:
            applied_checksum = _applied_checksum(cursor, migration.version)
            if applied_checksum != migration.checksum:
                raise MigrationIntegrityError(
                    f"migration {migration.filename} is missing or its checksum differs"
                )
        cursor.execute(
            """
            SELECT to_regclass('public.schema_metadata'),
                   to_regclass('public.domain_events'),
                   to_regclass('public.audit_events'),
                   to_regclass('public.job_instances'),
                   to_regclass('public.job_leases')
            """
        )
        tables = cast(tuple[object, ...] | None, cursor.fetchone())
        if tables is None or any(table is None for table in tables):
            raise MigrationError("authoritative-state schema is incomplete")
    return migrations[-1].version if migrations else 0


def _load_migrations() -> tuple[Migration, ...]:
    """Load paired SQL migrations bundled beside the source tree."""
    migration_directory = _migration_directory()
    up_paths = sorted(migration_directory.glob(f"*{_UP_SUFFIX}"))
    migrations: list[Migration] = []
    for up_path in up_paths:
        stem = up_path.name.removesuffix(_UP_SUFFIX)
        version_text, separator, description = stem.partition("_")
        if (
            separator != "_"
            or not description
            or len(version_text) != 4
            or not version_text.isdecimal()
            or int(version_text) < 1
        ):
            raise MigrationError(f"invalid migration filename: {up_path.name}")
        down_path = up_path.with_name(f"{stem}{_DOWN_SUFFIX}")
        if not down_path.is_file():
            raise MigrationError(f"missing down migration for {up_path.name}")
        migrations.append(
            Migration(
                version=int(version_text),
                filename=up_path.name,
                up_sql=up_path.read_text(encoding="utf-8"),
                down_sql=down_path.read_text(encoding="utf-8"),
            )
        )
    versions = [migration.version for migration in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise MigrationError("migration versions must start at 0001 and be contiguous")
    return tuple(migrations)


def _migration_directory() -> Path:
    """Locate repository migrations for editable-source execution and installed packages."""
    source_directory = Path(__file__).resolve().parents[3] / "migrations"
    if source_directory.is_dir():
        return source_directory
    installed_directory = Path(__file__).with_name("sql_migrations")
    if installed_directory.is_dir():
        return installed_directory
    raise MigrationError("could not locate bundled SQL migrations")


def _migration_table_exists(cursor: psycopg.Cursor[object]) -> bool:
    cursor.execute("SELECT to_regclass('public.schema_migrations')")
    row = cast(tuple[object, ...] | None, cursor.fetchone())
    return row is not None and row[0] is not None


def _applied_checksum(cursor: psycopg.Cursor[object], version: int) -> str | None:
    cursor.execute("SELECT checksum FROM schema_migrations WHERE version = %s", (version,))
    row = cast(tuple[object, ...] | None, cursor.fetchone())
    return None if row is None else str(row[0])


def _current_version(cursor: psycopg.Cursor[object]) -> int:
    cursor.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")
    row = cast(tuple[object, ...] | None, cursor.fetchone())
    if row is None:
        raise MigrationError("could not read schema_migrations")
    version = row[0]
    if type(version) is not int:
        raise MigrationError("schema_migrations returned an invalid version")
    return version
