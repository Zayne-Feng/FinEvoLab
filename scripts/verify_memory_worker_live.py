"""Verify real model planning and reporting through an isolated worker process."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finevolab.experience import AccessStore, AnalysisRequest
from finevolab.runtime import write_json
from finevolab.config import ModelConfig


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'output/memory_worker_live')
    args = parser.parse_args()
    measured = [ROOT / 'finevolab' / name for name in ['business_memory.py', 'durable_tasks.py', 'task_worker.py', 'experience.py']]
    measured += [Path(__file__), ROOT / 'scripts/manage_experience.py']
    source_hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in measured}
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / 'output').resolve()) or output.exists():
        raise ValueError('A new workspace output directory is required')
    workspace = output / 'workspace'
    names = [p.relative_to(ROOT).as_posix() for folder in ['finevolab', 'configs', 'prompts', 'requirements']
             for p in (ROOT / folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc']
    names += ['data/processed/reports.json', 'data/indexes/contextual/structured.json',
              'data/indexes/contextual/structured.metadata.json', 'data/filing_sources.json']
    for name in names:
        if (ROOT / name).is_file():
            target = workspace / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
    policy = {'execution_mode': 'worker', 'enabled': True, 'daily_tasks': 20, 'concurrent_tasks': 1,
              'budget': {'max_model_calls': 6, 'max_tool_calls': 10, 'max_total_tokens': 20000,
                         'max_context_chars': 60000, 'timeout_seconds': 240},
              'users': {'alice': {'daily_tasks': 20, 'invite_sha256': 'a' * 64}}}
    config = output / 'access.json'
    write_json(config, policy)
    access = AccessStore(workspace / 'output/experience', policy)
    records = []
    questions = [('Microsoft FY2024 revenue', 'MSFT', 2024), ('上一年呢？', 'MSFT', 2023),
                 ('AAPL FY2024 net income', 'AAPL', 2024)]
    for arm in ['off', 'on']:
        for index, (question, company, year) in enumerate(questions):
            row, _ = access.reserve('alice', AnalysisRequest(question=question,
                request_id=f'live-{arm}-request-{index:04d}', conversation_id=arm, use_memory=arm == 'on'))
            started = time.perf_counter()
            child = subprocess.run([sys.executable, str(ROOT / 'scripts/manage_experience.py'), 'worker', '--once',
                '--root', str(workspace), '--config', str(config)], cwd=ROOT, capture_output=True, timeout=300)
            status = access.task('alice', row['id'])
            if child.returncode or status['status'] != 'complete':
                write_json(output / 'failure.json', {'arm': arm, 'index': index, 'status': status['status'],
                                                    'error_type': status['error_type']})
                raise RuntimeError('The isolated real worker did not complete')
            path = workspace / 'output/tasks' / row['id']
            state = json.loads((path / 'state.json').read_text(encoding='utf-8'))
            result = state['phases']['analyze']['result']
            plan = result['request']
            records.append({'arm': arm, 'index': index, 'question': question, 'request': plan,
                'context_correct': plan['companies'] == [company] and plan['fiscal_year'] == year,
                'validation': state['phases']['validate']['result'], 'usage': result.get('usage'),
                'report_pdf': (path / 'report/report.pdf').read_bytes().startswith(b'%PDF-'),
                'seconds': time.perf_counter() - started})
            write_json(output / 'results.json', {'run_id': 'memory-worker-live', 'records': records})
    settings = ModelConfig.load()
    if any(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest for name, digest in source_hashes.items()):
        raise ValueError('Measured source files changed during execution')
    manifest = {'model': settings.model, 'options': settings.extra_body,
        'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'source_sha256': source_hashes,
        'scope': 'Six real model worker smoke tasks; planning context and source validation, no narrative quality estimate'}
    write_json(output / 'results.json', {'run_id': 'memory-worker-live', 'manifest': manifest, 'records': records})
    assert all(r['context_correct'] for r in records if r['arm'] == 'on')
    print(json.dumps({'tasks': len(records), 'context_correct': {arm: sum(r['context_correct'] for r in records if r['arm'] == arm) for arm in ['off', 'on']}}))


if __name__ == '__main__':
    main()
