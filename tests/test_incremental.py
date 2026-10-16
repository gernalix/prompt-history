from __future__ import annotations

import concurrent.futures
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from prompt_history import adapters as a
from prompt_history.store import connect, init_db, ingest_record, migrate_fts, SCHEMA_PATH
from prompt_history.maintenance import optimize


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'derived.sqlite'
        self.c = connect(self.db)
        self.addCleanup(self.c.close)
        init_db(self.c)
        (self.root / 'index').mkdir()
        self.index = self.root / 'index/prompts.jsonl'
        self.index.write_text('')

    def cycle(self, key='a', tokens=10):
        path = self.root / key / 'metrics.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(dict(prompt_id='123456', cycle_key=key,
            prompt_text_redacted='searchable text', status='PASS', model='gpt-sol',
            reasoning_effort='medium', input_tokens=tokens, output_tokens=2)))
        return path

    def append(self, key='a'):
        with self.index.open('a') as f:
            f.write(json.dumps(dict(path=key, cycle_key=key, prompt_id='123456'))+'\n')

    def usage(self):
        return a.ingest_codex_usage(self.c, self.root)

    def logical(self, conn):
        result = {}
        for table in ['prompts', 'executions', 'relations', 'artifacts']:
            cols = [r[1] for r in conn.execute('PRAGMA table_info('+table+')')
                    if r[1] not in {'inserted_at', 'updated_at', 'created_at'}]
            result[table] = [tuple(r) for r in conn.execute(
                'SELECT '+','.join(cols)+' FROM '+table+' ORDER BY 1')]
        return result

    def test_usage_no_change_append_late_metrics_retroactive_replacement_truncate(self):
        p = self.cycle(); self.append()
        self.assertGreater(self.usage(), 0)
        with mock.patch.object(a, 'ingest_record', side_effect=AssertionError('reprocessed')):
            self.assertEqual(self.usage(), 0)
        self.cycle(tokens=20)
        self.assertGreater(self.usage(), 0)
        self.assertEqual(self.c.execute('SELECT input_tokens FROM executions').fetchone()[0],20)
        # Return to an already observed payload must reapply it, preserving history.
        self.cycle(tokens=10); self.usage()
        self.assertEqual(self.c.execute('SELECT input_tokens FROM executions').fetchone()[0],10)
        self.cycle('b',30); self.append('b'); self.usage()
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM executions').fetchone()[0],2)
        replacement=self.root/'replacement'
        replacement.write_text(self.index.read_text())
        replacement.replace(self.index); self.usage()
        self.index.write_text(json.dumps(dict(path='a',cycle_key='a',prompt_id='123456',chat_id='edited'))+'\n')
        self.usage()
        self.assertEqual(self.c.execute('SELECT conversation_id FROM prompts').fetchone()[0],'edited')
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM usage_offsets').fetchone()[0],1)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM executions').fetchone()[0],2)
        p.unlink(); self.usage(); self.cycle(tokens=40); self.usage()
        self.assertEqual(self.c.execute("SELECT input_tokens FROM executions WHERE source_key='a'").fetchone()[0],40)

    def test_incomplete_append_rolls_back_records_and_watermarks(self):
        self.cycle();self.append();self.usage()
        before=self.logical(self.c)
        self.cycle('b');self.append('b')
        with self.index.open('a') as f:f.write('{')
        with self.assertRaises(ValueError):self.usage()
        self.assertEqual(before,self.logical(self.c))
        self.index.write_text(self.index.read_text()[:-1]);self.usage()
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM executions').fetchone()[0],2)

    def test_mid_ingest_failure_and_input_race_roll_back(self):
        self.cycle();self.append()
        original=a.ingest_record
        def fail(conn,record):
            if record['kind']=='execution':raise RuntimeError('crash')
            return original(conn,record)
        with mock.patch.object(a,'ingest_record',side_effect=fail):
            with self.assertRaises(RuntimeError):self.usage()
        for table in ['prompts','source_records','source_heads','usage_offsets','ingest_checkpoints']:
            self.assertEqual(self.c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
        def race(conn,record):
            value=original(conn,record)
            self.cycle(tokens=30)
            return value
        with mock.patch.object(a,'ingest_record',side_effect=race):
            with self.assertRaises(RuntimeError):self.usage()
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM prompts').fetchone()[0],0)
        self.assertGreater(self.usage(),0)

    def test_upgrade_does_not_replay_seen_payload_over_other_source(self):
        with self.c:
            first=dict(kind='prompt',source='roadmap',source_key='123456',prompt_id='123456',
                       prompt_text='body',title='older',text_quality=100)
            ingest_record(self.c,first)
            ingest_record(self.c,{**first,'source':'other','title':'current'})
            self.c.execute('DELETE FROM source_heads')
            self.assertFalse(ingest_record(self.c,first))
        self.assertEqual(self.c.execute('SELECT title FROM prompts').fetchone()[0],'current')

    def test_concurrent_imports_serialize_and_are_idempotent(self):
        self.cycle();self.append()
        def run(_):
            c=connect(self.db)
            try:return a.ingest_codex_usage(c,self.root)
            finally:c.close()
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            values=list(pool.map(run,range(2)))
        self.assertEqual(sorted(values),[0,2])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM source_records').fetchone()[0],2)

    def test_full_rebuild_matches_incremental_fts_links_and_analytics(self):
        self.cycle();self.append();self.usage()
        self.cycle(tokens=35);self.usage()
        self.cycle('b',50);self.append('b');self.usage()
        rebuilt=connect(self.root/'rebuilt.sqlite');self.addCleanup(rebuilt.close);init_db(rebuilt)
        a.ingest_codex_usage(rebuilt,self.root)
        self.assertEqual(self.logical(self.c),self.logical(rebuilt))
        self.assertEqual([tuple(r) for r in self.c.execute('SELECT * FROM v_model_performance')],
                         [tuple(r) for r in rebuilt.execute('SELECT * FROM v_model_performance')])
        self.assertEqual(self.c.execute("SELECT count(*) FROM prompt_fts WHERE prompt_fts MATCH 'searchable'").fetchone()[0],1)
        self.c.execute("INSERT INTO prompt_fts(prompt_fts,rank) VALUES('integrity-check',1)")
        self.c.commit()

    def exporter(self,text='PROMPT_ID=123456 initial'):
        root=self.root/'ChatGPTExport-test'
        p=root/'conversations/c/conversation.json';p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps(dict(provider='chatgpt-web',conversationId='c',messages=[
            dict(id='m',role='assistant',parts=[dict(text=text)])])))
        (root/'archive.json').write_text('{}')
        return root,p

    def test_exporter_edits_without_manifest_change_and_same_mtime_revert(self):
        root,p=self.exporter();a.ingest_chatgpt_exporter_archive(self.c,root)
        self.assertEqual(a.ingest_chatgpt_exporter_archive(self.c,root),0)
        old=p.stat();root,p=self.exporter('PROMPT_ID=654321 revised')
        os.utime(p,ns=(old.st_atime_ns,old.st_mtime_ns))
        a.ingest_chatgpt_exporter_archive(self.c,root)
        self.assertIn('revised',self.c.execute("SELECT prompt_text FROM prompts WHERE source='chatgpt'").fetchone()[0])
        root,p=self.exporter();a.ingest_chatgpt_exporter_archive(self.c,root)
        self.assertIn('initial',self.c.execute("SELECT prompt_text FROM prompts WHERE source='chatgpt'").fetchone()[0])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM link_pending').fetchone()[0],0)
        with mock.patch.object(a,'ingest_record',side_effect=AssertionError('link reprocessed')):
            self.assertEqual(a.link_explicit_prompt_ids(self.c),0)
        self.assertEqual(self.c.execute("SELECT count(*) FROM prompt_fts WHERE prompt_fts MATCH 'revised'").fetchone()[0],0)

    def test_sqlite_checkpoint_sees_wal_changes_and_schema_changes(self):
        p=self.root/'switcher.sqlite';s=sqlite3.connect(p);self.addCleanup(s.close)
        s.executescript('PRAGMA journal_mode=WAL; CREATE TABLE contexts(id TEXT,url TEXT,title TEXT,note TEXT); '
            'CREATE TABLE prompt_bindings(prompt_id TEXT,context_id TEXT,codex_thread TEXT,codex_deep_link TEXT); '
            "INSERT INTO contexts VALUES('c','url','title','note'); "
            "INSERT INTO prompt_bindings VALUES('123456','c','uuid','link')")
        s.commit()
        self.assertGreater(a.ingest_switcher(self.c,p),0)
        with mock.patch.object(a.sqlite3,'connect',side_effect=AssertionError('reopened source')):
            self.assertEqual(a.ingest_switcher(self.c,p),0)
        s.execute("UPDATE contexts SET note='changed'");s.commit()
        self.assertGreater(a.ingest_switcher(self.c,p),0)
        s.execute('ALTER TABLE contexts ADD COLUMN extra TEXT');s.commit()
        self.assertEqual(a.ingest_switcher(self.c,p),0)

    def test_roadmap_materialization_is_streamed_and_revised(self):
        p=self.root/'roadmap.sqlite';s=sqlite3.connect(p);self.addCleanup(s.close)
        s.executescript("CREATE TABLE prompts(prompt_id TEXT,title TEXT); "
            "CREATE TABLE prompt_materializations(prompt_id TEXT,body TEXT); "
            "INSERT INTO prompts VALUES('123456','title'); "
            "INSERT INTO prompt_materializations VALUES('123456','first body')")
        a.ingest_roadmap(self.c,p)
        self.assertEqual(a.ingest_roadmap(self.c,p),0)
        s.execute("UPDATE prompt_materializations SET body='changed body'");s.commit()
        a.ingest_roadmap(self.c,p)
        self.assertEqual(self.c.execute('SELECT prompt_text FROM prompts').fetchone()[0],'changed body')

    def test_interrupted_maintenance_blocks_reads_until_verified_recovery(self):
        with self.c:
            ingest_record(self.c,dict(kind='prompt',source='test',source_key='one',
                                     prompt_text='recovery searchable',text_quality=100))
            self.c.execute("INSERT INTO meta VALUES('fts_rebuild_pending','1')")
        with self.assertRaisesRegex(RuntimeError,'incomplete SQLite maintenance'):
            connect(self.db)
        optimize(self.db,self.root/'recovery-backup.sqlite')
        restored=connect(self.db)
        try:
            self.assertEqual(restored.execute("SELECT count(*) FROM prompt_fts WHERE prompt_fts MATCH 'recovery'").fetchone()[0],1)
        finally:restored.close()

    def test_legacy_fts_migration_backup_and_compaction_preserve_data(self):
        legacy=self.root/'legacy.sqlite';c=connect(legacy);self.addCleanup(c.close)
        schema=SCHEMA_PATH.read_text().replace("  content='prompts',\n  content_rowid='rowid',\n",'')
        c.executescript(schema);init_db(c)
        with c:
            for i in range(5):
                ingest_record(c,dict(kind='prompt',source='test',source_key=str(i),
                    prompt_text=('distinctive searchable words '*500),text_quality=100))
        before=self.logical(c)
        backup=self.root/'backup.sqlite'
        report=optimize(legacy,backup)
        self.assertTrue(report['migrated'])
        self.assertEqual(before,self.logical(c))
        self.assertEqual(backup.stat().st_mode & 0o777,0o600)
        self.assertFalse(c.execute("SELECT 1 FROM sqlite_master WHERE name='prompt_fts_content'").fetchone())
        self.assertEqual(c.execute("SELECT count(*) FROM prompt_fts WHERE prompt_fts MATCH 'distinctive'").fetchone()[0],5)
        c.execute("DELETE FROM prompts WHERE source_key='1'");c.commit()
        c.execute('VACUUM')
        # Maintenance rebuilds the external index after VACUUM (rowids may change).
        c.execute("INSERT INTO prompt_fts(prompt_fts) VALUES('rebuild')")
        c.execute("INSERT INTO prompt_fts(prompt_fts,rank) VALUES('integrity-check',1)");c.commit()
        with self.assertRaises(ValueError):optimize(legacy,backup)


if __name__=='__main__':unittest.main()
