"""Owned object-store adapter over real SQLite transactions.

One application DATA/ROOT event commits per transaction, with its audit row in
the same transaction. Slots are logical SQLite records, NOT device addresses.
Native recovery reads slots/roots, never the audit transcript or model oracle.
Only one writer and strict latest-root recovery are supported. This is an
engine-backed adapter, not a production deduplicating filesystem.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Callable

from model import canonical_manifest, make_ref

POLICIES = ('frontier-reuse', 'no-reuse', 'unsafe-reuse')
STAGES = ('before-begin', 'after-state', 'after-audit', 'after-commit')
Hook = Callable[[int, str], None]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


class SQLiteStore:
    def __init__(self, path: Path, *, mode='WAL', policy='frontier-reuse',
                 chunk_size=256, hook: Hook | None = None):
        if mode not in ('WAL', 'DELETE') or policy not in POLICIES:
            raise ValueError('unsupported SQLite mode or allocation policy')
        if type(chunk_size) is not int or not 1 <= chunk_size <= 1048576:
            raise ValueError('invalid chunk size')
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        self.db.execute('PRAGMA busy_timeout=10000')
        observed = self.db.execute('PRAGMA journal_mode='+mode).fetchone()[0]
        if observed.upper() != mode:
            self.db.close()
            raise RuntimeError('requested journal mode not active')
        self.db.execute('PRAGMA synchronous=FULL')
        if self.db.execute('PRAGMA synchronous').fetchone()[0] != 2:
            self.db.close()
            raise RuntimeError('FULL synchronization not active')
        self.db.execute('PRAGMA wal_autocheckpoint=0')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS slots(
            slot INTEGER PRIMARY KEY, generation INTEGER NOT NULL,
            role TEXT NOT NULL, payload BLOB NOT NULL);
          CREATE TABLE IF NOT EXISTS roots(
            event INTEGER PRIMARY KEY, prefix INTEGER UNIQUE NOT NULL,
            manifest TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS audit(event INTEGER PRIMARY KEY, body TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS operations(prefix INTEGER PRIMARY KEY, body TEXT NOT NULL);
        ''')
        self.mode, self.policy, self.chunk_size, self.hook = mode, policy, chunk_size, hook
        self._poisoned = False

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _next_id(self):
        return self.db.execute('SELECT coalesce(max(event)+1,0) FROM audit').fetchone()[0]

    def _emit(self, event, stage):
        if self.hook:
            self.hook(event, stage)

    def _persist(self, event, operation=None):
        if self._poisoned:
            raise RuntimeError('reopen after an interrupted write')
        i = event['id']
        try:
            self._emit(i, 'before-begin')
            self.db.execute('BEGIN IMMEDIATE')
            if self._next_id() != i:
                raise RuntimeError('concurrent writer or stale event')
            if event['kind'] == 'data':
                self.db.execute('INSERT OR REPLACE INTO slots VALUES(?,?,?,?)',
                                (event['slot'], event['generation'], event['role'],
                                 bytes.fromhex(event['data_hex'])))
            else:
                self.db.execute('INSERT INTO roots VALUES(?,?,?)',
                                (i, event['prefix'], canonical(event['manifest_ref'])))
                self.db.execute('INSERT INTO operations VALUES(?,?)',
                                (event['prefix'], canonical(operation)))
            self._emit(i, 'after-state')
            self.db.execute('INSERT INTO audit VALUES(?,?)', (i, canonical(event)))
            self._emit(i, 'after-audit')
            self.db.execute('COMMIT')
            self._emit(i, 'after-commit')
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            self._poisoned = True
            raise

    def _read_ref(self, ref, role):
        if (not isinstance(ref, dict)
                or set(ref) != {'slot', 'generation', 'digest', 'length'}
                or any(type(ref[k]) is not int for k in ('slot', 'generation', 'length'))):
            raise ValueError('malformed native reference')
        row = self.db.execute('SELECT generation,role,payload FROM slots WHERE slot=?',
                              (ref['slot'],)).fetchone()
        if row is None or row[0] != ref['generation'] or row[1] != role:
            raise ValueError('missing or stale native incarnation')
        data = bytes(row[2])
        if len(data) != ref['length'] or hashlib.sha256(data).hexdigest() != ref['digest']:
            raise ValueError('native content mismatch')
        return data

    def _native(self):
        """Read one consistent database snapshot; do not consult audit/model."""
        row = self.db.execute('SELECT event,prefix,manifest FROM roots ORDER BY event DESC LIMIT 1').fetchone()
        if row is None:
            return {'ok': True, 'root_event': None, 'prefix': 0, 'objects_hex': {}}, {}, None
        ref = json.loads(row[2])
        data = self._read_ref(ref, 'manifest')
        namespace = json.loads(data)
        if not isinstance(namespace, dict) or canonical_manifest(namespace) != data:
            raise ValueError('noncanonical native manifest')
        objects = {}
        for name, refs in namespace.items():
            if not isinstance(name, str) or not isinstance(refs, list):
                raise ValueError('malformed native namespace')
            objects[name] = b''.join(self._read_ref(r, 'chunk') for r in refs).hex()
        return {'ok': True, 'root_event': row[0], 'prefix': row[1], 'objects_hex': objects}, namespace, ref

    def recover(self):
        self.db.execute('BEGIN')
        try:
            value = self._native()[0]
            self.db.execute('COMMIT')
            return value
        except (ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            return {'ok': False, 'reason': str(exc)}

    def get(self, name):
        result = self.recover()
        if not result['ok']:
            raise ValueError(result['reason'])
        return bytes.fromhex(result['objects_hex'][name])

    def _allocate(self, payload, role, protected, reserved):
        rows = list(self.db.execute('SELECT slot,generation,role FROM slots ORDER BY slot'))
        eligible = [r for r in rows if r[2] == role and r[0] not in reserved
                    and (self.policy == 'unsafe-reuse' or r[0] not in protected)]
        if self.policy != 'no-reuse' and eligible:
            slot, generation, _ = eligible[0]
            generation += 1
        else:
            slot = max((r[0] for r in rows), default=-1) + 1
            generation = 1
        reserved.add(slot)
        ref = make_ref(slot, generation, payload)
        i = self._next_id()
        event = {'id': i, 'kind': 'data', **ref, 'data_hex': payload.hex(),
                 'role': role, 'deps': [i-1] if i else []}
        self._persist(event)
        return ref

    def apply(self, operation):
        if self._poisoned:
            raise RuntimeError('reopen after an interrupted write')
        if not isinstance(operation, dict) or operation.get('op') not in ('put', 'delete'):
            raise ValueError('expected put or delete')
        name = operation.get('name')
        if not isinstance(name, str) or not name:
            raise ValueError('nonempty object name required')
        if operation['op'] == 'put':
            if set(operation) != {'op', 'name', 'chunks_hex'} or not isinstance(operation['chunks_hex'], list):
                raise ValueError('invalid put')
            parts = [bytes.fromhex(x) for x in operation['chunks_hex']]
            if any(not isinstance(x, str) or x != b.hex() for x, b in zip(operation['chunks_hex'], parts)):
                raise ValueError('canonical content required')
        elif set(operation) != {'op', 'name'}:
            raise ValueError('invalid delete')
        state, namespace, old_manifest = self._native()
        # Protect the PREVIOUS publication, including the replaced name, until
        # the next publication commits. A working namespace is not retirement.
        protected = {r['slot'] for refs in namespace.values() for r in refs}
        if old_manifest:
            protected.add(old_manifest['slot'])
        reserved = set()
        current = {}
        for refs in namespace.values():
            for ref in refs:
                current[self._read_ref(ref, 'chunk')] = ref
        namespace = dict(namespace)
        expected = dict(state['objects_hex'])
        if operation['op'] == 'delete':
            namespace.pop(name, None)
            expected.pop(name, None)
        else:
            refs = []
            for payload in parts:
                if payload not in current:
                    current[payload] = self._allocate(payload, 'chunk', protected, reserved)
                refs.append(current[payload])
            namespace[name] = refs
            expected[name] = b''.join(parts).hex()
        mref = self._allocate(canonical_manifest(namespace), 'manifest', protected, reserved)
        i = self._next_id()
        self._persist({'id': i, 'kind': 'root', 'prefix': state['prefix']+1,
                       'manifest_ref': mref, 'namespace': namespace,
                       'objects_hex': expected, 'deps': [i-1] if i else []}, operation)
        return state['prefix']+1

    def put(self, name, payload):
        if not isinstance(payload, bytes):
            raise TypeError('payload must be bytes')
        return self.apply({'op': 'put', 'name': name,
                           'chunks_hex': [payload[i:i+self.chunk_size].hex()
                                          for i in range(0, len(payload), self.chunk_size)]})

    def delete(self, name):
        return self.apply({'op': 'delete', 'name': name})

    def trace(self):
        events = [json.loads(row[0]) for row in self.db.execute('SELECT body FROM audit ORDER BY event')]
        operations = [json.loads(row[0]) for row in self.db.execute('SELECT body FROM operations ORDER BY prefix')]
        return {'schema': 'retirement-frontier-trace-v1', 'events': events,
                'operations': operations, 'acknowledgements': [
                    {'issued': e['id']+1, 'prefix': e['prefix'], 'anchors': [e['id']]}
                    for e in events if e['kind'] == 'root']}

    def metrics(self):
        return {'slots': self.db.execute('SELECT count(*) FROM slots').fetchone()[0],
                'slot_payload_bytes': self.db.execute('SELECT coalesce(sum(length(payload)),0) FROM slots').fetchone()[0],
                'audit_bytes': self.db.execute('SELECT coalesce(sum(length(body)),0) FROM audit').fetchone()[0],
                'events': self._next_id(),
                'roots': self.db.execute('SELECT count(*) FROM roots').fetchone()[0]}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('database', type=Path)
    ap.add_argument('--mode', choices=('WAL', 'DELETE'), default='WAL')
    sub = ap.add_subparsers(dest='action', required=True)
    p = sub.add_parser('put'); p.add_argument('name'); p.add_argument('file', type=Path)
    p = sub.add_parser('get'); p.add_argument('name'); p.add_argument('file', type=Path)
    p = sub.add_parser('delete'); p.add_argument('name')
    sub.add_parser('list'); sub.add_parser('trace')
    args = ap.parse_args()
    with SQLiteStore(args.database, mode=args.mode) as store:
        if args.action == 'put': store.put(args.name, args.file.read_bytes())
        elif args.action == 'get':
            # Never overwrite an existing user file.
            with args.file.open('xb') as target: target.write(store.get(args.name))
        elif args.action == 'delete': store.delete(args.name)
        elif args.action == 'trace': print(canonical(store.trace()))
        else: print(canonical(store.recover()))


if __name__ == '__main__':
    main()
