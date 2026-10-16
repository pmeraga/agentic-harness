"""SQLite is both the durable task queue and the result store."""
import json
import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import contextmanager


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                  id TEXT PRIMARY KEY, created REAL NOT NULL, config TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                  id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                  task TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
                  owner TEXT, heartbeat REAL, started REAL, finished REAL,
                  result TEXT, error TEXT);
                CREATE INDEX IF NOT EXISTS queue ON jobs(state, id);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def submit(self, config, tasks, run_id=None):
        run_id = run_id or uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO runs VALUES (?,?,?)', (run_id, time.time(), json.dumps(config)))
            db.executemany('INSERT INTO jobs(run_id,task) VALUES (?,?)',
                           [(run_id, json.dumps(t)) for t in tasks])
        return run_id

    def claim(self, owner, run_id=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            query = "SELECT * FROM jobs WHERE state='queued'"
            params = []
            if run_id:
                query += ' AND run_id=?'
                params.append(run_id)
            row = db.execute(query + ' ORDER BY id LIMIT 1', params).fetchone()
            if row is None:
                return None
            now = time.time()
            db.execute("UPDATE jobs SET state='running',owner=?,heartbeat=?,started=? WHERE id=?",
                       (owner, now, now, row['id']))
            config = db.execute('SELECT config FROM runs WHERE id=?', (row['run_id'],)).fetchone()[0]
            return dict(row) | {'task': json.loads(row['task']), 'config': json.loads(config), 'owner': owner}

    def heartbeat(self, job_id, owner):
        with self.connect() as db:
            return db.execute("UPDATE jobs SET heartbeat=? WHERE id=? AND owner=? AND state='running'",
                              (time.time(), job_id, owner)).rowcount == 1

    def finish(self, job_id, owner, result=None, error=None):
        with self.connect() as db:
            return db.execute('''UPDATE jobs SET state=?,result=?,error=?,finished=?
                WHERE id=? AND owner=? AND state='running' ''',
                ('error' if error else 'completed', json.dumps(result) if result else None,
                 error, time.time(), job_id, owner)).rowcount == 1

    def reap(self, lease_seconds=90):
        # Never automatically rerun a paid LLM call after an ambiguous worker failure.
        with self.connect() as db:
            return db.execute('''UPDATE jobs SET state='error',error='Worker lease expired; submit a new run to retry',
                finished=? WHERE state='running' AND heartbeat<?''',
                (time.time(), time.time() - lease_seconds)).rowcount

    def get(self, run_id):
        with self.connect() as db:
            run = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
            if run is None:
                raise ValueError(f'Unknown run: {run_id}')
            jobs = [dict(r) for r in db.execute('SELECT * FROM jobs WHERE run_id=? ORDER BY id', (run_id,))]
        counts = {state: sum(j['state'] == state for j in jobs) for state in ('queued', 'running', 'completed', 'error')}
        state = ('running' if counts['running'] else 'queued' if counts['queued'] else
                 'error' if counts['error'] else 'completed')
        results = []
        for job in jobs:
            results.append({'task_id': json.loads(job['task'])['id'], 'state': job['state'],
                            'started': job['started'], 'finished': job['finished'],
                            'result': json.loads(job['result']) if job['result'] else None, 'error': job['error']})
        passed = sum(bool(j['result'] and j['result']['passed']) for j in results)
        return {'run_id': run_id, 'created': run['created'], 'state': state, 'counts': counts,
                'total': len(jobs), 'passed': passed, 'accuracy': passed / len(jobs),
                'mean_score': sum(j['result']['score'] for j in results if j['result']) / len(jobs),
                'tasks': results}

    def list(self, active=False):
        with self.connect() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM runs ORDER BY created DESC')]
        runs = [self.get(r) for r in ids]
        return [r for r in runs if not active or r['state'] in ('queued', 'running')]
