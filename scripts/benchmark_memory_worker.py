"""Measure context resolution and persisted task recovery on fixed inputs."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finevolab.business_memory import resolve
from finevolab.experience import AccessStore, AnalysisRequest
from finevolab.task_worker import execute_one
from finevolab.runtime import PhaseRunner, write_json


def fixture_state(task_id):
    return {'task_id': task_id, 'status': 'complete', 'phases': {
        'validate': {'result': {'status': 'passed'}}, 'analyze': {'result': {'request': {
        'intent': 'metric', 'companies': ['MSFT'], 'fiscal_year': 2024, 'metrics': ['revenue']}}}}}


def policy():
    return {'execution_mode': 'worker', 'daily_tasks': 100, 'concurrent_tasks': 1,
            'budget': {}, 'users': {'alice': {'daily_tasks': 100, 'invite_sha256': 'a' * 64}}}


async def checkpoint_job(root, job, interrupt=False):
    directory = root / 'output/tasks'
    async def prepare(state):
        path = root / 'calls.txt'
        path.write_text(path.read_text() + 'prepare\n' if path.exists() else 'prepare\n')
        return {'value': 42}
    async def analyze(state):
        if interrupt:
            os._exit(9)
        return fixture_state(job['task_id'])['phases']['analyze']['result']
    async def validate(state):
        return {'status': 'passed'}
    return await PhaseRunner(directory, job['task_id'], {'fixture': 'cpu-v1'}).run(
        {'prepare': prepare, 'analyze': analyze, 'validate': validate})


def memory_measurements(cases):
    rows = []
    for item in cases:
        for repeat in range(3):
            for arm in ['off', 'on']:
                memory = {'context': item['context'], 'enabled': arm == 'on'}
                started = time.perf_counter()
                result = resolve(item['question'], memory)
                passed = all(term in result for term in item['required']) and all(term not in result for term in item.get('forbidden', []))
                rows.append({'case_id': item['id'], 'split': item['split'], 'repeat': repeat,
                             'arm': arm, 'passed': passed, 'resolved_question': result,
                             'seconds': time.perf_counter() - started})
    return rows


def fault_measurements():
    rows = []
    for kind in ['queued_restart', 'claimed_exit', 'checkpoint_exit', 'duplicate', 'claim_race', 'stale_lease', 'cancel', 'unknown_usage']:
        for repeat in range(5):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory); access = AccessStore(root / 'output/experience', policy()); queue = access.queue
                request = AnalysisRequest(question='MSFT FY2024 revenue', request_id='request-0000000001')
                row, _ = access.reserve('alice', request)
                started = time.perf_counter()
                if kind in {'claimed_exit', 'checkpoint_exit'}:
                    mode = 'checkpoint' if kind == 'checkpoint_exit' else 'claim'
                    child = subprocess.run([sys.executable, __file__, '--child', mode, '--root', str(root)],
                                           cwd=ROOT, capture_output=True, timeout=30)
                    assert child.returncode == 9
                    with queue.connect() as db:
                        db.execute('UPDATE durable_jobs SET lease=0')
                if kind in {'queued_restart', 'claimed_exit', 'checkpoint_exit'}:
                    child = subprocess.run([sys.executable, __file__, '--child', 'recover', '--root', str(root)],
                                           cwd=ROOT, capture_output=True, timeout=30)
                    assert child.returncode == 0, child.stderr.decode(errors='replace')[-500:]
                    passed = access.task('alice', row['id'])['status'] == 'complete'
                    if kind == 'checkpoint_exit':
                        passed = passed and (root / 'calls.txt').read_text().count('prepare') == 1
                elif kind == 'duplicate':
                    passed = access.reserve('alice', request)[0]['id'] == row['id']
                    with queue.connect() as db:
                        passed = passed and db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 1
                elif kind == 'claim_race':
                    from concurrent.futures import ThreadPoolExecutor
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        jobs = list(pool.map(lambda i: queue.claim(str(i)), range(4)))
                    passed = sum(j is not None for j in jobs) == 1
                elif kind == 'stale_lease':
                    old = queue.claim('old')
                    with queue.connect() as db:
                        db.execute('UPDATE durable_jobs SET lease=0')
                    new = queue.claim('new')
                    passed = not queue.finish(old, fixture_state(row['id'])) and queue.finish(new, fixture_state(row['id']))
                elif kind == 'cancel':
                    job = queue.claim('worker'); queue.cancel('alice', row['id'])
                    passed = not queue.finish(job, fixture_state(row['id'])) and queue.claim('other') is None
                else:
                    from finevolab.subtasks import SubtaskJournal
                    from finevolab.runtime import RunBudget, BudgetExceeded
                    journal = SubtaskJournal(root / 'journal', {'task': row['id']}, RunBudget())
                    journal.reserve()
                    try:
                        SubtaskJournal(root / 'journal', {'task': row['id']}, RunBudget()).reserve()
                        passed = False
                    except BudgetExceeded:
                        passed = True
                rows.append({'kind': kind, 'repeat': repeat, 'passed': passed, 'seconds': time.perf_counter() - started,
                             'process_exit_injected': kind in {'claimed_exit', 'checkpoint_exit'}})
    return rows


def restart_comparison():
    rows = []
    for stage in ['claim', 'checkpoint']:
        for repeat in range(5):
            for arm in ['inline', 'worker']:
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    settings = policy()
                    settings['execution_mode'] = arm
                    access = AccessStore(root / 'output/experience', settings)
                    row, _ = access.reserve('alice', AnalysisRequest(question='MSFT FY2024 revenue', request_id='request-0000000001'))
                    child_stage = stage if arm == 'worker' else 'inline_' + stage
                    killed = subprocess.run([sys.executable, __file__, '--child', child_stage, '--root', str(root)],
                                            cwd=ROOT, capture_output=True, timeout=30)
                    assert killed.returncode == 9
                    if arm == 'worker':
                        with access.queue.connect() as db:
                            db.execute('UPDATE durable_jobs SET lease=0')
                        recovered = subprocess.run([sys.executable, __file__, '--child', 'recover', '--root', str(root)],
                                                   cwd=ROOT, capture_output=True, timeout=30)
                        assert recovered.returncode == 0
                        automatic = access.task('alice', row['id'])['status'] == 'complete'
                    else:
                        state_path = root / 'output/tasks' / row['id'] / 'state.json'
                        automatic = (access.task('alice', row['id'])['status'] == 'complete'
                                     and state_path.exists() and json.loads(state_path.read_text())['status'] == 'complete')
                        asyncio.run(checkpoint_job(root, {'task_id': row['id']}))
                    state = json.loads((root / 'output/tasks' / row['id'] / 'state.json').read_text())
                    rows.append({'arm': arm, 'stage': stage, 'repeat': repeat, 'automatic_complete': automatic,
                                 'final_complete': state['status'] == 'complete',
                                 'manual_resume': arm == 'inline',
                                 'prepare_calls': (root / 'calls.txt').read_text().count('prepare')})
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'output/memory_worker')
    parser.add_argument('--child', choices=['claim', 'checkpoint', 'recover', 'inline_claim', 'inline_checkpoint'])
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    if args.child:
        if args.child.startswith('inline_'):
            if args.child == 'inline_claim':
                os._exit(9)
            with AccessStore(args.root / 'output/experience', policy()).connect() as db:
                task_id = db.execute('SELECT id FROM tasks').fetchone()[0]
            asyncio.run(checkpoint_job(args.root, {'task_id': task_id}, True))
            return
        access = AccessStore(args.root / 'output/experience', policy())
        if args.child == 'claim':
            access.queue.claim('terminated'); os._exit(9)
        asyncio.run(execute_one(args.root, policy(), runner=lambda job: checkpoint_job(args.root, job, args.child == 'checkpoint')))
        return
    dataset = ROOT / 'data/evaluation/memory_worker/cases.json'
    cases = json.loads(dataset.read_text(encoding='utf-8'))
    paths = [ROOT / 'finevolab' / name for name in ['business_memory.py', 'durable_tasks.py', 'task_worker.py', 'experience.py']]
    paths += [Path(__file__), dataset]
    manifest = {'run_id': 'memory-worker-cpu', 'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'source_sha256': {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        'data_sha256': hashlib.sha256((ROOT / 'data/processed/reports.json').read_bytes()).hexdigest(),
        'scope': 'CPU context resolution and controlled task recovery; no model-quality score',
        'model_calls': 0, 'repeats': 3, 'fault_repeats': 5}
    memory, faults = memory_measurements(cases), fault_measurements()
    restarts = restart_comparison()
    if any(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != value
           for name, value in manifest['source_sha256'].items()):
        raise ValueError('Measured source files changed during execution')
    summary = {'memory': {arm: {'passed': sum(r['passed'] for r in memory if r['arm'] == arm),
                               'total': sum(r['arm'] == arm for r in memory)} for arm in ['off', 'on']},
               'faults': {'passed': sum(r['passed'] for r in faults), 'total': len(faults)}}
    summary['restart'] = {arm: {'automatic_complete': sum(r['automatic_complete'] for r in restarts if r['arm'] == arm),
                         'final_complete': sum(r['final_complete'] for r in restarts if r['arm'] == arm), 'total': 10}
                         for arm in ['inline', 'worker']}
    write_json(args.output / 'results.json', {'manifest': manifest, 'memory': memory, 'faults': faults, 'restarts': restarts, 'summary': summary})
    print(json.dumps(summary))
    if not all(r['passed'] for r in faults):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
