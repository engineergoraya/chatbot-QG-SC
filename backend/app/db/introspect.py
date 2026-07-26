"""
introspect.py — live schema discovery.

The chatbot must never hardcode table or column names. Instead it reads them
from the live database via information_schema every time it starts, so:

  * newly-loaded tables/views appear automatically — no code change needed;
  * the SQL guard can validate every table a generated query references
    against what actually exists;
  * the LLM prompt is built from the real schema, so the model can't invent
    columns that aren't there.

Nothing here executes user/model SQL. It runs fixed metadata queries only,
over the read-only connection.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import psycopg2

from app import config


@dataclass
class Column:
    name: str
    data_type: str
    nullable: bool


@dataclass
class Table:
    name: str
    kind: str  # 'BASE TABLE' or 'VIEW'
    columns: list[Column] = field(default_factory=list)

    @property
    def column_names(self) -> set[str]:
        return {c.name for c in self.columns}


@dataclass
class Schema:
    tables: dict[str, Table]

    @property
    def table_names(self) -> set[str]:
        return set(self.tables.keys())

    def has_table(self, name: str) -> bool:
        return name.lower() in self.tables

    def has_column(self, table: str, column: str) -> bool:
        t = self.tables.get(table.lower())
        return bool(t and column.lower() in {c.lower() for c in t.column_names})

    def to_prompt_text(self) -> str:
        """Render the schema as compact DDL-ish text for the LLM prompt."""
        lines: list[str] = []
        for tname in sorted(self.tables):
            t = self.tables[tname]
            tag = "VIEW" if t.kind == "VIEW" else "TABLE"
            lines.append(f"{tag} {t.name} (")
            col_lines = [
                f"    {c.name} {c.data_type}{'' if c.nullable else ' NOT NULL'}"
                for c in t.columns
            ]
            lines.append(",\n".join(col_lines))
            lines.append(");")
            lines.append("")
        return "\n".join(lines).strip()


_EXCLUDED_TABLES: set[str] = set()

_cached_schema: Schema | None = None


def introspect(conn=None, use_cache: bool = True) -> Schema:
    """Read all base tables and views (with columns) from the public schema."""
    global _cached_schema
    if use_cache and _cached_schema is not None:
        return _cached_schema

    own_conn = conn is None
    if own_conn:
        conn = psycopg2.connect(**config.readonly_dsn())

    try:
        tables: dict[str, Table] = {}
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name, table_type
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type IN ('BASE TABLE', 'VIEW')
                ORDER BY table_name
                """
            )
            for name, ttype in cur.fetchall():
                if name.lower() in _EXCLUDED_TABLES:
                    continue
                tables[name.lower()] = Table(name=name, kind=ttype)

            cur.execute(
                """
                SELECT table_name, column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                ORDER BY table_name, ordinal_position
                """
            )
            for tname, cname, dtype, is_nullable in cur.fetchall():
                key = tname.lower()
                if key in tables:
                    tables[key].columns.append(
                        Column(
                            name=cname,
                            data_type=dtype,
                            nullable=(is_nullable == "YES"),
                        )
                    )
        schema = Schema(tables=tables)
        if use_cache:
            _cached_schema = schema
        return schema
    finally:
        if own_conn:
            conn.close()
