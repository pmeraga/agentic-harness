"""Adapter selection goes through the CLI and worker, not just the loader."""
import json
import os
import subprocess
import sys

import pytest
from click.testing import CliRunner

from agentic_harness.adapters import CommandBenchmark, JsonlBenchmark, load_benchmark
from agentic_harness.cli import ROOT, cli
from agentic_harness.store import Store
from agentic_harness.worker import work
from fixture_agent import solve


class LocalBenchmarkRuntime:
    """Only trusted benchmark scripts run locally in these unit tests."""
    def __init__(self, job, directory):
        self.job = job
        self.workspace = directory / 'workspace'

    def __enter__(self):
        self.workspace.mkdir(parents=True)
        return self

    def __exit__(self, *_):
        pass

    def prepare(self, benchmark, task):
        self.run_script(benchmark, task, 'setup')

    def agent(self, agent, task):
        solve(task.id, self.workspace)

    def evaluate(self, benchmark, task):
        result = self.run_script(benchmark, task, 'evaluate')
        return benchmark.evaluate(result.stdout, result.returncode, task)

    def run_script(self, benchmark, task, phase):
        argv = benchmark.command(phase, task, str(self.workspace), str(benchmark.root))
        argv[0] = sys.executable
        process = subprocess.run(argv, text=True, capture_output=True, timeout=30)
        if phase == 'setup':
            assert process.returncode == 0, process.stderr
        return process


def test_original_benchmarks_keep_default_adapter():
    for name in ('bash-operations', 'python-tasks'):
        benchmark = load_benchmark(ROOT / 'benchmarks' / name)
        assert type(benchmark) is CommandBenchmark


def test_jsonl_adapter_discovers_tasks():
    benchmark = load_benchmark(ROOT / 'benchmarks/jsonl-tasks')
    assert isinstance(benchmark, JsonlBenchmark)
    assert [task.id for task in benchmark.tasks()] == ['text-001', 'text-002']
    assert benchmark.tasks()[0].image == 'alpine:latest'
    assert benchmark.tasks()[1].image == 'python:3.11-slim'


def test_jsonl_benchmark_through_cli_and_worker(tmp_path):
    agent_file = tmp_path / 'agent.json'
    agent_file.write_text(json.dumps({'command': ['echo', '{prompt}']}))
    data = tmp_path / 'data'
    submit = CliRunner().invoke(cli, ['--data-dir', str(data), 'run',
        '--agent', str(agent_file), '--benchmark', 'jsonl-tasks'])
    assert submit.exit_code == 0, submit.output
    run_id = submit.output.strip()
    assert (data / 'runs' / run_id / 'benchmark' / 'tasks.jsonl').exists()
    # Loads the adapter again from the snapshotted config in a separate worker.
    work(Store(data / 'harness.db'), concurrency=2, drain=True,
         runtime_factory=LocalBenchmarkRuntime)
    report = Store(data / 'harness.db').get(run_id)
    assert report['state'] == 'completed', report
    assert report['passed'] == 2
    assert report['accuracy'] == 1.0


def test_adapter_entry_point_can_supply_custom_class(tmp_path, monkeypatch):
    config = {'adapter': 'third_party', 'id_field': 'id',
              'prompt_field': 'prompt', 'setup': ['echo', '{task_id}'],
              'evaluate': ['echo', '{task_id}']}
    (tmp_path / 'harness.json').write_text(json.dumps(config))
    (tmp_path / 'dataset.json').write_text(json.dumps([
        {'id': 'custom', 'prompt': 'hi', 'docker_image': 'python:3.11-slim'}]))

    class ThirdPartyBenchmark(CommandBenchmark):
        pass

    class FakeEntryPoint:
        name = 'third_party'
        def load(self):
            return ThirdPartyBenchmark

    monkeypatch.setattr('agentic_harness.adapters.entry_points',
                        lambda **kwargs: [FakeEntryPoint()])
    result = load_benchmark(tmp_path)
    assert isinstance(result, ThirdPartyBenchmark)
    assert result.tasks()[0].id == 'custom'


def test_unregistered_adapter_fails_clearly(tmp_path, monkeypatch):
    (tmp_path / 'harness.json').write_text(json.dumps({'adapter': 'does_not_exist'}))
    monkeypatch.setattr('agentic_harness.adapters.entry_points', lambda **kwargs: [])
    with pytest.raises(ValueError, match='Unknown or ambiguous benchmark adapter'):
        load_benchmark(tmp_path)


def test_jsonl_invalid_line_reports_location(tmp_path):
    original = ROOT / 'benchmarks/jsonl-tasks'
    (tmp_path / 'harness.json').write_text((original / 'harness.json').read_text())
    (tmp_path / 'tasks.jsonl').write_text('{"id": "ok"}\nnot-json\n')
    with pytest.raises(ValueError, match='line 2'):
        load_benchmark(tmp_path).tasks()


@pytest.mark.docker
@pytest.mark.skipif(os.getenv('HARNESS_DOCKER_TEST') != '1', reason='Run make smoke on a Docker-enabled machine')
def test_jsonl_docker_lifecycle(tmp_path):
    data = tmp_path / 'data'
    agent_file = tmp_path / 'fixture-agent.json'
    agent_file.write_text(json.dumps({
        'command': ['python', '/opt/fixture_agent.py', '{task_id}'],
        'image': 'agentic-harness-fixture:1'}))
    result = CliRunner().invoke(cli, ['--data-dir', str(data), 'run',
        '--agent', str(agent_file), '--benchmark', 'jsonl-tasks'])
    assert result.exit_code == 0, result.output
    run_id = result.output.strip()
    work(Store(data / 'harness.db'), concurrency=2, drain=True)
    report = Store(data / 'harness.db').get(run_id)
    assert report['state'] == 'completed', report
    assert report['passed'] == 2
