"""
export_db.py — Export MySQL tables to SQL dump files via PyMySQL.

Replaces mysqldump to avoid MySQL 9.x mysql_native_password plugin removal.
Connects once, dumps all required tables, then closes the connection.

Outputs (project root, gitignored):
  forarchive.sql       — players, player_stats, sports
  forarchiveGAMES.sql  — games

Credentials are read from .env in the project root.
"""

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Load .env ─────────────────────────────────────────────────────────────────
ENV_FILE = Path(__file__).parent.parent / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "127.0.0.1"),
    "port":     int(os.environ.get("DB_PORT", "3306")),
    "db":       os.environ.get("DB_NAME", "altfantasysports"),
    "user":     os.environ.get("DB_USER", ""),
    "password": os.environ.get("DB_PASS", ""),
    "charset":  "utf8mb4",
}

PROJECT_DIR = Path(__file__).parent.parent
FORARCHIVE_SQL      = PROJECT_DIR / "forarchive.sql"
FORARCHIVE_GAMES_SQL = PROJECT_DIR / "forarchiveGAMES.sql"

# Tables to export to each file
EXPORT_MAP = {
    FORARCHIVE_SQL:       ["sports", "players", "player_stats"],
    FORARCHIVE_GAMES_SQL: ["games"],
}

# Rows per INSERT statement — keeps individual statements a reasonable size
CHUNK_SIZE = 500


def _escape_val(val) -> str:
    """Render a Python value as a MySQL literal."""
    if val is None:
        return "NULL"
    if isinstance(val, (int, float)):
        return repr(val)
    # str / bytes / date / datetime → quoted string
    s = str(val)
    # Escape backslash first, then single quote
    s = s.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{s}'"


def dump_table(conn, table: str, out_file) -> int:
    """
    Write CREATE TABLE + chunked INSERTs for one table directly to out_file.
    Uses SSCursor (server-side cursor) to stream rows without buffering the
    whole table in memory — essential for large tables like player_stats.
    Returns the number of rows written.
    """
    from pymysql.cursors import SSCursor

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    out_file.write(f"-- Table: `{table}`  (exported {ts})\n")
    out_file.write(f"DROP TABLE IF EXISTS `{table}`;\n")

    # SHOW CREATE TABLE uses a normal buffered cursor (single small result)
    with conn.cursor() as cur:
        cur.execute(f"SHOW CREATE TABLE `{table}`")
        _, create_sql = cur.fetchone()
        out_file.write(create_sql + ";\n\n")
        # Grab column names cheaply
        cur.execute(f"SELECT * FROM `{table}` LIMIT 0")
        columns = [d[0] for d in cur.description]

    col_list = ", ".join(f"`{c}`" for c in columns)

    def flush_chunk(chunk: list):
        values_clause = ",\n  ".join(
            "(" + ", ".join(_escape_val(v) for v in row) + ")"
            for row in chunk
        )
        out_file.write(
            f"INSERT INTO `{table}` ({col_list}) VALUES\n  {values_clause};\n"
        )

    row_count = 0
    chunk: list = []

    # SSCursor streams rows from the server one at a time
    with conn.cursor(SSCursor) as ss:
        ss.execute(f"SELECT * FROM `{table}`")
        for row in ss:
            chunk.append(row)
            row_count += 1
            if len(chunk) >= CHUNK_SIZE:
                flush_chunk(chunk)
                chunk = []

    if chunk:
        flush_chunk(chunk)

    if row_count == 0:
        out_file.write(f"-- (no rows in `{table}`)\n")

    out_file.write("\n")
    return row_count


def main():
    try:
        import pymysql
    except ImportError:
        print("ERROR: PyMySQL not installed. Run: .venv/bin/pip install PyMySQL", file=sys.stderr)
        sys.exit(1)

    print("  Connecting to database ...", end=" ", flush=True)
    try:
        conn = pymysql.connect(**DB_CONFIG)
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    print("OK")

    try:
        for out_path, tables in EXPORT_MAP.items():
            with out_path.open("w", encoding="utf-8") as f:
                f.write(
                    "-- AltSports Archive SQL dump\n"
                    f"-- Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                    f"-- Tables: {', '.join(tables)}\n\n"
                    "SET NAMES utf8mb4;\n"
                    "SET FOREIGN_KEY_CHECKS=0;\n\n"
                )
                total_rows = 0
                for t in tables:
                    print(f"  {t} ...", end=" ", flush=True)
                    n = dump_table(conn, t, f)
                    print(f"{n:,} rows")
                    total_rows += n
                f.write("SET FOREIGN_KEY_CHECKS=1;\n")
            print(f"  → {out_path.name} written")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
