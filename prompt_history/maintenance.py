"""Explicit, backed-up maintenance of the rebuildable warehouse only."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3

from .store import connect, init_db, migrate_fts


def db_stats(conn):
    return {
        'bytes': conn.execute('PRAGMA page_count').fetchone()[0] * conn.execute('PRAGMA page_size').fetchone()[0],
        'freelist_pages': conn.execute('PRAGMA freelist_count').fetchone()[0],
        'objects': [dict(r) for r in conn.execute(
            'SELECT name,SUM(pgsize) AS bytes,SUM(payload) AS payload,SUM(unused) AS unused '
            'FROM dbstat GROUP BY name ORDER BY bytes DESC')],
    }


def verify(conn):
    if [r[0] for r in conn.execute('PRAGMA integrity_check')] != ['ok']:
        raise RuntimeError('SQLite integrity check failed')
    if conn.execute('PRAGMA foreign_key_check').fetchone():
        raise RuntimeError('SQLite foreign key check failed')


def optimize(db_path, backup_path):
    db = Path(db_path).expanduser().resolve()
    backup = Path(backup_path).expanduser().resolve()
    if db == backup or backup.exists():
        raise ValueError('backup must be a new, separate file')
    required = db.stat().st_size * 3
    for directory in {db.parent, backup.parent}:
        if shutil.disk_usage(directory).free < required:
            raise RuntimeError('insufficient free space for backup, rebuild and VACUUM')
    conn = connect(db, maintenance=True)
    try:
        verify(conn)
        before = db_stats(conn)
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        dest = sqlite3.connect(backup)
        try:
            conn.backup(dest)
            verify(dest)
        finally:
            dest.close()
        # Keep readers/writers out across VACUUM's implicit transaction and the
        # following index rebuild; release this connection only after readback.
        conn.execute('PRAGMA busy_timeout=120000')
        conn.execute('PRAGMA locking_mode=EXCLUSIVE')
        conn.execute('BEGIN EXCLUSIVE')
        conn.commit()
        # Rebuild only redundant FTS storage; original messages/provenance stay.
        init_db(conn)
        conn.execute("INSERT INTO meta VALUES('fts_rebuild_pending','1') "
                     "ON CONFLICT(key) DO UPDATE SET value='1'")
        conn.commit()
        migrated = migrate_fts(conn)
        conn.execute('VACUUM')
        # VACUUM may renumber rowids in tables without an INTEGER PRIMARY KEY.
        conn.execute("INSERT INTO prompt_fts(prompt_fts) VALUES('rebuild')")
        conn.commit()
        verify(conn)
        conn.execute("INSERT INTO prompt_fts(prompt_fts,rank) VALUES('integrity-check',1)")
        conn.execute("UPDATE meta SET value='0' WHERE key='fts_rebuild_pending'")
        conn.commit()
        return dict(before=before, after=db_stats(conn), migrated=migrated, backup=str(backup))
    finally:
        conn.close()
