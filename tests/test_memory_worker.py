"""Verify context isolation, transactional enqueue, leases, and recovery boundaries."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from finevolab.business_memory import snapshot, resolve, remember
from finevolab.experience import AccessStore, AnalysisRequest, create_app
from finevolab.task_worker import execute_one


class MemoryWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.policy = {'execution_mode': 'worker', 'daily_tasks': 100, 'concurrent_tasks': 1,
                       'budget': {}, 'users': {'alice': {'daily_tasks': 100, 'invite_sha256': 'a' * 64}}}
        self.access = AccessStore(self.root / 'output/experience', self.policy)
        self.queue = self.access.queue

    def submit(self, key='request-0000000001', conversation='main'):
        return self.access.reserve('alice', AnalysisRequest(question='MSFT FY2024 revenue', request_id=key,
                                   conversation_id=conversation, use_memory=True))[0]

    def state(self, task_id):
        return {'task_id': task_id, 'status': 'complete', 'phases': {
            'validate': {'result': {'status': 'passed'}}, 'analyze': {'result': {'request': {
                'intent': 'metric', 'companies': ['MSFT'], 'fiscal_year': 2024, 'metrics': ['revenue']}}}}}

    def test_atomic_enqueue_and_idempotency(self):
        row = self.submit()
        self.assertEqual(row['status'], 'queued')
        self.assertEqual(self.submit()['id'], row['id'])
        with self.access.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM durable_jobs').fetchone()[0], 1)
        with self.assertRaises(Exception) as error:
            self.submit(conversation='other')
        self.assertEqual(error.exception.status_code, 409)

    def test_failed_enqueue_rolls_back_quota(self):
        from unittest.mock import patch
        with patch.object(self.queue, 'enqueue', side_effect=RuntimeError('Unavailable')):
            with self.assertRaises(RuntimeError):
                self.submit()
        with self.access.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 0)

    def test_claim_capacity_and_fencing(self):
        row = self.submit()
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = list(pool.map(lambda n: self.queue.claim(str(n)), range(4)))
        first = next(j for j in jobs if j)
        self.assertEqual(sum(bool(j) for j in jobs), 1)
        with self.queue.connect() as db:
            db.execute('UPDATE durable_jobs SET lease=0')
        second = self.queue.claim('replacement')
        self.assertFalse(self.queue.finish(first, self.state(row['id'])))
        self.assertTrue(self.queue.finish(second, self.state(row['id'])))
        self.assertEqual(self.access.task('alice', row['id'])['status'], 'complete')

    def test_cancel_cannot_publish_or_reclaim(self):
        row = self.submit()
        job = self.queue.claim('worker')
        with self.assertRaises(KeyError):
            self.queue.cancel('bob', row['id'])
        self.queue.cancel('alice', row['id'])
        self.assertFalse(self.queue.finish(job, self.state(row['id'])))
        self.assertIsNone(self.queue.claim('other'))

    def test_memory_isolation_expiry_and_revision(self):
        row = self.submit()
        job = self.queue.claim('worker')
        self.queue.finish(job, self.state(row['id']))
        with self.queue.connect() as db:
            stored = snapshot(db, 'alice', 'main', job['payload']['identity'])
            self.assertEqual(stored['context']['companies'], ['MSFT'])
            self.assertIn('fiscal year 2023', resolve('上一年呢？', stored))
            self.assertNotIn('上一年', resolve('上一年呢？', stored))
            self.assertIn('growth for fiscal year 2024', resolve('再和上一年比较', stored))
            self.assertEqual(resolve('AAPL FY2022 net income', stored), 'AAPL FY2022 net income')
            self.assertFalse(snapshot(db, 'bob', 'main', stored['source'])['context'])
            self.assertFalse(snapshot(db, 'alice', 'main', 'changed')['context'])
            self.assertFalse(remember(db, 'alice', job['payload']['memory'], self.state(row['id'])))
            db.execute('UPDATE business_context SET expires=0')
            self.assertFalse(snapshot(db, 'alice', 'main', stored['source'])['context'])

    def test_worker_executes_frozen_input_and_commits_context(self):
        row = self.submit()
        async def runner(job):
            self.assertEqual(job['payload']['question'], row['question'])
            return self.state(row['id'])
        self.assertTrue(asyncio.run(execute_one(self.root, self.policy, runner=runner)))
        self.assertEqual(self.access.task('alice', row['id'])['status'], 'complete')

    def test_worker_cancel_propagates(self):
        row = self.submit()
        async def runner(job):
            self.queue.cancel('alice', row['id'])
            await asyncio.sleep(1)
            self.fail('A cancelled job continued')
        asyncio.run(execute_one(self.root, self.policy, runner=runner, lease_seconds=.1))
        self.assertEqual(self.access.task('alice', row['id'])['status'], 'cancelled')

    def test_documentation_review_preserves_input_identity(self):
        from finevolab.task_worker import input_identity
        original = input_identity(self.root, self.policy)
        (self.root / 'configs').mkdir()
        (self.root / 'configs/readme_sync.json').write_text('{"version": 1}')
        self.assertEqual(input_identity(self.root, self.policy), original)

    def test_slow_enqueue_does_not_block_health_requests(self):
        import hashlib
        from threading import Event
        from unittest.mock import patch
        token = 'x' * 43
        self.policy['users']['alice']['invite_sha256'] = hashlib.sha256(token.encode()).hexdigest()
        config = self.root / 'access.json'
        config.write_text(json.dumps(self.policy))
        application = create_app(self.root, config)
        entered, release, healthy = Event(), Event(), Event()
        reserve = application.state.access.reserve
        def slow_reserve(*args):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('The test did not release enqueue')
            return reserve(*args)
        with TestClient(application) as client:
            headers = {'X-FinEvoLab': '1'}
            client.post('/experience/api/login', json={'invite': token}, headers=headers)
            with patch.object(application.state.access, 'reserve', side_effect=slow_reserve):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    pending = pool.submit(client.post, '/experience/api/tasks',
                        json={'question': 'MSFT FY2024 revenue', 'request_id': 'nonblocking-request-0001'}, headers=headers)
                    self.assertTrue(entered.wait(2))
                    def probe():
                        response = client.get('/experience/api/health')
                        if response.status_code == 200:
                            healthy.set()
                        return response
                    health = pool.submit(probe)
                    try:
                        responsive = healthy.wait(2)
                    finally:
                        release.set()
                    self.assertEqual(pending.result().status_code, 202)
                    self.assertEqual(health.result().status_code, 200)
                    self.assertTrue(responsive)

    def test_source_change_rejects_execution(self):
        row = self.submit()
        (self.root / 'configs').mkdir()
        (self.root / 'configs/example.json').write_text('{}')
        async def runner(job):
            self.fail('Changed input executed')
        asyncio.run(execute_one(self.root, self.policy, runner=runner))
        self.assertEqual(self.access.task('alice', row['id'])['error_type'], 'ValueError')

    def test_queue_preserves_trace_parent(self):
        from finevolab.tracing import trace_scope, tracing_mode
        with tracing_mode('file'):
            with trace_scope('submission', self.root / 'output/traces') as (trace, parent):
                row = self.submit()
                identifier, parent_id = trace.trace_id, parent['span_id']
            async def runner(job):
                self.assertIn(identifier, job['traceparent'])
                return self.state(row['id'])
            asyncio.run(execute_one(self.root, self.policy, runner=runner))
        spans = [span for p in (self.root / 'output/traces').glob('*.json')
                 for span in json.loads(p.read_text())['spans']]
        worker = next(span for span in spans if span['name'] == 'worker.execute')
        self.assertEqual(worker['trace_id'], identifier)
        self.assertEqual(worker['parent_span_id'], parent_id)

    def test_http_memory_lifecycle_and_web_restart(self):
        import hashlib
        token = 'x' * 43
        self.policy['users']['alice']['invite_sha256'] = hashlib.sha256(token.encode()).hexdigest()
        config = self.root / 'access.json'
        config.write_text(json.dumps(self.policy))
        headers = {'X-FinEvoLab': '1'}
        def app():
            return create_app(self.root, config)
        with TestClient(app()) as client:
            self.assertEqual(client.get('/experience/api/memory').status_code, 401)
            self.assertEqual(client.put('/experience/api/memory/preferences', json={'language': 'en'}, headers=headers).status_code, 401)
            client.post('/experience/api/login', json={'invite': token}, headers=headers)
            pref = {'language': 'en', 'amount_scale': 'millions'}
            self.assertEqual(client.put('/experience/api/memory/preferences', json=pref, headers=headers).json(), pref)
            body = {'question': 'MSFT FY2024 revenue', 'request_id': 'request-0000000001',
                    'conversation_id': 'main', 'use_memory': True}
            task_id = client.post('/experience/api/tasks', json=body, headers=headers).json()['task_id']
            self.assertEqual(client.get('/experience/api/tasks/' + task_id).json()['status'], 'queued')
            job = self.queue.claim('worker')
            client.delete('/experience/api/memory', headers=headers)
            self.queue.finish(job, self.state(task_id))
            self.assertFalse(client.get('/experience/api/memory?conversation_id=main').json()['context'])
            self.assertEqual(client.get('/experience/api/memory').json()['preferences']['amount_scale'], 'units')
            body['request_id'] = 'request-0000000002'
            pending = client.post('/experience/api/tasks', json=body, headers=headers).json()['task_id']
        with TestClient(app()) as client:
            client.post('/experience/api/login', json={'invite': token}, headers=headers)
            self.assertEqual(client.get('/experience/api/tasks/' + pending).json()['status'], 'queued')
            self.assertEqual(client.post('/experience/api/tasks/' + pending + '/cancel', json={}, headers=headers).json()['status'], 'cancelled')


if __name__ == '__main__':
    unittest.main()
