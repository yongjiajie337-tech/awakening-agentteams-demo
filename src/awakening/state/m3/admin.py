"""Narrow M3 migration/consumer-role provisioning.

This module never runs the M2 provisioner.  A new M3 test database is cloned
from the accepted main business database and receives only M3-002.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from psycopg import sql

from awakening.state.admin import _connect_bootstrap, _load_bootstrap_env, _read_env_file


CONSUMER_ROLE: Final = "awakening_m3_consumer"
OWNER_ROLE: Final = "awakening_m2_owner"
STATE_SERVICE_ROLE: Final = "awakening_state_service"
MAIN_DATABASE: Final = "awakening_m2"
TEST_DATABASE: Final = "awakening_m3_test"
MIGRATION_VERSION: Final = "M3-002"
MIGRATION_FILENAME: Final = "002_evidence_ingestion.sql"
CONSUMER_PASSWORD_FIELD: Final = "AWAKENING_M3_CONSUMER_DB_PASSWORD"

REQUIRED_M3_BUSINESS_TABLES: Final = frozenset(
    {
        "evidence_ingest_jobs",
        "evidence_ingestion_receipts",
        "evidence_items",
    }
)


class M3AdminError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def load_m3_env(path: str | Path) -> dict[str, str]:
    values = _read_env_file(path)
    password = values.get(CONSUMER_PASSWORD_FIELD, "")
    if not password:
        raise M3AdminError(f"M3_ENV_REQUIRED:{CONSUMER_PASSWORD_FIELD}")
    return {CONSUMER_PASSWORD_FIELD: password}


def _load_migration(path: str | Path) -> str:
    migration_path = Path(path)
    if (
        migration_path.name != MIGRATION_FILENAME
        or not migration_path.is_file()
        or migration_path.is_symlink()
    ):
        raise M3AdminError("M3_MIGRATION_FILE_INVALID")
    try:
        migration = migration_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise M3AdminError("M3_MIGRATION_FILE_UNREADABLE") from exc
    if MIGRATION_VERSION not in migration or "business.evidence_ingest_jobs" not in migration:
        raise M3AdminError("M3_MIGRATION_CONTRACT_INVALID")
    return migration


def _ensure_consumer_role(connection: Any, password: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (CONSUMER_ROLE,))
        if cursor.fetchone() is None:
            cursor.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(CONSUMER_ROLE), sql.Literal(password))
            )
        else:
            cursor.execute(
                sql.SQL(
                    "ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(CONSUMER_ROLE), sql.Literal(password))
            )


def _ensure_consumer_database_access(connection: Any, database: str) -> None:
    if database not in {MAIN_DATABASE, TEST_DATABASE}:
        raise M3AdminError("M3_DATABASE_NAME_NOT_ALLOWED")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT datallowconn FROM pg_database WHERE datname = %s",
            (database,),
        )
        row = cursor.fetchone()
        if row is None or not row["datallowconn"]:
            raise M3AdminError(f"M3_DATABASE_IDENTITY_INVALID:{database}")
        cursor.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database),
                sql.Identifier(CONSUMER_ROLE),
            )
        )
        cursor.execute(
            "SELECT has_database_privilege(%s, %s, 'CONNECT') AS allowed",
            (CONSUMER_ROLE, database),
        )
        if not cursor.fetchone()["allowed"]:
            raise M3AdminError(f"M3_CONSUMER_DATABASE_CONNECT_MISSING:{database}")


def _assert_m2_baseline(bootstrap: Mapping[str, Any], database: str) -> None:
    connection = _connect_bootstrap(bootstrap, database=database)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM business.schema_migrations WHERE version = 'M2-001'"
            )
            if cursor.fetchone() is None:
                raise M3AdminError(f"M3_M2_BASELINE_MISSING:{database}")
    finally:
        connection.close()


def _ensure_test_database_from_main(bootstrap: Mapping[str, Any]) -> None:
    control = _connect_bootstrap(bootstrap)
    try:
        with control.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DATABASE,))
            if cursor.fetchone() is not None:
                return
            cursor.execute(
                """
                SELECT count(*) AS active_connections
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid()
                """,
                (MAIN_DATABASE,),
            )
            if int(cursor.fetchone()["active_connections"]) != 0:
                raise M3AdminError("M3_TEMPLATE_DATABASE_HAS_ACTIVE_CONNECTIONS")
            cursor.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                    sql.Identifier(TEST_DATABASE),
                    sql.Identifier(MAIN_DATABASE),
                )
            )
    finally:
        control.close()


def _apply_and_verify_migration(
    bootstrap: Mapping[str, Any],
    database: str,
    migration: str,
) -> None:
    connection = _connect_bootstrap(bootstrap, database=database)
    try:
        with connection.cursor() as cursor:
            cursor.execute(migration)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM business.schema_migrations WHERE version = %s",
                (MIGRATION_VERSION,),
            )
            if cursor.fetchone() is None:
                raise M3AdminError(f"M3_MIGRATION_VERSION_MISSING:{database}")
            cursor.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'business' AND table_type = 'BASE TABLE'
                """
            )
            tables = {row["table_name"] for row in cursor.fetchall()}
            if not REQUIRED_M3_BUSINESS_TABLES.issubset(tables):
                raise M3AdminError(f"M3_BUSINESS_TABLE_SET_INVALID:{database}")
            for table in ("evidence_ingestion_receipts", "evidence_items"):
                cursor.execute(
                    """
                    SELECT count(*) AS trigger_count
                    FROM pg_trigger
                    WHERE tgrelid = %s::regclass
                      AND tgname = %s
                      AND NOT tgisinternal
                      AND tgenabled <> 'D'
                    """,
                    (
                        f"business.{table}",
                        f"reject_{table}_mutation",
                    ),
                )
                if int(cursor.fetchone()["trigger_count"]) != 1:
                    raise M3AdminError(f"M3_IMMUTABLE_TRIGGER_INVALID:{database}:{table}")
            cursor.execute(
                """
                SELECT has_table_privilege(%s, %s, 'INSERT') AS can_insert,
                       has_table_privilege(%s, %s, 'UPDATE') AS can_update,
                       has_table_privilege(%s, %s, 'DELETE') AS can_delete
                """,
                (
                    CONSUMER_ROLE,
                    "business.evidence_items",
                    CONSUMER_ROLE,
                    "business.evidence_items",
                    CONSUMER_ROLE,
                    "business.evidence_items",
                ),
            )
            consumer_business = cursor.fetchone()
            if any(consumer_business.values()):
                raise M3AdminError(f"M3_CONSUMER_BUSINESS_PRIVILEGE_INVALID:{database}")
            cursor.execute(
                "SELECT has_table_privilege(%s, %s, 'SELECT') AS allowed",
                (CONSUMER_ROLE, "m3_integration.evidence_ingest_requested_v"),
            )
            if not cursor.fetchone()["allowed"]:
                raise M3AdminError(f"M3_CONSUMER_VIEW_PRIVILEGE_MISSING:{database}")
    finally:
        connection.close()


def provision_m3(
    bootstrap_env_path: str | Path,
    m3_env_path: str | Path,
    migration_path: str | Path,
) -> None:
    bootstrap = _load_bootstrap_env(bootstrap_env_path)
    m3_env = load_m3_env(m3_env_path)
    migration = _load_migration(migration_path)

    _assert_m2_baseline(bootstrap, MAIN_DATABASE)
    control = _connect_bootstrap(bootstrap)
    try:
        _ensure_consumer_role(control, m3_env[CONSUMER_PASSWORD_FIELD])
    finally:
        control.close()

    _ensure_test_database_from_main(bootstrap)
    control = _connect_bootstrap(bootstrap)
    try:
        for database in (MAIN_DATABASE, TEST_DATABASE):
            _ensure_consumer_database_access(control, database)
    finally:
        control.close()
    _assert_m2_baseline(bootstrap, TEST_DATABASE)
    for database in (MAIN_DATABASE, TEST_DATABASE):
        _apply_and_verify_migration(bootstrap, database, migration)


__all__ = (
    "CONSUMER_PASSWORD_FIELD",
    "CONSUMER_ROLE",
    "M3AdminError",
    "MIGRATION_FILENAME",
    "MIGRATION_VERSION",
    "provision_m3",
)
