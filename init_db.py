"""
init_db.py
==========
Creates the database.

    python init_db.py            create anything missing
    python init_db.py --fresh    delete the database first

--fresh removes every account and everything in them.
"""

from __future__ import annotations

import argparse

from config import DB_PATH
from db import ensure_schema, init_schema, open_connection


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the Secretary database.")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="delete the existing database first, removing every account",
    )
    arguments = parser.parse_args()

    if arguments.fresh and DB_PATH.exists():
        for suffix in ("", "-wal", "-shm"):
            companion = DB_PATH.with_name(DB_PATH.name + suffix)
            if companion.exists():
                companion.unlink()
        print(f"removed {DB_PATH}")

    init_schema()
    ensure_schema()

    connection = open_connection()
    try:
        tables = [
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
        users = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        connection.close()

    print(f"database    {DB_PATH}")
    print(f"journal     {journal}")
    print(f"tables      {len(tables)}: {', '.join(tables)}")
    print(f"accounts    {users}")
    if users == 0:
        print("\nThe first account registered needs no invite code and becomes admin.")


if __name__ == "__main__":
    main()
