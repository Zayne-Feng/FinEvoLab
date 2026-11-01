"""Verify acceptance exits and business intent checks against controlled results."""

import asyncio
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from scripts import benchmark_memory_worker as benchmark
from scripts import verify_worker_service as service


class WorkerAcceptanceTests(unittest.TestCase):
    def benchmark_result(self, broken=None):
        memory = [{'arm': arm, 'passed': arm == 'on'} for arm in ('off', 'on') for _ in range(90)]
        faults = [{'passed': True} for _ in range(40)]
        restarts = [{'arm': arm, 'automatic_complete': arm == 'worker', 'final_complete': True}
                    for arm in ('inline', 'worker') for _ in range(10)]
        if broken == 'memory':
            memory[-1]['passed'] = False
        elif broken == 'fault':
            faults[-1]['passed'] = False
        elif broken == 'automatic':
            restarts[-1]['automatic_complete'] = False
        elif broken == 'final':
            restarts[0]['final_complete'] = False
        elif broken == 'missing':
            restarts.pop()
        with tempfile.TemporaryDirectory() as directory:
            with patch('sys.argv', ['benchmark_memory_worker', '--output', directory]), \
                 patch.object(benchmark, 'memory_measurements', return_value=memory), \
                 patch.object(benchmark, 'fault_measurements', return_value=faults), \
                 patch.object(benchmark, 'restart_comparison', return_value=restarts), redirect_stdout(io.StringIO()):
                code = 0
                try:
                    benchmark.main()
                except SystemExit as error:
                    code = error.code
            return code, json.loads((Path(directory) / 'results.json').read_text())

    def test_benchmark_rejects_failed_or_missing_acceptance_results(self):
        for broken in ('memory', 'fault', 'automatic', 'final', 'missing'):
            with self.subTest(broken=broken):
                code, result = self.benchmark_result(broken)
                self.assertEqual(code, 1)
                self.assertFalse(result['passed'])

    def test_benchmark_allows_unsuccessful_control_arm(self):
        code, result = self.benchmark_result()
        self.assertEqual(code, 0)
        self.assertTrue(result['passed'])

    def service_result(self, detail, case):
        client_type = httpx.AsyncClient
        submitted = False
        def respond(request):
            nonlocal submitted
            path = request.url.path
            if path.endswith('/session'):
                body = {'execution_mode': 'worker', 'daily_tasks': 5, 'used': int(submitted),
                        'budget': {'timeout_seconds': 10}}
            elif path.endswith('/memory/preferences'):
                body = json.loads(request.content)
            elif path.endswith('/tasks') and request.method == 'GET':
                return httpx.Response(401)
            elif path.endswith('/tasks'):
                body = {'task_id': 'fixture-task', 'reused': submitted}
                submitted = True
            elif '/report/' in path:
                return httpx.Response(200, content=b'%PDF-fixture' if path.endswith('/pdf') else b'<html>fixture</html>')
            elif path.endswith('/fixture-task'):
                return httpx.Response(200, json=detail, headers={'traceparent': 'fixture-trace'})
            elif path.endswith(('/login', '/logout')):
                body = {}
            else:
                raise AssertionError(f'Unexpected request: {request.method} {path}')
            return httpx.Response(200, json=body)
        output_root = service.ROOT / 'output'
        output_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output_root) as directory:
            root = Path(directory)
            invitation = root / 'invitation.txt'
            invitation.write_text('fixture-invitation', encoding='utf-8')
            args = SimpleNamespace(output=root / 'result', invitation_file=invitation,
                                   cases=[case], url='http://fixture')
            settings = SimpleNamespace(model='fixture', extra_body={})
            with patch.object(service.ModelConfig, 'load', return_value=settings), \
                 patch.object(service.httpx, 'AsyncClient', side_effect=lambda **kwargs: client_type(
                     **kwargs, transport=httpx.MockTransport(respond))), redirect_stdout(io.StringIO()):
                code = 0
                try:
                    asyncio.run(service.verify(args))
                except SystemExit as error:
                    code = error.code
            return code, json.loads((args.output / 'results.json').read_text())

    def test_service_rejects_wrong_derived_metric_and_calculation_identity(self):
        detail = {'status': 'complete', 'business_status': 'complete', 'validation': {'status': 'passed'},
            'request': {'companies': ['MSFT'], 'fiscal_year': 2024, 'metrics': ['operating_margin']},
            'answer': {'calculations': [{'kind': 'derived', 'metric': 'operating_margin',
                                        'company': 'MSFT', 'fiscal_year': 2024, 'unit': 'percent'}]},
            'execution_attempts': 1, 'presentation': {'preferences': {'amount_scale': 'millions'}}}
        for field, value in (('metric', 'net_margin'), ('company', 'AAPL'),
                             ('fiscal_year', 2023), ('unit', 'percentage_points'), ('plan', 'net_margin')):
            with self.subTest(field=field):
                wrong = deepcopy(detail)
                if field == 'plan':
                    wrong['request']['metrics'] = [value]
                else:
                    wrong['answer']['calculations'][0][field] = value
                code, result = self.service_result(wrong, 'derived')
                self.assertEqual(code, 1)
                self.assertFalse(result['records'][0]['context_correct'])
        code, result = self.service_result(detail, 'derived')
        self.assertEqual(code, 0)
        self.assertTrue(result['passed'])

    def test_service_rejects_hybrid_without_growth_intent(self):
        detail = {'status': 'complete', 'business_status': 'complete', 'validation': {'status': 'passed'},
            'request': {'companies': ['MSFT'], 'fiscal_year': 2024, 'metrics': ['revenue'], 'numeric_intent': 'metric'},
            'answer_type': 'hybrid', 'narrative': {'claims': [{'text': 'fixture'}]},
            'execution_attempts': 1, 'presentation': {'preferences': {'amount_scale': 'millions'}}}
        code, result = self.service_result(detail, 'hybrid')
        self.assertEqual(code, 1)
        self.assertFalse(result['records'][0]['context_correct'])
        detail['request']['numeric_intent'] = 'growth'
        code, result = self.service_result(detail, 'hybrid')
        self.assertEqual(code, 0)
        self.assertTrue(result['passed'])


if __name__ == '__main__':
    unittest.main()
