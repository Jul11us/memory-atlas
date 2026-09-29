import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from memory_atlas import (ROOT, Atlas, AtlasHandler, FeedbackStore, Memory, Network,
                          ThreadingHTTPServer, memory_lifecycle, rank_memories)


class DecayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.atlas = Atlas(ROOT / 'examples' / 'demo-memories', self.root / 'state.sqlite3', home=self.root)
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.memory = self.atlas.memories[0]

    def tearDown(self):
        self.temp.cleanup()

    def age(self, identifier, days):
        value = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.atlas.store.connect() as db:
            db.execute('UPDATE memory_activity SET first_seen=?, last_used=NULL WHERE memory_id=?', (value, identifier))

    def test_exact_boundary_and_linear_fade(self):
        activity = {'first_seen': self.now.isoformat(), 'last_used': None}
        half = memory_lifecycle(activity, {}, 90, self.now + timedelta(days=45))
        self.assertEqual((half['retention'], half['days_remaining'], half['dormant']), (.5, 45, False))
        before = memory_lifecycle(activity, {}, 90, self.now + timedelta(days=90, microseconds=-1))
        after = memory_lifecycle(activity, {}, 90, self.now + timedelta(days=90))
        self.assertFalse(before['dormant'])
        self.assertEqual((after['retention'], after['days_remaining'], after['dormant']), (0, 0, True))

    def test_existing_old_source_dates_start_at_first_import(self):
        self.assertTrue(any(item.date == '2026-01-01' for item in self.atlas.memories))
        self.assertFalse(any(row['dormant'] for row in self.atlas.lifecycle().values()))
        self.assertEqual(self.atlas.store.settings(), {'decay_days': 90})

    def test_search_reload_and_restart_never_renew(self):
        self.age(self.memory.id, 91)
        before = self.atlas.store.activity()[self.memory.id]
        self.atlas.state()
        self.atlas.search('')
        self.atlas.reload()
        restored = Atlas(self.atlas.source, self.atlas.store.path, home=self.root)
        self.assertEqual(restored.store.activity()[self.memory.id], before)
        self.assertTrue(restored.lifecycle()[self.memory.id]['dormant'])
        self.assertNotIn(self.memory.id, [row['id'] for row in restored.search('')['results']])

    def test_explicit_use_restores_expired_memory_and_preserves_source(self):
        source = Path(self.memory.source)
        before = source.read_bytes()
        self.age(self.memory.id, 91)
        result = self.atlas.use_memory(self.memory.id)
        self.assertFalse(result['lifecycle']['dormant'])
        self.assertGreater(result['lifecycle']['retention'], .999)
        self.assertIsNotNone(result['lifecycle']['last_used'])
        self.assertIn(self.memory.id, [row['id'] for row in self.atlas.search('')['results']])
        self.assertEqual(source.read_bytes(), before)

    def test_fixed_memories_and_disabled_decay_are_exempt(self):
        self.age(self.memory.id, 1000)
        self.atlas.store.change(self.memory.id, 'pin')
        self.assertEqual(self.atlas.lifecycle()[self.memory.id]['retention'], 1)
        self.atlas.store.change(self.memory.id, 'pin')
        self.assertTrue(self.atlas.lifecycle()[self.memory.id]['dormant'])
        self.atlas.store.set_decay_days(0)
        self.assertEqual(self.atlas.lifecycle()[self.memory.id]['exempt'], 'disabled')
        self.assertEqual(self.atlas.lifecycle()[self.memory.id]['retention'], 1)

    def test_new_setting_reuses_clock_and_persists(self):
        self.age(self.memory.id, 45)
        self.atlas.store.set_decay_days(30)
        self.assertTrue(self.atlas.lifecycle()[self.memory.id]['dormant'])
        self.atlas.store.set_decay_days(180)
        self.assertFalse(self.atlas.lifecycle()[self.memory.id]['dormant'])
        self.assertEqual(FeedbackStore(self.atlas.store.path).settings()['decay_days'], 180)

    def test_invalid_settings_and_unknown_use_do_not_write(self):
        for value in [-1, 3651, 1.5, '90', None, True]:
            with self.assertRaises(ValueError):
                self.atlas.store.set_decay_days(value)
        before = self.atlas.store.activity()
        with self.assertRaises(ValueError):
            self.atlas.use_memory('missing')
        self.assertEqual(self.atlas.store.settings()['decay_days'], 90)
        self.assertEqual(self.atlas.store.activity(), before)

    def test_dormant_intermediate_cannot_spread_or_support_neighbours(self):
        memories = [Memory(i, text, text, 'tip', 'P', 'fictional.md', 1, '2026-01-01', .7)
                    for i, text in [('a', 'seed'), ('b', 'bridge'), ('c', 'destination')]]
        network = Network(memories, self.atlas.store)
        network.edges = {('a', 'b'): {'w': 1, 'pruned': 0}, ('b', 'c'): {'w': 1, 'pruned': 0}}
        life = {'a': {'retention': 1}, 'b': {'retention': 0, 'dormant': True}, 'c': {'retention': .5}}
        plain = rank_memories(memories, {}, 'seed', network)
        expired = rank_memories(memories, {'b': {'boost': 3}}, 'seed', network, life)
        self.assertIn('c', [row['id'] for row in plain])
        self.assertEqual([row['id'] for row in expired], ['a'])
        ranked = {row['id']: row for row in rank_memories(memories, {'b': {'boost': 3}}, '', network, life)}
        self.assertEqual(ranked['c']['support'], 0)
        self.assertEqual(ranked['c']['retention'], .5)

    def test_recall_renews_only_returned_memories_and_uses_corrections(self):
        target = next(item for item in self.atlas.memories if item.kind == 'task')
        self.atlas.feedback(target.id, 'correct', 'exclusive fictional-keyword')
        before = self.atlas.store.activity()
        response = self.atlas.recall('fictional-keyword', 1)
        self.assertEqual(response['renewed'], 1)
        self.assertEqual(response['results'][0]['id'], target.id)
        self.assertEqual(response['results'][0]['text'], 'exclusive fictional-keyword')
        after = self.atlas.store.activity()
        self.assertTrue(all(after[i] == row for i, row in before.items() if i != target.id))
        self.assertEqual(response['results'][0]['source'], target.source)
        self.assertEqual(response['results'][0]['source_line'], target.source_line)

    def test_expired_memories_are_not_renewed_by_recall(self):
        for memory in self.atlas.memories:
            self.age(memory.id, 91)
        before = self.atlas.store.activity()
        response = self.atlas.recall('deployment')
        self.assertEqual(response['results'], [])
        self.assertEqual(response['renewed'], 0)
        self.assertEqual(self.atlas.store.activity(), before)

    def test_invalid_recall_does_not_renew(self):
        before = self.atlas.store.activity()
        for query, limit in [('', 5), ('x'*201, 5), (None, 5), ('deployment', 0), ('deployment', 31), ('deployment', True)]:
            with self.assertRaises(ValueError):
                self.atlas.recall(query, limit)
        self.assertEqual(self.atlas.store.activity(), before)

    def test_migration_keeps_feedback_and_clock_never_moves_backwards(self):
        self.atlas.feedback(self.memory.id, 'boost')
        future = datetime.now(timezone.utc) + timedelta(days=1)
        self.atlas.store.use_memories([self.memory.id], future)
        self.atlas.store.use_memories([self.memory.id], datetime.now(timezone.utc))
        restored = FeedbackStore(self.atlas.store.path)
        self.assertEqual(restored.all()[self.memory.id]['boost'], 1)
        self.assertEqual(restored.activity()[self.memory.id]['last_used'], future.isoformat())

    def test_old_database_migration_preserves_saved_correction(self):
        legacy = self.root / 'legacy.sqlite3'
        with closing(sqlite3.connect(legacy)) as db, db:
            db.execute('CREATE TABLE feedback (memory_id TEXT PRIMARY KEY, boost INTEGER, pinned INTEGER, archived INTEGER, correction TEXT, updated_at TEXT)')
            db.execute('INSERT INTO feedback VALUES (?, 2, 1, 0, ?, ?)',
                       (self.memory.id, 'fictional saved correction', self.now.isoformat()))
        upgraded = FeedbackStore(legacy)
        self.assertEqual(upgraded.all()[self.memory.id]['correction'], 'fictional saved correction')
        self.assertEqual(upgraded.all()[self.memory.id]['boost'], 2)
        self.assertEqual(upgraded.settings(), {'decay_days': 90})
        self.assertEqual(upgraded.activity(), {})

    def test_http_api_and_example_client(self):
        from examples.recall import recall
        handler = type('TestHandler', (AtlasHandler,), {'atlas': self.atlas})
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            def post(path, payload, origin=None):
                headers = {'Content-Type': 'application/json'}
                if origin:
                    headers['Origin'] = origin
                request = Request(base+path, data=json.dumps(payload).encode(), headers=headers)
                with urlopen(request) as response:
                    return json.load(response)
            self.assertEqual(post('/api/settings', {'decay_days': 120})['decay_days'], 120)
            self.assertEqual(post('/api/memories/use', {'id': self.memory.id})['id'], self.memory.id)
            self.assertTrue(recall('deployment', 2, base)['results'])
            for path, payload in [('/api/settings', {'decay_days': False}), ('/api/recall', {'query': 'x', 'limit': 31})]:
                with self.assertRaises(HTTPError) as raised:
                    post(path, payload)
                self.assertEqual(raised.exception.code, 400)
            with self.assertRaises(HTTPError) as raised:
                post('/api/recall', {'query': 'deployment'}, 'https://example.com')
            self.assertEqual(raised.exception.code, 403)
            for url in ['https://example.com', 'http://127.0.0.1@example.com', 'http://localhost/private']:
                with self.assertRaises(ValueError):
                    recall('deployment', base_url=url)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
