"""Execute leased financial jobs using version-bound workflow checkpoints."""

import asyncio
import hashlib
from pathlib import Path
from uuid import uuid4

from filelock import FileLock, Timeout

from finevolab.business_memory import resolve
from finevolab.durable_tasks import TaskQueue
from finevolab.runtime import RunBudget, fingerprint, read_json
from finevolab.tracing import trace_scope, flush_exports


def input_identity(root, policy):
    names = ["data/processed/reports.json", "data/indexes/contextual/structured.json", "prompts/benchmark.json"]
    names += [p.relative_to(root).as_posix() for folder in ('finevolab', 'configs')
              for p in (root / folder).glob('*') if p.suffix in {'.py', '.json'} and p.name != 'readme_sync.json']
    model = None
    if (root / 'prompts/benchmark.json').exists():
        from finevolab.config import ModelConfig
        settings = ModelConfig.load()
        model = {'model': settings.model, 'base_url': settings.base_url, 'options': settings.extra_body}
    return fingerprint({"policy": policy, "model": model, "files": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                        for name in names if (root / name).is_file()}})


async def execute_one(root: Path, policy, worker=None, runner=None, lease_seconds=30):
    queue = TaskQueue(root / 'output/experience/access.sqlite3')
    job = queue.claim(worker or uuid4().hex, policy['concurrent_tasks'], lease_seconds)
    if job is None:
        return False
    task = None
    lock = FileLock(str(root / 'output/experience' / (job['task_id'] + '.dispatch.lock')))
    async def run():
        while True:
            try:
                lock.acquire(timeout=0)
                break
            except Timeout:
                await asyncio.sleep(min(.2, lease_seconds / 4))
        try:
            if job['payload']['identity'] != input_identity(root, policy):
                raise ValueError('Task input identity changed')
            if runner:
                return await runner(job)
            from finevolab.config import ModelConfig
            from finevolab.workflow import run_question
            budget = RunBudget(**job['payload']['budget'])
            return await run_question(job['task_id'], resolve(job['payload']['question'], job['payload']['memory']),
                ModelConfig.load(), job['payload']['candidate'], root=root, budget=budget,
                timeout_seconds=budget.timeout_seconds, tool_backend=job['payload']['tool_backend'])
        finally:
            lock.release()
    with trace_scope('worker.execute', root / 'output/traces', incoming=job['traceparent']):
        task = asyncio.create_task(run())
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=min(1, lease_seconds / 4))
                if not queue.heartbeat(job, lease_seconds):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    return True
            try:
                state = task.result()
            except Exception as error:
                queue.finish(job, error=type(error).__name__)
            else:
                queue.finish(job, state=state)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            flush_exports()
    return True


async def serve(root, policy, once=False):
    worker = uuid4().hex
    while True:
        found = await execute_one(root, policy, worker)
        if once:
            return
        if not found:
            await asyncio.sleep(.5)
