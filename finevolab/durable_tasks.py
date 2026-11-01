"""Persist task leases and fence result publication across worker restarts."""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time

from finevolab import business_memory


class TaskQueue:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS durable_jobs (
                    task_id TEXT PRIMARY KEY, payload TEXT, worker TEXT, generation INTEGER DEFAULT 0,
                    lease REAL DEFAULT 0, created REAL, started REAL, finished REAL,
                    cancel INTEGER DEFAULT 0, traceparent TEXT, error_type TEXT);
            """)
            business_memory.initialize(db)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def enqueue(db, task_id, payload, incoming=None):
        db.execute("INSERT INTO durable_jobs(task_id,payload,created,traceparent) VALUES(?,?,?,?)",
                   (task_id, json.dumps(payload), time.time(), incoming))

    def claim(self, worker, capacity=1, lease_seconds=30):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            active = db.execute("SELECT COUNT(*) FROM durable_jobs j JOIN tasks t ON t.id=j.task_id "
                                "WHERE t.status='running' AND j.lease>?", (now,)).fetchone()[0]
            if active >= capacity:
                return None
            row = db.execute("SELECT j.*,t.owner FROM durable_jobs j JOIN tasks t ON t.id=j.task_id "
                "WHERE j.cancel=0 AND (t.status='queued' OR (t.status='running' AND j.lease<?)) "
                "ORDER BY j.created,j.task_id LIMIT 1", (now,)).fetchone()
            if row is None:
                return None
            generation = row['generation'] + 1
            db.execute("UPDATE durable_jobs SET worker=?,generation=?,lease=?,started=COALESCE(started,?) WHERE task_id=?",
                       (worker, generation, now + lease_seconds, now, row['task_id']))
            db.execute("UPDATE tasks SET status='running',error_type=NULL WHERE id=?", (row['task_id'],))
            return {**dict(row), "worker": worker, "generation": generation, "payload": json.loads(row['payload'])}

    def heartbeat(self, job, lease_seconds=30):
        with self.connect() as db:
            changed = db.execute("UPDATE durable_jobs SET lease=? WHERE task_id=? AND worker=? AND generation=? "
                "AND cancel=0 AND lease>? AND EXISTS(SELECT 1 FROM tasks WHERE id=task_id AND status='running')",
                (time.time() + lease_seconds, job['task_id'], job['worker'], job['generation'], time.time())).rowcount
            return changed == 1

    def finish(self, job, state=None, error=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM durable_jobs WHERE task_id=?", (job['task_id'],)).fetchone()
            if not row or row['cancel'] or row['worker'] != job['worker'] or row['generation'] != job['generation'] or row['lease'] <= time.time():
                return False
            status = 'failed' if error else state['status']
            db.execute("UPDATE tasks SET status=?,error_type=? WHERE id=?", (status, error, job['task_id']))
            db.execute("UPDATE durable_jobs SET finished=?,lease=0,error_type=? WHERE task_id=?",
                       (time.time(), error, job['task_id']))
            if not error:
                business_memory.remember(db, job['owner'], job['payload']['memory'], state)
            return True

    def cancel(self, owner, task_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM tasks WHERE id=? AND owner=?", (task_id, owner)).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row['status'] in {'queued', 'running'}:
                db.execute("UPDATE durable_jobs SET cancel=1,lease=0 WHERE task_id=?", (task_id,))
                db.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (task_id,))

    def detail(self, task_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM durable_jobs WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                return {}
            return {"queue_seconds": max(0, (row['started'] or time.time()) - row['created']),
                    "attempts": row['generation'], "memory": json.loads(row['payload'])['memory']}
