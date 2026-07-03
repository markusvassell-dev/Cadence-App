"""Applies the database schema to DATABASE_URL.

Usage: python -m scripts.init_db

Applies handoff/SCHEMA.sql (the canonical schema) and then
handoff/addendum/SCHEMA_additions.sql (the `markets` table the app seeds on
startup). Run this once against a fresh database before the app boots.
"""

import asyncio
import pathlib

import asyncpg

from app.config import get_settings

# This file lives at <root>/scripts/init_db.py; the schema lives at <root>/handoff.
# (Flat deploy layout: the `app` package sits at the deploy root, one level up.)
ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA_FILES = [
    ROOT / "handoff" / "SCHEMA.sql",
    ROOT / "handoff" / "addendum" / "SCHEMA_additions.sql",
]


async def main() -> None:
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        for path in SCHEMA_FILES:
            await conn.execute(path.read_text())
            print(f"Applied schema from {path}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
