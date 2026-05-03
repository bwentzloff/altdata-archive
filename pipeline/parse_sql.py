"""
parse_sql.py
Reads forarchive.sql and outputs structured Python dicts for players,
player_stats, and sports. No external deps beyond stdlib.
"""

import re
import json
from pathlib import Path

SQL_FILE = Path(__file__).parent.parent / "forarchive.sql"
OUT_DIR = Path(__file__).parent / "raw"
OUT_DIR.mkdir(exist_ok=True)


def parse_insert_block(sql_text, table_name):
    """
    Extract all rows from INSERT INTO `table_name` ... VALUES blocks.
    Returns list of dicts keyed by column names from the INSERT header.
    """
    # Find column names from the first INSERT for this table
    col_pattern = re.compile(
        rf"INSERT INTO `{re.escape(table_name)}` \(([^)]+)\)\s*VALUES",
        re.IGNORECASE,
    )
    first_match = col_pattern.search(sql_text)
    if not first_match:
        return []

    raw_cols = first_match.group(1)
    columns = [c.strip().strip("`") for c in raw_cols.split(",")]

    rows = []
    # Find every VALUES block for this table
    values_pattern = re.compile(
        rf"INSERT INTO `{re.escape(table_name)}` \([^)]+\)\s*VALUES\s*(.*?);\s*(?=INSERT|UNLOCK|/\*|$)",
        re.IGNORECASE | re.DOTALL,
    )
    for block_match in values_pattern.finditer(sql_text):
        block = block_match.group(1).strip()
        # Split individual row tuples — careful with commas inside strings
        row_tuples = split_value_tuples(block)
        for tup in row_tuples:
            vals = parse_row_values(tup)
            if len(vals) == len(columns):
                rows.append(dict(zip(columns, vals)))
            elif vals:
                # Pad or truncate gracefully
                row = {}
                for i, col in enumerate(columns):
                    row[col] = vals[i] if i < len(vals) else None
                rows.append(row)
    return rows


def split_value_tuples(block):
    """Split a VALUES block into individual (...) tuple strings."""
    tuples = []
    depth = 0
    in_string = False
    escape_next = False
    current = []
    i = 0
    while i < len(block):
        ch = block[i]
        if escape_next:
            current.append(ch)
            escape_next = False
            i += 1
            continue
        if ch == "\\" and in_string:
            escape_next = True
            current.append(ch)
            i += 1
            continue
        if ch == "'" and not escape_next:
            in_string = not in_string
            current.append(ch)
            i += 1
            continue
        if not in_string:
            if ch == "(":
                depth += 1
                if depth == 1:
                    current = ["("]
                    i += 1
                    continue
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    current.append(")")
                    tuples.append("".join(current))
                    current = []
                    i += 1
                    continue
        current.append(ch)
        i += 1
    return tuples


def parse_row_values(tup):
    """Parse a single row tuple string like (1,'foo',NULL,3.14) into a list."""
    # Strip outer parens
    inner = tup.strip()
    if inner.startswith("("):
        inner = inner[1:]
    if inner.endswith(")"):
        inner = inner[:-1]

    values = []
    current = []
    in_string = False
    escape_next = False
    i = 0
    while i < len(inner):
        ch = inner[i]
        if escape_next:
            current.append(ch)
            escape_next = False
            i += 1
            continue
        if ch == "\\" and in_string:
            escape_next = True
            current.append(ch)
            i += 1
            continue
        if ch == "'":
            if in_string:
                # Check for escaped quote ''
                if i + 1 < len(inner) and inner[i + 1] == "'":
                    current.append("'")
                    i += 2
                    continue
                in_string = False
            else:
                in_string = True
            i += 1
            continue
        if not in_string and ch == ",":
            values.append(coerce_value("".join(current).strip()))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    if current or inner.endswith(","):
        values.append(coerce_value("".join(current).strip()))
    return values


def coerce_value(s):
    if s.upper() == "NULL":
        return None
    # Try int
    try:
        return int(s)
    except ValueError:
        pass
    # Try float
    try:
        return float(s)
    except ValueError:
        pass
    return s


def main():
    print(f"Reading {SQL_FILE} ...")
    sql_text = SQL_FILE.read_text(encoding="utf-8")

    for table in ["sports", "players", "player_stats"]:
        print(f"Parsing {table} ...")
        rows = parse_insert_block(sql_text, table)
        out_path = OUT_DIR / f"{table}.json"
        out_path.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
        print(f"  -> {len(rows)} rows written to {out_path}")


if __name__ == "__main__":
    main()
