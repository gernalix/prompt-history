"""Disposable importer checkpoints, committed with the derived records they cover."""
from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import json
from pathlib import Path
import sqlite3


@contextmanager
def atomic(conn: sqlite3.Connection):
    # Importers nest; only the outer owner commits. BEGIN IMMEDIATE serializes
    # competing writers before either one observes a checkpoint.
    owner = not conn.in_transaction
    if owner:
        conn.execute('BEGIN IMMEDIATE')
    try:
        yield
    except BaseException:
        if owner:
            conn.rollback()
        raise
    else:
        if owner:
            conn.commit()


def stamp(path: Path, *, sqlite: bool = False) -> str:
    paths = [path, Path(str(path) + '-wal')] if sqlite else [path]
    values = []
    for p in paths:
        try:
            s = p.stat()
            values.append([s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns])
        except FileNotFoundError:
            values.append(None)
    return json.dumps(values, separators=(',', ':'))


def get(conn, key):
    row = conn.execute('SELECT value FROM ingest_checkpoints WHERE key=?', (key,)).fetchone()
    return row[0] if row else None


def put(conn, key, value):
    conn.execute('INSERT INTO ingest_checkpoints(key,value) VALUES(?,?) '
                 'ON CONFLICT(key) DO UPDATE SET value=excluded.value WHERE value<>excluded.value', (key, value))


def checkpointed(*, sqlite: bool = False):
    def decorate(fn):
        @wraps(fn)
        def run(conn, path):
            path = Path(path).expanduser().resolve()
            key = f'v1:{fn.__name__}:{path}'
            with atomic(conn):
                before = stamp(path, sqlite=sqlite)
                if get(conn, key) == before:
                    return 0
                count = fn(conn, path)
                if stamp(path, sqlite=sqlite) != before:
                    raise RuntimeError(f'input changed during ingestion: {path}')
                put(conn, key, before)
                return count
        return run
    return decorate


def transactional(fn):
    @wraps(fn)
    def run(conn, *args, **kwargs):
        with atomic(conn):
            return fn(conn, *args, **kwargs)
    return run
