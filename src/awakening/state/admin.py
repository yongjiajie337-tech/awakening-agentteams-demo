"""Local PostgreSQL administration for the approved M2 databases.

This module is intentionally separate from the State Service runtime store.
It reads local secret files internally, never prints their values, provisions
only the two fixed M2 databases, and can drop only the fixed M2 test database.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from psycopg import Error as PsycopgError
from psycopg import OperationalError, connect, sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row


OWNER_ROLE: Final = "awakening_m2_owner"
RUNTIME_ROLE: Final = "awakening_state_service"
PROBE_ROLE: Final = "awakening_m2_adapter_probe"
MAIN_DATABASE: Final = "awakening_m2"
TEST_DATABASE: Final = "awakening_m2_test"
MIGRATION_VERSION: Final = "M2-001"
MIGRATION_FILENAME: Final = "001_authoritative_state.sql"

M2_ENV_FIELDS: Final = (
    "AWAKENING_M2_DB_HOST",
    "AWAKENING_M2_DB_PORT",
    "AWAKENING_M2_DB_USER",
    "AWAKENING_STATE_SERVICE_DB_PASSWORD",
    "AWAKENING_M2_DB_NAME",
    "AWAKENING_M2_TEST_DB_NAME",
)

REQUIRED_TABLES: Final = frozenset(
    {
        "schema_migrations",
        "programs",
        "program_members",
        "plan_versions",
        "tasks",
        "state_change_proposals",
        "approval_requests",
        "human_decisions",
        "command_receipts",
        "domain_events",
        "outbox_events",
    }
)

IMMUTABLE_TABLES: Final = frozenset(
    {"plan_versions", "tasks", "human_decisions", "domain_events", "outbox_events"}
)

_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class M2AdminError(RuntimeError):
    """Stable administration failure that contains no secret value."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _read_env_file(path: str | Path) -> dict[str, str]:
    env_path = Path(path)
    if not env_path.is_file() or env_path.is_symlink():
        raise M2AdminError("M2_ENV_FILE_INVALID")
    try:
        text = env_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise M2AdminError("M2_ENV_FILE_UNREADABLE") from exc

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in raw_line:
            raise M2AdminError(f"M2_ENV_LINE_INVALID:{line_number}")
        key, value = raw_line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not _ENV_KEY.fullmatch(key):
            raise M2AdminError(f"M2_ENV_KEY_INVALID:{line_number}")
        if key in values:
            raise M2AdminError(f"M2_ENV_KEY_DUPLICATE:{key}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def _required_nonempty(values: Mapping[str, str], key: str) -> str:
    value = values.get(key, "")
    if not value:
        raise M2AdminError(f"M2_ENV_VALUE_MISSING:{key}")
    return value


def _parse_port(value: str, field: str) -> int:
    try:
        port = int(value, 10)
    except ValueError as exc:
        raise M2AdminError(f"M2_ENV_PORT_INVALID:{field}") from exc
    if not 1 <= port <= 65535:
        raise M2AdminError(f"M2_ENV_PORT_INVALID:{field}")
    return port


def load_m2_env(path: str | Path) -> dict[str, str]:
    """Load and strictly validate the fixed local M2 runtime environment."""

    values = _read_env_file(path)
    if set(values) != set(M2_ENV_FIELDS):
        raise M2AdminError("M2_RUNTIME_ENV_FIELDS_INVALID")
    for key in M2_ENV_FIELDS:
        _required_nonempty(values, key)
    if values["AWAKENING_M2_DB_HOST"] != "127.0.0.1":
        raise M2AdminError("M2_RUNTIME_DB_HOST_INVALID")
    _parse_port(values["AWAKENING_M2_DB_PORT"], "AWAKENING_M2_DB_PORT")
    if values["AWAKENING_M2_DB_USER"] != RUNTIME_ROLE:
        raise M2AdminError("M2_RUNTIME_DB_USER_INVALID")
    if values["AWAKENING_M2_DB_NAME"] != MAIN_DATABASE:
        raise M2AdminError("M2_RUNTIME_MAIN_DATABASE_INVALID")
    if values["AWAKENING_M2_TEST_DB_NAME"] != TEST_DATABASE:
        raise M2AdminError("M2_RUNTIME_TEST_DATABASE_INVALID")
    return values


def build_runtime_dsn(values: Mapping[str, str], *, test: bool = False) -> str:
    """Build a psycopg conninfo string without logging it."""

    validated = dict(values)
    for key in M2_ENV_FIELDS:
        _required_nonempty(validated, key)
    if validated["AWAKENING_M2_DB_HOST"] != "127.0.0.1":
        raise M2AdminError("M2_RUNTIME_DB_HOST_INVALID")
    if validated["AWAKENING_M2_DB_USER"] != RUNTIME_ROLE:
        raise M2AdminError("M2_RUNTIME_DB_USER_INVALID")
    if validated["AWAKENING_M2_DB_NAME"] != MAIN_DATABASE:
        raise M2AdminError("M2_RUNTIME_MAIN_DATABASE_INVALID")
    if validated["AWAKENING_M2_TEST_DB_NAME"] != TEST_DATABASE:
        raise M2AdminError("M2_RUNTIME_TEST_DATABASE_INVALID")
    database_key = "AWAKENING_M2_TEST_DB_NAME" if test else "AWAKENING_M2_DB_NAME"
    return make_conninfo(
        host=validated["AWAKENING_M2_DB_HOST"],
        port=_parse_port(validated["AWAKENING_M2_DB_PORT"], "AWAKENING_M2_DB_PORT"),
        user=validated["AWAKENING_M2_DB_USER"],
        password=validated["AWAKENING_STATE_SERVICE_DB_PASSWORD"],
        dbname=validated[database_key],
        connect_timeout=5,
    )


def _load_bootstrap_env(path: str | Path) -> dict[str, Any]:
    values = _read_env_file(path)
    user = _required_nonempty(values, "POSTGRES_USER")
    database = _required_nonempty(values, "POSTGRES_DB")
    password = _required_nonempty(values, "AWAKENING_POSTGRES_PASSWORD")
    port = _parse_port(_required_nonempty(values, "POSTGRES_PORT"), "POSTGRES_PORT")
    if database in {MAIN_DATABASE, TEST_DATABASE}:
        raise M2AdminError("M2_BOOTSTRAP_DATABASE_UNSAFE")
    return {
        "host": "127.0.0.1",
        "port": port,
        "user": user,
        "password": password,
        "dbname": database,
        "connect_timeout": 5,
    }


def _connect_bootstrap(config: Mapping[str, Any], *, database: str | None = None):
    options = dict(config)
    if database is not None:
        options["dbname"] = database
    try:
        return connect(**options, autocommit=True, row_factory=dict_row)
    except OperationalError as exc:
        raise M2AdminError("M2_BOOTSTRAP_CONNECTION_FAILED") from exc


def _ensure_roles(connection, runtime_password: str) -> None:
    role_creation = (
        (
            OWNER_ROLE,
            "CREATE ROLE awakening_m2_owner NOLOGIN NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS",
        ),
        (
            RUNTIME_ROLE,
            "CREATE ROLE awakening_state_service LOGIN NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE INHERIT NOREPLICATION NOBYPASSRLS",
        ),
        (
            PROBE_ROLE,
            "CREATE ROLE awakening_m2_adapter_probe NOLOGIN NOSUPERUSER "
            "NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS",
        ),
    )
    with connection.cursor() as cursor:
        for role_name, create_statement in role_creation:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role_name,))
            if cursor.fetchone() is None:
                cursor.execute(create_statement)

        cursor.execute(
            "ALTER ROLE awakening_m2_owner NOLOGIN NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
        )
        cursor.execute(
            "ALTER ROLE awakening_state_service LOGIN NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE INHERIT NOREPLICATION NOBYPASSRLS"
        )
        cursor.execute(
            "ALTER ROLE awakening_m2_adapter_probe NOLOGIN NOSUPERUSER "
            "NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
        )
        cursor.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(RUNTIME_ROLE),
                sql.Literal(runtime_password),
            )
        )

    expected = {
        OWNER_ROLE: (False, False),
        RUNTIME_ROLE: (True, True),
        PROBE_ROLE: (False, False),
    }
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT rolname, rolcanlogin, rolinherit, rolsuper, rolcreatedb,
                   rolcreaterole, rolreplication, rolbypassrls
            FROM pg_roles
            WHERE rolname = ANY(%s)
            """,
            (list(expected),),
        )
        rows = {row["rolname"]: row for row in cursor.fetchall()}
    if set(rows) != set(expected):
        raise M2AdminError("M2_ROLE_SET_INVALID")
    for role_name, (can_login, inherits) in expected.items():
        row = rows[role_name]
        if (
            bool(row["rolcanlogin"]) != can_login
            or bool(row["rolinherit"]) != inherits
            or row["rolsuper"]
            or row["rolcreatedb"]
            or row["rolcreaterole"]
            or row["rolreplication"]
            or row["rolbypassrls"]
        ):
            raise M2AdminError(f"M2_ROLE_ATTRIBUTES_INVALID:{role_name}")


def _database_row(connection, database: str) -> dict[str, Any] | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT d.datname, owner.rolname AS owner_name,
                   pg_encoding_to_char(d.encoding) AS encoding_name,
                   d.datistemplate, d.datallowconn
            FROM pg_database AS d
            JOIN pg_roles AS owner ON owner.oid = d.datdba
            WHERE d.datname = %s
            """,
            (database,),
        )
        row = cursor.fetchone()
    return dict(row) if row is not None else None


def _ensure_database(connection, database: str) -> None:
    if database not in {MAIN_DATABASE, TEST_DATABASE}:
        raise M2AdminError("M2_DATABASE_NAME_NOT_ALLOWED")
    row = _database_row(connection, database)
    if row is None:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "CREATE DATABASE {} OWNER {} TEMPLATE template0 ENCODING 'UTF8'"
                ).format(sql.Identifier(database), sql.Identifier(OWNER_ROLE))
            )
        row = _database_row(connection, database)
    if row is None:
        raise M2AdminError(f"M2_DATABASE_CREATE_FAILED:{database}")
    if (
        row["owner_name"] != OWNER_ROLE
        or row["encoding_name"] != "UTF8"
        or row["datistemplate"]
        or not row["datallowconn"]
    ):
        raise M2AdminError(f"M2_DATABASE_IDENTITY_INVALID:{database}")
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(database)
            )
        )
        cursor.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(
                sql.Identifier(database),
                sql.Identifier(OWNER_ROLE),
                sql.Identifier(RUNTIME_ROLE),
            )
        )


def _load_migration(path: str | Path) -> str:
    migration_path = Path(path)
    if (
        migration_path.name != MIGRATION_FILENAME
        or not migration_path.is_file()
        or migration_path.is_symlink()
    ):
        raise M2AdminError("M2_MIGRATION_FILE_INVALID")
    try:
        migration = migration_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise M2AdminError("M2_MIGRATION_FILE_UNREADABLE") from exc
    if MIGRATION_VERSION not in migration or "business.schema_migrations" not in migration:
        raise M2AdminError("M2_MIGRATION_CONTRACT_INVALID")
    return migration


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
                raise M2AdminError(f"M2_MIGRATION_VERSION_MISSING:{database}")
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'business' AND table_type = 'BASE TABLE'
                """
            )
            tables = {row["table_name"] for row in cursor.fetchall()}
            if tables != REQUIRED_TABLES:
                raise M2AdminError(f"M2_BUSINESS_TABLE_SET_INVALID:{database}")
            cursor.execute(
                """
                SELECT c.relname AS table_name, owner.rolname AS owner_name
                FROM pg_class AS c
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                JOIN pg_roles AS owner ON owner.oid = c.relowner
                WHERE n.nspname = 'business' AND c.relkind = 'r'
                """
            )
            if any(row["owner_name"] != OWNER_ROLE for row in cursor.fetchall()):
                raise M2AdminError(f"M2_BUSINESS_TABLE_OWNER_INVALID:{database}")
            cursor.execute(
                """
                SELECT c.relname AS table_name, count(t.oid) AS trigger_count
                FROM pg_class AS c
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                LEFT JOIN pg_trigger AS t
                  ON t.tgrelid = c.oid
                 AND NOT t.tgisinternal
                 AND t.tgenabled <> 'D'
                 AND t.tgname = 'reject_' || c.relname || '_mutation'
                WHERE n.nspname = 'business' AND c.relname = ANY(%s)
                GROUP BY c.relname
                """,
                (list(IMMUTABLE_TABLES),),
            )
            trigger_counts = {
                row["table_name"]: row["trigger_count"] for row in cursor.fetchall()
            }
            if set(trigger_counts) != set(IMMUTABLE_TABLES) or any(
                count != 1 for count in trigger_counts.values()
            ):
                raise M2AdminError(f"M2_HISTORY_TRIGGER_SET_INVALID:{database}")
            cursor.execute(
                "SELECT has_schema_privilege(%s, 'business', 'USAGE') AS allowed",
                (RUNTIME_ROLE,),
            )
            if not cursor.fetchone()["allowed"]:
                raise M2AdminError(f"M2_RUNTIME_SCHEMA_USAGE_MISSING:{database}")
            for table in IMMUTABLE_TABLES:
                cursor.execute(
                    """
                    SELECT has_table_privilege(%s, %s, 'SELECT') AS can_select,
                           has_table_privilege(%s, %s, 'INSERT') AS can_insert,
                           has_table_privilege(%s, %s, 'UPDATE') AS can_update,
                           has_table_privilege(%s, %s, 'DELETE') AS can_delete
                    """,
                    (
                        RUNTIME_ROLE,
                        f"business.{table}",
                        RUNTIME_ROLE,
                        f"business.{table}",
                        RUNTIME_ROLE,
                        f"business.{table}",
                        RUNTIME_ROLE,
                        f"business.{table}",
                    ),
                )
                privileges = cursor.fetchone()
                if (
                    not privileges["can_select"]
                    or not privileges["can_insert"]
                    or privileges["can_update"]
                    or privileges["can_delete"]
                ):
                    raise M2AdminError(
                        f"M2_HISTORY_RUNTIME_PRIVILEGE_INVALID:{database}:{table}"
                    )
            cursor.execute(
                "SELECT has_schema_privilege(%s, 'business', 'USAGE') AS allowed",
                (PROBE_ROLE,),
            )
            if cursor.fetchone()["allowed"]:
                raise M2AdminError(f"M2_PROBE_SCHEMA_PRIVILEGE_INVALID:{database}")
    finally:
        connection.close()


def _verify_runtime_connection(values: Mapping[str, str], *, test: bool) -> None:
    expected_database = TEST_DATABASE if test else MAIN_DATABASE
    try:
        with connect(
            build_runtime_dsn(values, test=test),
            autocommit=True,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT current_user AS user_name,
                           current_database() AS database_name,
                           EXISTS (
                               SELECT 1 FROM business.schema_migrations
                               WHERE version = %s
                           ) AS migration_present
                    """,
                    (MIGRATION_VERSION,),
                )
                row = cursor.fetchone()
    except OperationalError as exc:
        raise M2AdminError(
            f"M2_RUNTIME_CONNECTION_FAILED:{expected_database}"
        ) from exc
    if (
        row is None
        or row["user_name"] != RUNTIME_ROLE
        or row["database_name"] != expected_database
        or not row["migration_present"]
    ):
        raise M2AdminError(f"M2_RUNTIME_IDENTITY_INVALID:{expected_database}")


def provision(
    bootstrap_env_path: str | Path,
    runtime_env_path: str | Path,
    migration_path: str | Path,
) -> None:
    bootstrap = _load_bootstrap_env(bootstrap_env_path)
    runtime = load_m2_env(runtime_env_path)
    if int(runtime["AWAKENING_M2_DB_PORT"]) != bootstrap["port"]:
        raise M2AdminError("M2_BOOTSTRAP_RUNTIME_PORT_MISMATCH")
    migration = _load_migration(migration_path)

    control = _connect_bootstrap(bootstrap)
    try:
        _ensure_roles(control, runtime["AWAKENING_STATE_SERVICE_DB_PASSWORD"])
        for database in (MAIN_DATABASE, TEST_DATABASE):
            _ensure_database(control, database)
    finally:
        control.close()

    for database in (MAIN_DATABASE, TEST_DATABASE):
        _apply_and_verify_migration(bootstrap, database, migration)
    _verify_runtime_connection(runtime, test=False)
    _verify_runtime_connection(runtime, test=True)


def drop_test_database(bootstrap_env_path: str | Path) -> bool:
    """Drop only ``awakening_m2_test`` after terminating its connections."""

    bootstrap = _load_bootstrap_env(bootstrap_env_path)
    if TEST_DATABASE == MAIN_DATABASE or bootstrap["dbname"] == TEST_DATABASE:
        raise M2AdminError("M2_TEST_DATABASE_DROP_GUARD_FAILED")

    control = _connect_bootstrap(bootstrap)
    try:
        row = _database_row(control, TEST_DATABASE)
        if row is None:
            return False
        if row["owner_name"] != OWNER_ROLE or row["datistemplate"]:
            raise M2AdminError("M2_TEST_DATABASE_IDENTITY_INVALID")
        with control.cursor() as cursor:
            cursor.execute(
                sql.SQL("ALTER DATABASE {} WITH ALLOW_CONNECTIONS false").format(
                    sql.Identifier(TEST_DATABASE)
                )
            )
            cursor.execute(
                """
                SELECT pid, pg_terminate_backend(pid) AS terminated
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid()
                """,
                (TEST_DATABASE,),
            )
            if any(not result["terminated"] for result in cursor.fetchall()):
                raise M2AdminError("M2_TEST_DATABASE_CONNECTION_TERMINATE_FAILED")
            cursor.execute(
                "SELECT count(*) AS count FROM pg_stat_activity WHERE datname = %s",
                (TEST_DATABASE,),
            )
            if cursor.fetchone()["count"] != 0:
                raise M2AdminError("M2_TEST_DATABASE_CONNECTIONS_REMAIN")
            cursor.execute(
                sql.SQL("DROP DATABASE {}").format(sql.Identifier(TEST_DATABASE))
            )
        if _database_row(control, TEST_DATABASE) is not None:
            raise M2AdminError("M2_TEST_DATABASE_DROP_POSTCONDITION_FAILED")
        return True
    finally:
        control.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Awakening M2 PostgreSQL admin")
    subparsers = parser.add_subparsers(dest="operation", required=True)

    provision_parser = subparsers.add_parser("provision")
    provision_parser.add_argument("--bootstrap-env", required=True)
    provision_parser.add_argument("--runtime-env", required=True)
    provision_parser.add_argument("--migration", required=True)

    drop_parser = subparsers.add_parser("drop-test-database")
    drop_parser.add_argument("--bootstrap-env", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.operation == "provision":
            provision(args.bootstrap_env, args.runtime_env, args.migration)
            print("M2_PROVISION_STATUS=passed")
            print(f"M2_PROVISION_MAIN_DATABASE={MAIN_DATABASE}")
            print(f"M2_PROVISION_TEST_DATABASE={TEST_DATABASE}")
            print(f"M2_PROVISION_MIGRATION={MIGRATION_VERSION}")
            return 0
        if args.operation == "drop-test-database":
            dropped = drop_test_database(args.bootstrap_env)
            print(
                "M2_TEST_DATABASE_DROP_STATUS="
                + ("dropped" if dropped else "absent_no_action")
            )
            print(f"M2_TEST_DATABASE_NAME={TEST_DATABASE}")
            return 0
        raise M2AdminError("M2_ADMIN_OPERATION_INVALID")
    except M2AdminError as exc:
        print(f"M2_ADMIN_STATUS=failed:{exc.reason_code}", file=sys.stderr)
        return 1
    except PsycopgError:
        print("M2_ADMIN_STATUS=failed:M2_POSTGRES_OPERATION_FAILED", file=sys.stderr)
        return 1
    except Exception:
        print("M2_ADMIN_STATUS=failed:M2_ADMIN_UNEXPECTED_ERROR", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "M2AdminError",
    "build_runtime_dsn",
    "drop_test_database",
    "load_m2_env",
    "main",
    "provision",
]
