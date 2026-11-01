"""Verify invited analysis through a running persistent worker service."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finevolab.config import ModelConfig
from finevolab.runtime import write_json

CASES = [
    ('metric', 'Microsoft FY2024 revenue', ['MSFT'], 2024, ['revenue']),
    ('followup', '上一年呢？', ['MSFT'], 2023, ['revenue']),
    ('hybrid', 'Calculate MSFT FY2024 revenue growth and explain the disclosed reasons using annual report passages.', ['MSFT'], 2024, ['revenue']),
    ('comparison', 'Compare MSFT and AAPL FY2024 revenue and state their fiscal period differences.', ['MSFT', 'AAPL'], 2024, ['revenue']),
    ('derived', 'Calculate MSFT FY2024 operating margin.', ['MSFT'], 2024, ['operating_margin']),
]


async def verify(args):
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / 'output').resolve()) or output.exists():
        raise ValueError('A new output directory is required')
    output.mkdir(parents=True)
    cases = [case for case in CASES if case[0] in args.cases]
    if 'followup' in args.cases and 'metric' not in args.cases:
        raise ValueError('The follow-up requires its metric anchor')
    invitation = args.invitation_file.read_text(encoding='utf-8-sig').strip()
    measured = [*sorted((ROOT / 'finevolab').glob('*.py')), Path(__file__), ROOT / 'scripts/manage_experience.py']
    hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in measured}
    settings = ModelConfig.load()
    result = {'manifest': {'run_id': 'worker-daily-service', 'base_commit': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(), 'source_sha256': hashes,
        'model': settings.model, 'options': settings.extra_body,
        'data_sha256': hashlib.sha256((ROOT / 'data/processed/reports.json').read_bytes()).hexdigest(),
        'case_ids': [case[0] for case in cases],
        'scope': 'Real-model daily-service integration tasks; no generalized accuracy estimate'},
        'records': [], 'checks': {}}
    headers = {'X-FinEvoLab': '1'}
    async with httpx.AsyncClient(base_url=args.url, trust_env=False, timeout=30, headers=headers) as client:
        result['checks']['anonymous_rejected'] = (await client.get('/experience/api/tasks')).status_code == 401
        response = await client.post('/experience/api/login', json={'invite': invitation})
        response.raise_for_status()
        session = (await client.get('/experience/api/session')).json()
        result['checks']['worker_mode'] = session['execution_mode'] == 'worker'
        if not result['checks']['worker_mode']:
            raise ValueError('The service must use worker execution mode')
        if session['daily_tasks'] - session['used'] < len(cases):
            raise ValueError('The existing invitation quota cannot cover the fixed cases')
        conversation = uuid4().hex
        preferences = session.get('preferences') or {'language': 'zh', 'amount_scale': 'units'}
        response = await client.put('/experience/api/memory/preferences',
                                    json={'language': 'en', 'amount_scale': 'millions'})
        response.raise_for_status()
        result['checks']['preferences_saved'] = response.json()['amount_scale'] == 'millions'
        async def run_case(case):
            name, question, companies, year, metrics = case
            body = {'question': question, 'request_id': 'worker-live-' + uuid4().hex,
                    'conversation_id': conversation if name in {'metric', 'followup'} else uuid4().hex,
                    'use_memory': name in {'metric', 'followup'}}
            started = time.perf_counter()
            response = await client.post('/experience/api/tasks', json=body)
            response.raise_for_status()
            created = response.json()
            duplicate = await client.post('/experience/api/tasks', json=body)
            duplicate.raise_for_status()
            elapsed_limit = session['budget']['timeout_seconds'] * len(cases) + 120
            while time.perf_counter() - started < elapsed_limit:
                detail_response = await client.get('/experience/api/tasks/' + created['task_id'])
                detail_response.raise_for_status()
                detail = detail_response.json()
                if detail['status'] not in {'queued', 'running'}:
                    break
                await asyncio.sleep(.25)
            else:
                raise TimeoutError('The daily analysis task did not finish')
            plan = detail.get('request') or {}
            reports = {}
            for kind in ('html', 'pdf'):
                response = await client.get('/experience/api/tasks/' + created['task_id'] + '/report/' + kind)
                reports[kind] = response.status_code == 200 and (response.content.startswith(b'%PDF-') if kind == 'pdf' else b'<html' in response.content.lower())
            correct = set(plan.get('companies', [])) == set(companies) and plan.get('fiscal_year') == year
            correct &= all(metric in plan.get('metrics', []) for metric in metrics)
            if name == 'hybrid':
                correct &= (plan.get('numeric_intent') == 'growth' and detail.get('answer_type') == 'hybrid'
                            and bool((detail.get('narrative') or {}).get('claims')))
            if name == 'derived':
                correct &= any(item.get('kind') == 'derived' and item.get('metric') == 'operating_margin'
                               and item.get('company') == 'MSFT' and item.get('fiscal_year') == year
                               and item.get('unit') == 'percent'
                               for item in (detail.get('answer') or {}).get('calculations', []))
            record = {'case_id': name, 'question': question, 'status': detail['status'],
                'error_type': detail.get('error_type'), 'request': plan, 'answer_type': detail.get('answer_type'),
                'validation': detail.get('validation'), 'usage': detail.get('usage'),
                'business_status': detail.get('business_status'), 'evidence_count': len(detail.get('evidence', [])),
                'claim_count': len((detail.get('narrative') or {}).get('claims', [])),
                'cache_hits': sum(event.get('cache_hit', False) for event in detail.get('events', [])),
                'queue_seconds': detail.get('queue_seconds'), 'execution_attempts': detail.get('execution_attempts'),
                'elapsed_seconds': time.perf_counter() - started, 'reports': reports,
                'context_correct': bool(correct), 'reused': duplicate.json().get('reused', False),
                'same_task': duplicate.json()['task_id'] == created['task_id'],
                'display_scale': detail.get('presentation', {}).get('preferences', {}).get('amount_scale'),
                'trace_propagated': bool(detail_response.headers.get('traceparent'))}
            record['passed'] = (record['status'] == 'complete' and record['business_status'] == 'complete' and bool(correct)
                and record['validation']['status'] == 'passed' and all(reports.values())
                and record['reused'] and record['same_task'] and record['execution_attempts'] == 1
                and record['display_scale'] == 'millions' and record['trace_propagated'])
            result['records'].append(record)
            write_json(output / 'results.json', result)
            return detail
        try:
            for case in cases:
                if case[0] in {'metric', 'followup'}:
                    await run_case(case)
            if 'metric' in args.cases:
                memory = (await client.get('/experience/api/memory', params={'conversation_id': conversation})).json()
                result['checks']['context_persisted'] = memory['context'].get('companies') == ['MSFT']
            await asyncio.gather(*(run_case(case) for case in cases if case[0] not in {'metric', 'followup'}))
            ending = (await client.get('/experience/api/session')).json()
            result['checks']['quota_once'] = ending['used'] - session['used'] == len(cases)
        finally:
            response = await client.put('/experience/api/memory/preferences', json=preferences)
            result['checks']['preferences_restored'] = response.status_code == 200
            await client.post('/experience/api/logout')
    if any(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != value for name, value in hashes.items()):
        raise ValueError('Measured source files changed during execution')
    result['passed'] = len(result['records']) == len(cases) and all(row['passed'] for row in result['records']) and all(result['checks'].values())
    write_json(output / 'results.json', result)
    print(json.dumps({'passed': result['passed'], 'tasks': len(result['records']), 'checks': result['checks']}))
    if not result['passed']:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8085')
    parser.add_argument('--invitation-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'output/worker_service_live')
    parser.add_argument('--cases', nargs='+', choices=[case[0] for case in CASES],
                        default=[case[0] for case in CASES])
    args = parser.parse_args()
    asyncio.run(verify(args))


if __name__ == '__main__':
    main()
