"""Bounded task execution, heartbeat ownership, graceful shutdown."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import uuid

from .adapters import CommandAgent, load_benchmark, Task
from .runtime import DockerRuntime


def perform(store, job, runtime_factory=DockerRuntime):
    done = threading.Event()

    def heartbeat():
        while not done.wait(10):
            if not store.heartbeat(job['id'], job['owner']):
                return

    pulse = threading.Thread(target=heartbeat, daemon=True)
    pulse.start()
    directory = Path(job['config']['artifacts']) / str(job['id'])
    try:
        benchmark = load_benchmark(Path(job['config']['benchmark']))
        agent = CommandAgent(job['config']['agent'])
        task = Task(**job['task'])
        with runtime_factory(job, directory) as runtime:
            runtime.prepare(benchmark, task)
            runtime.agent(agent, task)
            result = runtime.evaluate(benchmark, task)
        store.finish(job['id'], job['owner'], result=result)
    except Exception as exc:
        # Credentials may appear in SDK error messages; redact known allowlisted values.
        import os
        message = f'{type(exc).__name__}: {exc}'
        for name in job['config']['agent'].get('env', []):
            value = os.environ.get(name)
            if value:
                message = message.replace(value, '[REDACTED]')
        store.finish(job['id'], job['owner'], error=message)
    finally:
        done.set()
        pulse.join()


def work(store, concurrency=2, drain=False, stop=None, run_id=None, runtime_factory=DockerRuntime):
    stop = stop or threading.Event()

    def loop():
        owner = uuid.uuid4().hex
        while not stop.is_set():
            store.reap()
            job = store.claim(owner, run_id)
            if job:
                perform(store, job, runtime_factory)
            elif drain:
                return
            else:
                stop.wait(1)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(loop) for _ in range(concurrency)]
        for future in futures:
            future.result()
