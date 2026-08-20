import os
import sqlite3
from pathlib import Path
from typing import Any

from backend.validators import validate_select_sql


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT_DIR / "data" / "retail.db"


def database_path() -> Path:
    return Path(os.getenv("DATABASE_PATH", DEFAULT_DB_PATH)).resolve()


def connect_read_only() -> sqlite3.Connection:
    db_path = database_path()
    if not db_path.exists():
        raise FileNotFoundError(
            f"Database not found at {db_path}. Run `python data/seed.py` first."
        )

    # The validator is deliberately simple, so read-only mode is an independent,
    # database-enforced boundary if application policy misses an edge case.
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def load_schema() -> str:
    # This small demo reloads metadata for every question so the LLM receives fresh,
    # visible schema context. Production would cache it by migration version and
    # invalidate the cache after schema changes.
    with connect_read_only() as connection:
        table_rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()

        parts: list[str] = []
        for table_row in table_rows:
            table_name = table_row["name"]
            columns = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
            # Foreign keys give the LLM join relationships without exposing sample rows.
            foreign_keys = connection.execute(
                f"PRAGMA foreign_key_list({table_name})"
            ).fetchall()
            parts.append(f"Table: {table_name}")
            for column in columns:
                primary_key = " primary key" if column["pk"] else ""
                parts.append(f"- {column['name']} {column['type']}{primary_key}")
            for foreign_key in foreign_keys:
                parts.append(
                    f"- foreign key: {foreign_key['from']} -> "
                    f"{foreign_key['table']}.{foreign_key['to']}"
                )
            parts.append("")

        return "\n".join(parts).strip()


def execute_select(sql: str) -> tuple[list[dict[str, Any]], list[str]]:
    is_valid, reason, safe_sql = validate_select_sql(sql)
    if not is_valid or safe_sql is None:
        raise ValueError(reason or "Invalid SQL query.")

    with connect_read_only() as connection:
        # Planning first surfaces schema and SQL errors before fetching result rows.
        connection.execute(f"EXPLAIN QUERY PLAN {safe_sql}")
        cursor = connection.execute(safe_sql)
        rows = [dict(row) for row in cursor.fetchall()]
        columns = [description[0] for description in cursor.description or []]
        return rows, columns
