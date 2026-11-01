"""Measure queued HTTP workloads and process recovery without model calls."""

import argparse
import asyncio
from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import random
import socket
import subprocess
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finevolab.experience import AccessStore
from finevolab.runtime import PhaseRunner, read_json, write_json
from finevolab.service_metrics import summarize
from finevolab.task_worker import execute_one

TOKEN = 'worker-load-invitation-contract-only'
HEADERS = {'X-FinEvoLab': '1'}
CASES = ['fast'] * 8 + ['slow'] * 2 + ['failure', 'timeout']
LEASE_SECONDS = 30.0


def policy(capacity):
    return {'execution_mode': 'worker', 'daily_tasks': 100, 'concurrent_tasks': capacity,
            'budget': {'timeout_seconds': 2}, 'users': {name: {
                'daily_tasks': 50, 'invite_sha256': hashlib.sha256(token.encode()).hexdigest()}
                for name, token in [('alice', TOKEN), ('bob', TOKEN + '-bob')]}}


async def fixture(root, job):
    kind = job['payload']['question']
    async def prepare(state):
        counter = root / (job['task_id'] + '.prepare')
        counter.write_text(counter.read_text() + '1\n' if counter.exists() else '1\n')
        return {'kind': kind}
    async def analyze(state):
        if kind == 'recover' and job['generation'] == 1:
            (root / 'recovery.ready').write_text(job['task_id'])
            await asyncio.sleep(30)
        await asyncio.sleep(.3 if kind == 'slow' else .03)
        if kind == 'failure':
            raise ValueError('Injected fixture failure')
        if kind == 'timeout':
            await asyncio.sleep(5)
        return {'request': {'intent': 'metric', 'companies': ['MSFT'], 'fiscal_year': 2024,
                            'metrics': ['revenue']}, 'usage': {'requests': 0, 'total_tokens': 0}}
    async def validate(state):
        return {'status': 'passed'}
    return await PhaseRunner(root / 'output/tasks', job['task_id'], {'fixture': 'worker-load-v1'}).run(
        {'prepare': prepare, 'analyze': analyze, 'validate': validate},
        timeout_seconds=.2 if kind == 'timeout' else 240)


async def child_worker(root, config):
    settings = read_json(config)
    while True:
        found = await execute_one(root, settings, runner=lambda job: fixture(root, job),
                                  lease_seconds=LEASE_SECONDS)
        if not found:
            await asyncio.sleep(.5)


@contextmanager
def process(command, log_path):
    with log_path.open('ab') as log:
        child = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            yield child
        finally:
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10)


async def until(check, seconds=40):
    started = time.perf_counter()
    while time.perf_counter() - started < seconds:
        value = await check()
        if value:
            return value
        await asyncio.sleep(.1)
    raise TimeoutError('Acceptance condition did not complete')


def latency(rows, field):
    values = summarize([{'status': 'complete', 'elapsed_seconds': row[field]} for row in rows], 1)
    return {key: values[key] for key in ('p50_seconds', 'p95_seconds', 'max_seconds', 'mean_seconds')}


async def run_cell(directory, capacity, repeat):
    directory.mkdir(parents=True)
    config = directory / 'access.json'
    write_json(config, policy(capacity))
    access = AccessStore(directory / 'output/experience', policy(capacity))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    command = [sys.executable, '-c',
               'import faulthandler; faulthandler.dump_traceback_later(20, repeat=True); from scripts.manage_experience import main; main()', 'serve',
               '--root', str(directory), '--config', str(config), '--port', str(port)]
    worker_command = [sys.executable, str(Path(__file__).resolve()), '--child', '--root', str(directory),
                      '--config', str(config)]
    records, assertions = [], []
    with ExitStack() as stack:
        server = stack.enter_context(process(command, directory / 'server.log'))
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=40,
                                     headers=HEADERS) as client:
            async def ready():
                if server.poll() is not None:
                    raise RuntimeError('HTTP service exited')
                try:
                    return (await client.get('/experience/api/health')).status_code == 200
                except httpx.TransportError:
                    return False
            await until(ready)
            assertions.append(('anonymous', (await client.get('/experience/api/tasks')).status_code == 401))
            assert (await client.post('/experience/api/login', json={'invite': TOKEN})).status_code == 200
            async def submit(kind, key):
                started = time.perf_counter()
                response = await client.post('/experience/api/tasks', json={'question': kind, 'request_id': key})
                response.raise_for_status()
                return {'kind': kind, 'task_id': response.json()['task_id'],
                        'submit_seconds': time.perf_counter() - started}
            kinds = CASES.copy()
            random.Random(17 + repeat).shuffle(kinds)
            started = time.perf_counter()
            records = await asyncio.gather(*(submit(kind, f'load-request-{index:04d}') for index, kind in enumerate(kinds)))
            duplicate = await submit(kinds[0], 'load-request-0000')
            assertions.append(('idempotency', duplicate['task_id'] == records[0]['task_id']))
            bad = await client.post('/experience/api/tasks', json={'question': 'other', 'request_id': 'load-request-0000'})
            assertions.append(('idempotency_conflict', bad.status_code == 409))
            bad = await client.post('/experience/api/tasks', json={'question': 'fast', 'request_id': 'invalid-request-0001', 'unknown': 1})
            assertions.append(('schema', bad.status_code == 422))
            cancelled = await submit('slow', 'cancel-request-0001')
            result = await client.post('/experience/api/tasks/' + cancelled['task_id'] + '/cancel')
            assertions.append(('queued_cancel', result.json()['status'] == 'cancelled'))
            async with httpx.AsyncClient(base_url=client.base_url, headers=HEADERS, trust_env=False) as bob:
                assert (await bob.post('/experience/api/login', json={'invite': TOKEN + '-bob'})).status_code == 200
                assertions.append(('owner_read', (await bob.get('/experience/api/tasks/' + records[0]['task_id'])).status_code == 404))
                assertions.append(('owner_cancel', (await bob.post('/experience/api/tasks/' + records[0]['task_id'] + '/cancel')).status_code == 404))
            assertions.append(('quota_once', (await client.get('/experience/api/session')).json()['used'] == 13))
            workers = [stack.enter_context(process(worker_command, directory / f'worker-{index}.log'))
                       for index in range(capacity)]
            maximum_active = 0
            async def completed():
                nonlocal maximum_active
                if any(worker.poll() is not None for worker in workers):
                    raise RuntimeError('A load worker exited')
                with access.connect() as db:
                    statuses = db.execute('SELECT status FROM tasks').fetchall()
                maximum_active = max(maximum_active, sum(row[0] == 'running' for row in statuses))
                return all(row[0] not in {'queued', 'running'} for row in statuses)
            await until(completed, seconds=180)
            wall = time.perf_counter() - started
            for record in records:
                status = (await client.get('/experience/api/tasks/' + record['task_id'])).json()
                with access.connect() as db:
                    timing = db.execute('SELECT created,started,finished,generation FROM durable_jobs WHERE task_id=?',
                                        (record['task_id'],)).fetchone()
                expected = 'failed' if record['kind'] in {'failure', 'timeout'} else 'complete'
                record.update(status='timeout' if status['error_type'] == 'TimeoutError' else status['status'],
                    error_type=status['error_type'], elapsed_seconds=timing['finished'] - timing['created'],
                    queue_seconds=timing['started'] - timing['created'],
                    execution_seconds=timing['finished'] - timing['started'], attempts=timing['generation'],
                    passed=status['status'] == expected and timing['generation'] == 1)
                if record['kind'] == 'failure':
                    record['passed'] &= status['error_type'] == 'ValueError'
                if record['kind'] == 'timeout':
                    record['passed'] &= status['error_type'] == 'TimeoutError'
                record.pop('task_id')
            assertions.append(('capacity', 1 <= maximum_active <= capacity))
            assertions.append(('cancel_not_executed', access.queue.detail(cancelled['task_id'])['attempts'] == 0))
            for worker in workers:
                worker.terminate()
                worker.wait(timeout=10)
            recovery = await submit('recover', 'recovery-request-0001')
            with process(worker_command, directory / 'interrupted-worker.log') as interrupted:
                async def checkpoint_ready():
                    return (directory / 'recovery.ready').exists()
                await until(checkpoint_ready)
                interrupted.kill()
                interrupted.wait(timeout=10)
            server.terminate()
            server.wait(timeout=10)
            server = stack.enter_context(process(command, directory / 'restarted-server.log'))
            await until(ready)
            assertions.append(('web_restart', (await client.get('/experience/api/tasks/' + recovery['task_id'])).json()['status'] == 'running'))
            recovery_started = time.perf_counter()
            with process(worker_command, directory / 'recovery-worker.log'):
                async def recovered():
                    return access.task('alice', recovery['task_id'])['status'] == 'complete'
                await until(recovered)
            detail = access.queue.detail(recovery['task_id'])
            calls = (directory / (recovery['task_id'] + '.prepare')).read_text().count('1')
            assertions.append(('checkpoint_recovery', detail['attempts'] == 2 and calls == 1))
            recovery_seconds = time.perf_counter() - recovery_started
    return {'capacity': capacity, 'repeat': repeat, 'records': records,
            'summary': summarize(records, wall), 'queue': latency(records, 'queue_seconds'),
            'execution': latency(records, 'execution_seconds'), 'submission': latency(records, 'submit_seconds'),
            'max_active_observed': maximum_active, 'recovery_seconds': recovery_seconds,
            'assertions': dict(assertions), 'passed': all(row['passed'] for row in records) and all(ok for _, ok in assertions)}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'output/worker_load')
    parser.add_argument('--child', action='store_true')
    parser.add_argument('--root', type=Path)
    parser.add_argument('--config', type=Path)
    args = parser.parse_args()
    if args.child:
        import faulthandler
        faulthandler.dump_traceback_later(30, repeat=True)
        await child_worker(args.root, args.config)
        return
    os.environ.setdefault('FINEVOLAB_TRACE_MODE', 'batch')
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / 'output').resolve()) or output.exists():
        raise ValueError('A new workspace output directory is required')
    output.mkdir(parents=True)
    measured = [*sorted((ROOT / 'finevolab').glob('*.py')), Path(__file__), ROOT / 'scripts/manage_experience.py']
    hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in measured}
    manifest = {'run_id': 'worker-mixed-load', 'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'source_sha256': hashes, 'model_calls': 0, 'repeats': 3, 'capacities': [1, 2],
        'cases_per_cell': CASES, 'trace_mode': os.environ['FINEVOLAB_TRACE_MODE'], 'worker_poll_seconds': .5, 'observer_poll_seconds': .1, 'lease_seconds': LEASE_SECONDS, 'seed': 17,
        'scope': 'Loopback TCP HTTP and real worker processes with controlled asynchronous fixtures; no model-quality estimate',
        'latency_scope': 'Durable enqueue to result publication; cold worker startup included',
        'http_timeout_seconds': 40, 'injected_failures_per_cell': 2, 'cancelled_per_cell': 1,
        'recovery': 'Kill worker after prepare checkpoint, restart Web, reclaim expired lease without database edits'}
    cells = []
    for repeat in range(3):
        for capacity in ([1, 2] if repeat % 2 == 0 else [2, 1]):
            cells.append(await run_cell(output / f'capacity-{capacity}-repeat-{repeat}', capacity, repeat))
            write_json(output / 'results.json', {'manifest': manifest, 'cells': cells})
    if any(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != value for name, value in hashes.items()):
        raise ValueError('Measured source files changed during execution')
    passed = all(cell['passed'] for cell in cells)
    write_json(output / 'results.json', {'manifest': manifest, 'cells': cells, 'passed': passed})
    print(json.dumps({'passed': passed, 'cells': len(cells), 'tasks': sum(len(cell['records']) for cell in cells)}))
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
