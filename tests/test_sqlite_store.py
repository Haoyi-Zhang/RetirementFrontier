import json
from pathlib import Path
import tempfile
import unittest

from checker import analyze_trace
from medium import materialize, recover
from model import admit_trace
from sqlite_store import SQLiteStore, STAGES
from verifier import verify_certificate


class SQLiteAdapterTests(unittest.TestCase):
    def test_objects_alias_delete_and_reopen(self):
        for mode in ('WAL', 'DELETE'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'store.db'
                with SQLiteStore(path, mode=mode, chunk_size=3) as s:
                    s.put('a', b'abcdabcd'); s.put('alias', b'abcdabcd')
                    s.delete('a'); s.put('b', b'new'); s.put('empty', b'')
                with SQLiteStore(path, mode=mode) as s:
                    self.assertEqual(s.get('alias'), b'abcdabcd')
                    self.assertEqual(s.get('b'), b'new')
                    self.assertEqual(s.get('empty'), b'')
                    self.assertNotIn('a', s.recover()['objects_hex'])
                    trace = s.trace(); model = admit_trace(trace)
                    result = analyze_trace(trace, minimum=True)
                    self.assertTrue(result['safe'])
                    self.assertTrue(verify_certificate(trace, result['certificate'])['accepted'])
                    expected = recover(materialize(model, list(range(model.n))))
                    self.assertEqual(s.recover()['objects_hex'], expected['objects_hex'])

    def test_native_recovery_does_not_trust_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            with SQLiteStore(Path(tmp)/'s.db') as s:
                s.put('a', b'value')
                trace = s.trace()
                s.db.execute("UPDATE slots SET payload=? WHERE role='chunk'", (b'wrong',))
                self.assertFalse(s.recover()['ok'])
                self.assertTrue(analyze_trace(trace)['safe'])

    def test_uncommitted_state_and_log_rollback_together(self):
        for mode in ('WAL', 'DELETE'):
            for stage in ('after-state', 'after-audit'):
                with self.subTest(mode=mode, stage=stage), tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp)/'s.db'
                    with SQLiteStore(path, mode=mode) as s:
                        s.put('a', b'first'); before = s.trace()
                        def fail(i, at):
                            if at == stage: raise RuntimeError('test interruption')
                        s.hook = fail
                        with self.assertRaises(RuntimeError): s.put('a', b'second')
                        with self.assertRaises(RuntimeError): s.put('b', b'third')
                    with SQLiteStore(path, mode=mode) as s:
                        self.assertEqual(s.trace(), before)
                        self.assertEqual(s.get('a'), b'first')
                        s.put('b', b'third')
                        self.assertTrue(analyze_trace(s.trace())['safe'])

    def test_safe_reuse_saves_slots_without_reusing_current_manifest(self):
        counts = {}
        for policy in ('frontier-reuse', 'no-reuse'):
            with tempfile.TemporaryDirectory() as tmp:
                with SQLiteStore(Path(tmp)/'s.db', policy=policy) as s:
                    for i in range(8): s.put('a', bytes([i])*20)
                    counts[policy] = s.metrics()['slots']
                    self.assertTrue(analyze_trace(s.trace())['safe'])
                    self.assertEqual(s.get('a'), bytes([7])*20)
        self.assertLess(counts['frontier-reuse'], counts['no-reuse'])

    def test_unsafe_reuse_yields_actual_bad_committed_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'s.db'
            with SQLiteStore(path, policy='unsafe-reuse') as s:
                s.put('a', b'first')
                target = s.metrics()['events']
                def stop(i, stage):
                    if i == target and stage == 'after-commit': raise RuntimeError('stop')
                s.hook = stop
                with self.assertRaises(RuntimeError): s.put('a', b'second')
                self.assertFalse(s.recover()['ok'])
                self.assertFalse(analyze_trace(s.trace())['safe'])

    def test_empty_deleted_and_orphan_prefixes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with SQLiteStore(Path(tmp)/'s.db') as s:
                self.assertEqual(s.recover()['objects_hex'], {})
                s.delete('absent'); s.put('a', b''); s.delete('a')
                self.assertEqual(s.recover()['objects_hex'], {})
                self.assertTrue(analyze_trace(s.trace())['safe'])

    def test_invalid_configuration_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'s.db'
            with self.assertRaises(ValueError): SQLiteStore(path, mode='OFF')
            with self.assertRaises(ValueError): SQLiteStore(path, chunk_size=True)
            self.assertFalse(path.exists())

    def test_every_record_boundary_agrees_with_model(self):
        for mode in ('WAL', 'DELETE'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                checks = []
                with SQLiteStore(Path(tmp)/'s.db', mode=mode) as s:
                    def after(i, stage):
                        if stage == 'after-commit':
                            t = s.trace(); m = admit_trace(t)
                            native = s.recover(); abstract = recover(materialize(m, range(m.n)))
                            checks.append(native['ok'] == abstract['ok'] and native.get('objects_hex') == abstract.get('objects_hex'))
                    s.hook = after
                    s.put('a', b'alpha'); s.put('alias', b'alpha')
                    s.delete('a'); s.put('a', b'beta')
                    self.assertTrue(checks and all(checks))
