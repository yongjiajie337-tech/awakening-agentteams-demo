"""Narrow M4 migration and observability-role provisioning.

This module applies only the additive M4-003 migration to the already accepted
main State database.  It neither recreates the M2/M3 databases nor invokes an
older module provisioner.
"""

from __future__ import annotations

from collections.abc import Mapping
import base64
import hashlib
import hmac
from pathlib import Path
import secrets
from typing import Any, Final

from psycopg import sql

from awakening.state.admin import _connect_bootstrap, _load_bootstrap_env, _read_env_file


MAIN_DATABASE: Final = "awakening_m2"
MIGRATION_VERSION: Final = "M4-003"
MIGRATION_FILENAME: Final = "003_agent_runtime.sql"
OBSERVABILITY_ROLE: Final = "awakening_m4_observability_writer"
OBSERVABILITY_PASSWORD_FIELD: Final = "AWAKENING_M4_OBSERVABILITY_DB_PASSWORD"
STATE_TOKEN_FIELDS: Final = frozenset(
    {
        "AWAKENING_M4_STATE_MANAGER_TOKEN",
        "AWAKENING_M4_STATE_ARCHITECT_TOKEN",
        "AWAKENING_M4_STATE_COACH_TOKEN",
        "AWAKENING_M4_STATE_REVIEWER_TOKEN",
    }
)
STATE_SERVICE_ROLE: Final = "awakening_state_service"
TOOL_PROBE_ROLE: Final = "awakening_m4_tool_adapter_probe"
GATEWAY_PROBE_ROLE: Final = "awakening_m4_gateway_probe"

REQUIRED_BUSINESS_TABLES: Final = frozenset(
    {
        "runtime_config_snapshots",
        "model_budget_reservations",
        "model_usage_ledger",
    }
)
REQUIRED_OBSERVABILITY_TABLES: Final = frozenset(
    {"context_manifests", "skill_invocation_receipts"}
)
REQUIRED_TRIGGERS: Final = frozenset(
    {
        "reject_runtime_config_snapshots_mutation",
        "reject_model_usage_ledger_mutation",
        "reject_context_manifests_mutation",
        "reject_skill_invocation_receipts_mutation",
        "validate_model_usage_ledger_insert",
        "enforce_model_budget_reservation_transition",
    }
)


class M4AdminError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def load_m4_env(path: str | Path) -> dict[str, str]:
    values = _read_env_file(path)
    allowed_sets = (
        {OBSERVABILITY_PASSWORD_FIELD},
        {OBSERVABILITY_PASSWORD_FIELD, *STATE_TOKEN_FIELDS},
    )
    if set(values) not in allowed_sets:
        raise M4AdminError("M4_ENV_FIELDS_INVALID")
    password = values.get(OBSERVABILITY_PASSWORD_FIELD, "")
    if len(password) < 32 or any(character.isspace() for character in password):
        raise M4AdminError(f"M4_ENV_REQUIRED:{OBSERVABILITY_PASSWORD_FIELD}")
    return {OBSERVABILITY_PASSWORD_FIELD: password}


def _scram_sha256_verifier(password: str) -> str:
    """Derive a PostgreSQL SCRAM verifier so plaintext is not sent in SQL."""

    iterations = 4096
    salt = secrets.token_bytes(16)
    salted_password = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    client_key = hmac.new(salted_password, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted_password, b"Server Key", hashlib.sha256).digest()
    return (
        f"SCRAM-SHA-256${iterations}:"
        f"{base64.b64encode(salt).decode('ascii')}$"
        f"{base64.b64encode(stored_key).decode('ascii')}:"
        f"{base64.b64encode(server_key).decode('ascii')}"
    )


def _load_migration(path: str | Path) -> str:
    migration_path = Path(path)
    if (
        migration_path.name != MIGRATION_FILENAME
        or not migration_path.is_file()
        or migration_path.is_symlink()
    ):
        raise M4AdminError("M4_MIGRATION_FILE_INVALID")
    try:
        migration = migration_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise M4AdminError("M4_MIGRATION_FILE_UNREADABLE") from exc
    required_markers = (
        MIGRATION_VERSION,
        "business.runtime_config_snapshots",
        "observability.context_manifests",
    )
    if any(marker not in migration for marker in required_markers):
        raise M4AdminError("M4_MIGRATION_CONTRACT_INVALID")
    return migration


def _assert_accepted_baseline(bootstrap: Mapping[str, Any]) -> None:
    connection = _connect_bootstrap(bootstrap, database=MAIN_DATABASE)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT version
                FROM business.schema_migrations
                WHERE version IN ('M2-001', 'M3-002')
                """
            )
            versions = {row["version"] for row in cursor.fetchall()}
            if versions != {"M2-001", "M3-002"}:
                raise M4AdminError("M4_ACCEPTED_BASELINE_MISSING")
    finally:
        connection.close()


def _apply_migration(bootstrap: Mapping[str, Any], migration: str) -> None:
    connection = _connect_bootstrap(bootstrap, database=MAIN_DATABASE)
    try:
        with connection.cursor() as cursor:
            cursor.execute(migration)
    finally:
        connection.close()


def _configure_observability_role(
    bootstrap: Mapping[str, Any],
    password: str,
) -> None:
    verifier = _scram_sha256_verifier(password)
    control = _connect_bootstrap(bootstrap)
    try:
        with control.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(
                    sql.Identifier(OBSERVABILITY_ROLE),
                    sql.Literal(verifier),
                )
            )
            cursor.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(MAIN_DATABASE),
                    sql.Identifier(OBSERVABILITY_ROLE),
                )
            )
    finally:
        control.close()


def _assert_table_set(
    cursor: Any,
    *,
    schema: str,
    expected: frozenset[str],
) -> None:
    cursor.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = %s AND table_type = 'BASE TABLE'
        """,
        (schema,),
    )
    present = {row["table_name"] for row in cursor.fetchall()}
    if not expected.issubset(present):
        raise M4AdminError(f"M4_{schema.upper()}_TABLE_SET_INVALID")


def _has_any_table_privilege(cursor: Any, role: str, relation: str) -> bool:
    cursor.execute(
        """
        SELECT has_table_privilege(%s, %s, 'SELECT') AS can_select,
               has_table_privilege(%s, %s, 'INSERT') AS can_insert,
               has_table_privilege(%s, %s, 'UPDATE') AS can_update,
               has_table_privilege(%s, %s, 'DELETE') AS can_delete
        """,
        (role, relation, role, relation, role, relation, role, relation),
    )
    return any(bool(value) for value in cursor.fetchone().values())


def _verify_migration(bootstrap: Mapping[str, Any]) -> None:
    connection = _connect_bootstrap(bootstrap, database=MAIN_DATABASE)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM business.schema_migrations WHERE version = %s",
                (MIGRATION_VERSION,),
            )
            if cursor.fetchone() is None:
                raise M4AdminError("M4_MIGRATION_VERSION_MISSING")

            _assert_table_set(
                cursor,
                schema="business",
                expected=REQUIRED_BUSINESS_TABLES,
            )
            _assert_table_set(
                cursor,
                schema="observability",
                expected=REQUIRED_OBSERVABILITY_TABLES,
            )

            cursor.execute(
                """
                SELECT tgname
                FROM pg_trigger
                WHERE NOT tgisinternal AND tgenabled <> 'D'
                  AND tgname = ANY(%s)
                """,
                (list(REQUIRED_TRIGGERS),),
            )
            triggers = {row["tgname"] for row in cursor.fetchall()}
            if triggers != REQUIRED_TRIGGERS:
                raise M4AdminError("M4_TRIGGER_SET_INVALID")

            for relation in (
                "business.runtime_config_snapshots",
                "business.model_budget_reservations",
                "business.model_usage_ledger",
            ):
                cursor.execute(
                    """
                    SELECT has_table_privilege(%s, %s, 'SELECT') AS can_select,
                           has_table_privilege(%s, %s, 'INSERT') AS can_insert,
                           has_table_privilege(%s, %s, 'DELETE') AS can_delete
                    """,
                    (
                        STATE_SERVICE_ROLE,
                        relation,
                        STATE_SERVICE_ROLE,
                        relation,
                        STATE_SERVICE_ROLE,
                        relation,
                    ),
                )
                privileges = cursor.fetchone()
                if (
                    not privileges["can_select"]
                    or not privileges["can_insert"]
                    or privileges["can_delete"]
                ):
                    raise M4AdminError("M4_STATE_SERVICE_PRIVILEGE_INVALID")

            for relation in (
                "observability.context_manifests",
                "observability.skill_invocation_receipts",
            ):
                cursor.execute(
                    """
                    SELECT has_table_privilege(%s, %s, 'SELECT') AS can_select,
                           has_table_privilege(%s, %s, 'INSERT') AS can_insert,
                           has_table_privilege(%s, %s, 'UPDATE') AS can_update,
                           has_table_privilege(%s, %s, 'DELETE') AS can_delete
                    """,
                    (
                        OBSERVABILITY_ROLE,
                        relation,
                        OBSERVABILITY_ROLE,
                        relation,
                        OBSERVABILITY_ROLE,
                        relation,
                        OBSERVABILITY_ROLE,
                        relation,
                    ),
                )
                privileges = cursor.fetchone()
                if (
                    not privileges["can_select"]
                    or not privileges["can_insert"]
                    or privileges["can_update"]
                    or privileges["can_delete"]
                ):
                    raise M4AdminError("M4_OBSERVABILITY_PRIVILEGE_INVALID")

            for role in (TOOL_PROBE_ROLE, GATEWAY_PROBE_ROLE):
                for relation in (
                    "business.runtime_config_snapshots",
                    "observability.context_manifests",
                ):
                    if _has_any_table_privilege(cursor, role, relation):
                        raise M4AdminError("M4_PROBE_PRIVILEGE_INVALID")

            cursor.execute(
                """
                SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                       rolinherit, rolreplication, rolbypassrls
                FROM pg_roles WHERE rolname = %s
                """,
                (OBSERVABILITY_ROLE,),
            )
            role = cursor.fetchone()
            if role is None or role != {
                "rolcanlogin": True,
                "rolsuper": False,
                "rolcreatedb": False,
                "rolcreaterole": False,
                "rolinherit": False,
                "rolreplication": False,
                "rolbypassrls": False,
            }:
                raise M4AdminError("M4_OBSERVABILITY_ROLE_INVALID")
    finally:
        connection.close()


def provision_m4(
    bootstrap_env_path: str | Path,
    m4_env_path: str | Path,
    migration_path: str | Path,
) -> None:
    bootstrap = _load_bootstrap_env(bootstrap_env_path)
    m4_env = load_m4_env(m4_env_path)
    migration = _load_migration(migration_path)

    _assert_accepted_baseline(bootstrap)
    _apply_migration(bootstrap, migration)
    _configure_observability_role(
        bootstrap,
        m4_env[OBSERVABILITY_PASSWORD_FIELD],
    )
    _verify_migration(bootstrap)


__all__ = (
    "M4AdminError",
    "MIGRATION_FILENAME",
    "MIGRATION_VERSION",
    "OBSERVABILITY_PASSWORD_FIELD",
    "OBSERVABILITY_ROLE",
    "provision_m4",
)
