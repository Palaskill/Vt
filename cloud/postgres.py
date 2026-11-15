"""Small compatibility adapter for the existing, server-side business rules.

Connections are short lived and each transaction establishes its own search_path,
which also works with a transaction-mode pooler. No database credentials reach JS.
"""
import os
import re
from decimal import Decimal

import psycopg2
from psycopg2.extras import DictCursor

INSERT_ID_TABLES = {
    'users', 'medicines', 'lots', 'suppliers', 'orders', 'order_lines', 'receipts',
    'supplier_invoices', 'supplier_payments', 'sales', 'sale_lines', 'sale_allocations',
    'sale_payments', 'audit',
}
LOCK_ID = 0x56657453746F636B


def _placeholders(sql):
    """Convert bind markers without touching question marks in SQL string literals."""
    out = []
    single = False
    double = False
    index = 0
    while index < len(sql):
        char = sql[index]
        if char == "'" and not double:
            if single and index + 1 < len(sql) and sql[index + 1] == "'":
                out.extend(("'", "'")); index += 2; continue
            single = not single
        elif char == '"' and not single:
            double = not double
        out.append('%s' if char == '?' and not single and not double else char)
        index += 1
    return ''.join(out)


def translate(sql):
    sql = sql.strip()
    if sql.upper() == 'BEGIN IMMEDIATE':
        return f'SELECT pg_advisory_xact_lock({LOCK_ID})', False
    sql = re.sub(r'\b(m\.name|name)\s+COLLATE\s+NOCASE\b', r'lower(\1)', sql, flags=re.I)
    if re.match(r'INSERT\s+OR\s+REPLACE\s+INTO\s+settings\s*\(', sql, re.I):
        sql = re.sub(r'INSERT\s+OR\s+REPLACE', 'INSERT', sql, count=1, flags=re.I)
        sql += ' ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value'
    match = re.match(r'INSERT\s+INTO\s+([a-z_]+)\s*\(', sql, re.I)
    returns_id = bool(match and match.group(1).lower() in INSERT_ID_TABLES)
    if returns_id:
        sql += ' RETURNING id'
    return _placeholders(sql), returns_id


def _convert(value):
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise ValueError('Unexpected fractional database value')
        return int(value)
    return bytes(value) if isinstance(value, memoryview) else value


class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Result:
    def __init__(self, cursor, returns_id=False):
        self.cursor = cursor
        self.lastrowid = cursor.fetchone()[0] if returns_id else None

    @staticmethod
    def _row(row):
        return None if row is None else Row((key, _convert(value)) for key, value in dict(row).items())

    def fetchone(self):
        return self._row(self.cursor.fetchone())

    def fetchall(self):
        return [self._row(row) for row in self.cursor.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class Connection:
    def __init__(self, raw):
        self.raw = raw
        self.ready = False

    def execute(self, sql, params=()):
        if not self.ready:
            with self.raw.cursor() as cursor:
                cursor.execute('SET LOCAL search_path TO vetstock, pg_catalog')
            self.ready = True
        statement, returns_id = translate(sql)
        cursor = self.raw.cursor(cursor_factory=DictCursor)
        cursor.execute(statement, tuple(params))
        return Result(cursor, returns_id)

    def commit(self):
        self.raw.commit(); self.ready = False

    def rollback(self):
        self.raw.rollback(); self.ready = False

    def __enter__(self):
        return self

    def __exit__(self, typ, value, tb):
        try:
            if typ is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.raw.close()


def db():
    url = os.environ.get('DATABASE_URL', '')
    ca = os.environ.get('VETSTOCK_DATABASE_CA_PATH', '')
    if not url or not ca:
        raise RuntimeError('DATABASE_URL and VETSTOCK_DATABASE_CA_PATH are required')
    # The CA certificate must be supplied separately. Never downgrade TLS.
    return Connection(psycopg2.connect(url, connect_timeout=10, sslmode='verify-full', sslrootcert=ca))
