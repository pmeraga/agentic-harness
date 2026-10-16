import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import pytest
from click.testing import CliRunner

from agentic_harness.adapters import CommandAgent, CommandBenchmark, Task
from agentic_harness.cli import cli, ROOT
from agentic_harness.store import Store
from agentic_harness.worker import work
from agentic_harness.runtime import DockerRuntime, execute
from fixture_agent import solve


class LocalTestRuntime:
    """Only used by tests: execute real trusted benchmark scripts locally."""
    def __init__(self, job, directory):
        self.job, self.directory = job, directory
        self.workspace = directory / 'workspace'
    def __enter__(self):
        self.workspace.mkdir(parents=True)
        return self
    def __exit__(self, *_):
        pass
    def script(self, bench, phase, task):
        command = bench.command(phase, task, str(self.workspace), str(bench.root))
        command[0] = sys.executable
        return subprocess.run(command, capture_output=True, text=True, timeout=60)
    def prepare(self, bench, task):
        result = self.script(bench, 'setup', task)
        assert result.returncode == 0, result.stderr
    def agent(self, agent, task):
        solve(task.id, self.workspace)
    def evaluate(self, bench, task):
        result = self.script(bench, 'evaluate', task)
        return bench.evaluate(result.stdout, result.returncode, task)


def submit(store, tmp, benchmark='bash-operations', agent=None):
    bench = CommandBenchmark(ROOT / 'benchmarks' / benchmark)
    config = {'benchmark': str(bench.root), 'agent': agent or {'command': ['true']},
              'artifacts': str(tmp / 'artifacts'), 'timeout': 30, 'cpus': 1, 'memory': '1g'}
    return store.submit(config, [asdict(t) for t in bench.tasks()])


@pytest.mark.parametrize('benchmark,total', [('bash-operations', 5), ('python-tasks', 3)])
def test_real_benchmarks_through_queue(tmp_path, benchmark, total):
    store = Store(tmp_path / 'queue.db')
    run_id = submit(store, tmp_path, benchmark)
    assert store.get(run_id)['state'] == 'queued'
    work(store, concurrency=3, drain=True, runtime_factory=LocalTestRuntime)
    result = Store(store.path).get(run_id)
    assert result['state'] == 'completed', result
    assert result['passed'] == total
    assert result['accuracy'] == result['mean_score'] == 1


def test_atomic_claim_across_connections(tmp_path):
    store = Store(tmp_path / 'queue.db')
    run_id = submit(store, tmp_path)
    def claim(i):
        return Store(store.path).claim(str(i))
    with ThreadPoolExecutor(max_workers=12) as pool:
        jobs = list(pool.map(claim, range(20)))
    claimed = [j for j in jobs if j]
    assert len(claimed) == len({j['id'] for j in claimed}) == 5
    assert store.get(run_id)['counts']['running'] == 5


def test_stale_worker_is_fenced(tmp_path):
    store = Store(tmp_path / 'queue.db')
    run_id = submit(store, tmp_path)
    job = store.claim('old')
    with store.connect() as db:
        db.execute('UPDATE jobs SET heartbeat=0 WHERE id=?', (job['id'],))
    assert store.reap() == 1
    assert not store.finish(job['id'], 'old', result={'passed': True})
    assert not store.heartbeat(job['id'], 'old')
    assert store.get(run_id)['counts']['error'] == 1


def test_infrastructure_error_does_not_stop_queue(tmp_path):
    class Broken(LocalTestRuntime):
        def agent(self, agent, task):
            if task.id == 'bash-002':
                raise RuntimeError('test failure')
            super().agent(agent, task)
    store = Store(tmp_path / 'queue.db')
    run_id = submit(store, tmp_path)
    work(store, drain=True, runtime_factory=Broken)
    result = store.get(run_id)
    assert result['state'] == 'error'
    assert result['passed'] == 4 and result['accuracy'] == .8


def test_failed_grade_is_completed_not_error(tmp_path):
    class Noop(LocalTestRuntime):
        def agent(self, agent, task):
            pass
    store = Store(tmp_path / 'queue.db')
    run_id = submit(store, tmp_path)
    work(store, drain=True, runtime_factory=Noop)
    report = store.get(run_id)
    assert report['state'] == 'completed'
    assert report['passed'] == 0
    assert all(t['result']['passed'] is False for t in report['tasks'])


@pytest.mark.parametrize('bad', [
    {'task_id': 'x', 'passed': True, 'score': 1},
    {'task_id': 'bash-001', 'passed': 'yes', 'score': 1},
    {'task_id': 'bash-001', 'passed': True, 'score': float('nan')},
    {'task_id': 'bash-001', 'passed': True, 'score': 2},
])
def test_malformed_grades_rejected(bad):
    bench = CommandBenchmark(ROOT / 'benchmarks/bash-operations')
    with pytest.raises(ValueError):
        bench.evaluate(json.dumps(bad), 0, bench.tasks()[0])


def test_python_grading_ignores_modified_tests(tmp_path):
    bench = CommandBenchmark(ROOT / 'benchmarks/python-tasks')
    (tmp_path / 'solution.py').write_text('def add(): return 5\n')
    (tmp_path / 'test_solution.py').write_text('def test_cheat(): assert True\n')
    command = bench.command('evaluate', bench.tasks()[0], str(tmp_path), str(bench.root))
    command[0] = sys.executable
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0  # Python grader's intentional convention
    assert bench.evaluate(result.stdout, 0, bench.tasks()[0])['passed'] is False


def test_submission_snapshot_and_cli(tmp_path):
    runner = CliRunner()
    cfg = tmp_path / 'agent.json'
    cfg.write_text(json.dumps({'command': ['echo', '{prompt}']}))
    args = ['--data-dir', str(tmp_path / 'data')]
    result = runner.invoke(cli, args + ['run', '--agent', str(cfg), '--benchmark', 'bash-operations', '--task_ids', 'bash-001'])
    assert result.exit_code == 0, result.output
    run_id = result.output.strip()
    assert (tmp_path / 'data/runs' / run_id / 'benchmark/dataset.json').exists()
    status = runner.invoke(cli, args + ['status', '--run_id', run_id])
    assert json.loads(status.output)['state'] == 'queued'
    assert len(json.loads(runner.invoke(cli, args + ['list', '--active']).output)) == 1
    for fmt in ('json', 'table', 'summary'):
        assert runner.invoke(cli, args + ['results', '--run_id', run_id, '--format', fmt]).exit_code == 0
    for ids in ('missing', '', 'bash-001,bash-001'):
        assert runner.invoke(cli, args + ['run', '--agent', str(cfg), '--benchmark', 'bash-operations', '--task_ids', ids]).exit_code != 0
    assert runner.invoke(cli, args + ['status', '--run_id', 'missing']).exit_code != 0


def test_concurrency_bound(tmp_path):
    lock = threading.Lock()
    active = peak = 0
    class Measured(LocalTestRuntime):
        def agent(self, agent, task):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(.1)
            super().agent(agent, task)
            with lock:
                active -= 1
    store = Store(tmp_path / 'queue.db')
    submit(store, tmp_path)
    work(store, concurrency=2, drain=True, runtime_factory=Measured)
    assert peak == 2


def test_timeout_kills_subprocess(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        execute([sys.executable, '-c', 'import time; time.sleep(10)'], .05, tmp_path / 'out.log')


def test_docker_cleanup_on_timeout(tmp_path, monkeypatch):
    job = {'id': 1, 'run_id': 'test', 'config': {'timeout': 1, 'cpus': 1, 'memory': '1g',
            'agent': {'command': ['true'], 'image': 'image'}}}
    runtime = DockerRuntime(job, tmp_path)
    calls = []
    def fake(args, phase, *a, **kw):
        calls.append(args)
        if args[0] == 'run':
            raise subprocess.TimeoutExpired(args, 1)
        return 0, ''
    monkeypatch.setattr(runtime, '_docker', fake)
    with pytest.raises(RuntimeError, match='timed out'):
        with runtime:
            runtime.agent(CommandAgent(job['config']['agent']), Task('id', 'prompt', 'image'))
    assert any(c[:2] == ['rm', '-f'] for c in calls)
    assert any(c[:2] == ['volume', 'rm'] for c in calls)
    command = next(c for c in calls if c[0] == 'run')
    assert '--cap-drop' in command
    assert not any('/benchmark' in x for x in command)


@pytest.mark.docker
@pytest.mark.skipif(os.getenv('HARNESS_DOCKER_TEST') != '1', reason='Run make smoke on a Docker-enabled machine')
@pytest.mark.parametrize('benchmark,total', [('bash-operations', 5), ('python-tasks', 3)])
def test_docker_full_lifecycle(tmp_path, benchmark, total):
    store = Store(tmp_path / 'queue.db')
    run_id = submit(store, tmp_path, benchmark, {'command': ['python', '/opt/fixture_agent.py', '{task_id}'],
                                               'image': 'agentic-harness-fixture:1'})
    work(store, concurrency=2, drain=True)
    result = store.get(run_id)
    assert result['passed'] == total, result
    assert result['state'] == 'completed'


def test_multiple_runs_persist_and_drain(tmp_path):
    store = Store(tmp_path / 'queue.db')
    first = submit(store, tmp_path / 'first')
    second = submit(store, tmp_path / 'second', 'python-tasks')
    assert len(store.list(active=True)) == 2
    # A new store instance represents a worker starting after the submitter exits.
    work(Store(store.path), concurrency=3, drain=True, runtime_factory=LocalTestRuntime)
    assert not store.list(active=True)
    assert store.get(first)['passed'] == 5
    assert store.get(second)['passed'] == 3


def test_custom_benchmark_and_prompt_argv(tmp_path):
    (tmp_path / 'harness.json').write_text(json.dumps({
        'id_field': 'key', 'prompt_field': 'question',
        'setup': ['tool', '{workspace}', '{task_id}'],
        'evaluate': ['grade', '{benchmark}', '{task_id}']}))
    prompt = 'hello; $(touch forbidden) "quoted" {braces}'
    (tmp_path / 'dataset.json').write_text(json.dumps([
        {'key': 'custom', 'question': prompt, 'docker_image': 'custom:1'}]))
    bench = CommandBenchmark(tmp_path)
    task = bench.tasks()[0]
    agent = CommandAgent({'command': ['agent', '--task', '{prompt}']})
    assert agent.command(task) == ['agent', '--task', prompt]
    assert bench.command('setup', task, '/workspace', '/benchmark') == ['tool', '/workspace', 'custom']


def test_secret_values_not_in_docker_argv(tmp_path, monkeypatch):
    monkeypatch.setenv('TEST_KEY', 'secret-value')
    monkeypatch.setenv('UNRELATED_SECRET', 'do-not-forward')
    job = {'id': 1, 'run_id': 'test', 'config': {'timeout': 1, 'cpus': 1, 'memory': '1g',
            'agent': {'command': ['agent', '{prompt}'], 'image': 'image', 'env': ['TEST_KEY']}}}
    runtime = DockerRuntime(job, tmp_path)
    calls = []
    def fake(args, *a, **kw):
        calls.append(args)
        return 0, ''
    monkeypatch.setattr(runtime, '_docker', fake)
    with runtime:
        runtime.agent(CommandAgent(job['config']['agent']), Task('id', 'prompt', 'image'))
    command = next(c for c in calls if c[0] == 'run')
    assert 'TEST_KEY' in command
    assert 'secret-value' not in command
    assert 'UNRELATED_SECRET' not in command


def test_graceful_stop_leaves_queued_work(tmp_path):
    store = Store(tmp_path / 'queue.db')
    run_id = submit(store, tmp_path)
    stop = threading.Event()
    class StopsAfterFirst(LocalTestRuntime):
        def agent(self, agent, task):
            solve(task.id, self.workspace)
            stop.set()
    work(store, concurrency=1, stop=stop, runtime_factory=StopsAfterFirst)
    report = store.get(run_id)
    assert report['counts']['completed'] == 1
    assert report['counts']['queued'] == 4


@pytest.mark.parametrize('status,ok', [('Submitted', True), ('LimitsExceeded', False), ('RuntimeError', False)])
def test_mini_wrapper_checks_trajectory(tmp_path, monkeypatch, status, ok):
    import importlib.util
    spec = importlib.util.spec_from_file_location('run_mini', ROOT / 'docker/run_mini.py')
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    trajectory = tmp_path / 'trajectory.json'
    def call(command):
        trajectory.write_text(json.dumps({'info': {'exit_status': status}}))
        assert command[1:3] == ['--exit-immediately', '-y']
        return 0
    monkeypatch.setattr(wrapper.subprocess, 'call', call)
    if ok:
        wrapper.main('prompt', trajectory)
    else:
        with pytest.raises(SystemExit, match=status):
            wrapper.main('prompt', trajectory)


def test_env_config_rejects_secret_assignment():
    with pytest.raises(ValueError, match='environment variable names'):
        CommandAgent({'command': ['agent'], 'env': ['API_KEY=secret']})
