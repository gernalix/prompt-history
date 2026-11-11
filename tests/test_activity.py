import json
from datetime import date
from pathlib import Path
import sqlite3
import tempfile
import unittest

from prompt_history.activity import aggregate, summarize, classify
from prompt_history.chatgpt_adapters import activity_text_length
from prompt_history.store import connect, init_db, ingest_record


class ActivityTests(unittest.TestCase):
    def test_visibility(self):
        message = {'recipient':'all','parts':[{'kind':'text','text':'hello'},
                    {'kind':'reasoning_summary','text':'hidden'},
                    {'kind':'tool_result','text':'tool'}]}
        self.assertEqual(activity_text_length(message), 5)
        message['extensions']={'chatgpt':{'channel':'analysis'}}
        self.assertEqual(activity_text_length(message),0)
        message['extensions']={'chatgpt':{'metadata':{'is_visually_hidden_from_conversation':True}}}
        self.assertEqual(activity_text_length(message),0)

    def test_canonical_mirrors_timezone_and_repeated_messages(self):
        with tempfile.TemporaryDirectory() as temp:
            db=Path(temp)/'history.sqlite'; threads=Path(temp)/'threads.sqlite'
            with sqlite3.connect(threads) as conn:
                conn.execute('CREATE TABLE threads(id,source,originator)')
                conn.executemany('INSERT INTO threads VALUES(?,?,?)',[
                    ('desktop','vscode','Codex Desktop'),('cli','cli',None),('worker','exec','codex_exec')])
            conn=connect(db); init_db(conn)
            def put(source,key,conversation,role,text,when,metadata=None):
                ingest_record(conn,dict(kind='prompt',source=source,source_key=key,conversation_id=conversation,
                    message_id=key,role=role,prompt_text=text,created_at=when,metadata=metadata or {}))
            # Midnight Copenhagen, UTC previous day. Repeated identical text is real activity.
            for i in range(2):
                put('codex-native-history',f'n{i}','desktop','user','hello','2026-11-09T23:30:00Z')
                put('codex-session-bandit',f'b{i}','desktop','user','hello','2026-11-09T23:30:01Z')
            put('codex-native-history','a','desktop','assistant','answer','2026-11-10T01:30:00Z')
            put('codex-session-bandit','cli','cli','assistant','CLI','2026-11-10T02:00:00Z')
            put('codex-session-bandit','worker','worker','assistant','automation','2026-11-10T02:00:00Z')
            put('chatgpt','web','web','user','hello','2026-11-10T04:00:00Z',{'activity_chars':5})
            put('chatgpt','hidden','web','assistant','hidden','2026-11-10T04:00:00Z',{'activity_chars':0})
            put('chatgpt','pending','web','assistant','unknown','2026-11-10T04:00:00Z')
            put('codex-native-history','tool','desktop','tool','private tool','2026-11-10T01:00:00Z')
            conn.commit();conn.close()
            data=aggregate(db,threads,today=date(2026,11,10))
            total=summarize(data,'7','both','characters')
            self.assertEqual(total['total'],24)
            self.assertEqual(total['active_days'],1)
            self.assertEqual(total['days'][-1]['desktop'],16)
            self.assertEqual(data['quality']['mirrors_excluded'],2)
            self.assertEqual(summarize(data,'7','both','messages')['total'],5)
            self.assertEqual(summarize(data,'7','both','conversations')['total'],3)
            self.assertEqual(summarize(data,'7','user','characters')['total'],15)
            self.assertNotIn('hello',json.dumps(data))
            self.assertNotIn('desktop:user',json.dumps(data))

    def test_rolling_mean_uses_preceding_period_and_zero_days(self):
        data={'today':'2026-11-10','rows':[
            {'day':'2026-11-03','platform':'cli','role':'user','characters':70,'messages':1,'conversation_ids':[1]},
            {'day':'2026-11-04','platform':'cli','role':'user','characters':7,'messages':1,'conversation_ids':[1]},
            {'day':'2026-11-10','platform':'cli','role':'assistant','characters':7,'messages':1,'conversation_ids':[1]}]}
        result=summarize(data,'7')
        self.assertEqual(len(result['days']),7)
        self.assertEqual(result['days'][0]['average7'],11)
        self.assertEqual(result['days'][-1]['average7'],2)
        self.assertEqual(summarize(data,'7','both','conversations')['total'],1)
        with self.assertRaises(ValueError):summarize(data,'-1')

    def test_origin_requires_evidence(self):
        self.assertEqual(classify(('vscode',None)),'unknown')
        self.assertEqual(classify(('cli',None)),'cli')
        self.assertIsNone(classify(('{"subagent":{}}',None)))
        self.assertIsNone(classify(('vscode','symphony-orchestrator')))


if __name__=='__main__':unittest.main()
