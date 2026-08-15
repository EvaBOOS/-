"""Lightweight SQLite/Postgres column patches for evolving models."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def ensure_schema_patches(engine: AsyncEngine, database_url: str) -> None:
    """Add new columns if missing (create_all does not alter existing tables)."""
    is_sqlite = database_url.startswith("sqlite")
    # SQLite accepts the MySQL/SQLite-ism DATETIME; Postgres does not (needs
    # TIMESTAMPTZ). Postgres also has no int->boolean cast (BOOLEAN DEFAULT 0
    # fails there) — only the TRUE/FALSE keywords work, and those are valid
    # on both engines, so use them unconditionally instead of 0/1.
    _dt_type = "DATETIME" if is_sqlite else "TIMESTAMPTZ"
    patches = [
        ("video_generations", "mode", "VARCHAR(32) DEFAULT 'avatar'"),
        ("video_generations", "source_video_path", "VARCHAR(500)"),
        ("trend_insights", "hashtags", "JSON"),
        ("client_branding", "custom_music_path", "VARCHAR(500)"),
        ("client_branding", "custom_music_name", "VARCHAR(120)"),
        # SQLAlchemy's Enum(AccountType) stores/reads by member *name*
        # (COMPANY/BLOGGER), not .value — the default here must match that,
        # not the lowercase value used in the Python/JSON layer.
        ("clients", "account_type", "VARCHAR(20) DEFAULT 'COMPANY'"),
        ("clients", "discount_percent", "INTEGER DEFAULT 0"),
        ("clients", "offer_notes", "TEXT"),
        ("client_branding", "subtitle_emphasis_style", "VARCHAR(20) DEFAULT 'color'"),
        ("client_branding", "subtitle_accent_color", "VARCHAR(20)"),
        ("clients", "terms_accepted_at", _dt_type),
        ("clients", "marketing_opt_in", "BOOLEAN DEFAULT FALSE"),
        ("client_applications", "terms_accepted_at", _dt_type),
        ("clients", "moderation_strikes", "INTEGER DEFAULT 0"),
        ("clients", "last_strike_at", _dt_type),
        ("clients", "moderation_frozen_at", _dt_type),
    ]

    async with engine.begin() as conn:
        for table, column, col_type in patches:
            exists = await _column_exists(conn, table, column, is_sqlite)
            if exists:
                continue
            await conn.execute(
                text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            )

    if not is_sqlite:
        conn = await engine.connect()
        try:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await _ensure_pg_enum_values(
                conn,
                ["generationstatus", "generation_status"],
                ["moderation_hold", "blocked", "MODERATION_HOLD", "BLOCKED"],
            )
        finally:
            await conn.close()


async def _ensure_pg_enum_values(conn, type_names: list[str], values: list[str]) -> None:
    """Add new labels to an existing Postgres ENUM (SQLAlchemy native enums)."""
    for type_name in type_names:
        exists = await conn.execute(
            text("SELECT 1 FROM pg_type WHERE typname = :n"),
            {"n": type_name},
        )
        if not exists.first():
            continue
        current = await conn.execute(
            text(
                "SELECT enumlabel FROM pg_enum "
                "JOIN pg_type ON pg_enum.enumtypid = pg_type.oid "
                "WHERE typname = :n"
            ),
            {"n": type_name},
        )
        have = {row[0] for row in current.fetchall()}
        for val in values:
            if val in have:
                continue
            await conn.execute(text(f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{val}'"))


async def _column_exists(conn, table: str, column: str, is_sqlite: bool) -> bool:
    if is_sqlite:
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        rows = result.fetchall()
        return any(r[1] == column for r in rows)

    result = await conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    )
    return result.first() is not None
