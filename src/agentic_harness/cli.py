"""Submit now, execute in workers, query durable results from any process."""
import json
import os
from pathlib import Path
import re
import shutil
import signal
import threading
import uuid

import click

from .adapters import CommandAgent, CommandBenchmark, read_config
from .runtime import doctor
from .store import Store
from .worker import work

ROOT = Path(__file__).resolve().parents[2]


@click.group()
@click.option('--data-dir', type=click.Path(path_type=Path),
              default=lambda: Path(os.getenv('HARNESS_DATA_DIR', '.harness')),
              help='Queue database, source snapshots, and logs.')
@click.pass_context
def cli(ctx, data_dir):
    """Run independent agents against benchmark adapters in Docker."""
    ctx.obj = data_dir.resolve()


def checked(fn):
    """Present actionable errors without a Python traceback."""
    import functools
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
            raise click.ClickException(str(exc)) from exc
    return wrapper


@cli.command()
@click.option('--agent', required=True, help='mini-swe-agent, openai-responses, directory containing harness.json, or JSON config.')
@click.option('--benchmark', required=True, help='Bundled name or directory containing harness.json.')
@click.option('--task_ids', '--task-ids', default=None, help='Comma-separated task IDs; default all.')
@click.option('--wait', is_flag=True, help='Execute this run in the foreground after submission.')
@click.option('--concurrency', type=click.IntRange(1, 64), default=2)
@click.option('--timeout', type=click.IntRange(1, 86400), default=600, help='Seconds per task phase.')
@click.option('--cpus', type=click.FloatRange(min=0.1), default=1.0)
@click.option('--memory', default='2g', help='Docker memory limit, e.g. 2g.')
@click.option('--keep-workspaces', is_flag=True, help='Keep named Docker volumes for inspection.')
@click.pass_obj
@checked
def run(data, agent, benchmark, task_ids, wait, concurrency, timeout, cpus, memory, keep_workspaces):
    """Persist a benchmark run and return its ID immediately."""
    if not re.fullmatch(r'[1-9][0-9]*[bkmgBKMG]?', memory):
        raise ValueError('Memory must be a positive integer with optional b/k/m/g suffix')
    bench_path = Path(benchmark)
    if not bench_path.is_dir():
        bench_path = ROOT / 'benchmarks' / benchmark
    adapter = CommandBenchmark(bench_path)
    tasks = adapter.tasks()
    if task_ids is not None:
        requested = [x.strip() for x in task_ids.split(',')]
        if not all(requested) or len(set(requested)) != len(requested):
            raise ValueError('Task IDs must be nonempty and unique')
        unknown = set(requested) - {t.id for t in tasks}
        if unknown:
            raise ValueError(f'Unknown task IDs: {sorted(unknown)}')
        tasks = [t for t in tasks if t.id in requested]
    agent_path = Path(agent)
    if agent in ('mini-swe-agent', 'agents/mini-swe-agent'):
        agent_path = ROOT / 'configs' / 'agents' / 'mini-swe-agent.json'
    elif agent in ('openai-responses', 'agents/openai-responses'):
        agent_path = ROOT / 'configs' / 'agents' / 'openai-responses.json'
    elif agent_path.is_dir():
        agent_path /= 'harness.json'
    agent_config = read_config(agent_path)
    CommandAgent(agent_config)
    if wait:
        doctor()
    run_id = uuid.uuid4().hex
    artifacts = data / 'runs' / run_id
    snapshot = artifacts / 'benchmark'
    if artifacts.is_relative_to(adapter.root):
        raise ValueError('Data directory must be outside the benchmark source directory')
    # Copy source at submission time: queued work doesn't silently change when edited.
    shutil.copytree(adapter.root, snapshot, ignore=shutil.ignore_patterns('__pycache__', '.git', '.venv', '.env', '.harness', '.pytest_cache'))
    config = {'benchmark': str(snapshot), 'agent': agent_config, 'artifacts': str(artifacts),
              'timeout': timeout, 'cpus': cpus, 'memory': memory, 'keep_workspaces': keep_workspaces}
    from dataclasses import asdict
    store = Store(data / 'harness.db')
    store.submit(config, [asdict(t) for t in tasks], run_id)
    click.echo(run_id)
    if wait:
        work(store, concurrency, drain=True, run_id=run_id)
        report = store.get(run_id)
        click.echo(json.dumps(report, indent=2))
        if report['state'] == 'error':
            raise click.ClickException('Run contains infrastructure errors; see task errors and logs')


@cli.command()
@click.option('--concurrency', type=click.IntRange(1, 64), default=2)
@click.option('--drain', is_flag=True, help='Exit when no queued tasks remain.')
@click.pass_obj
@checked
def worker(data, concurrency, drain):
    """Pull tasks from the durable queue; Ctrl-C stops claiming and finishes active work."""
    doctor()
    stop = threading.Event()
    def shutdown(*_):
        stop.set()
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    work(Store(data / 'harness.db'), concurrency, drain, stop)


@cli.command()
@click.option('--run_id', '--run-id', required=True)
@click.pass_obj
@checked
def status(data, run_id):
    """View task counts and state for a run."""
    report = Store(data / 'harness.db').get(run_id)
    click.echo(json.dumps({k: v for k, v in report.items() if k != 'tasks'}, indent=2))


@cli.command('list')
@click.option('--active', is_flag=True, help='Only queued and running runs.')
@click.pass_obj
@checked
def list_runs(data, active):
    """List submitted runs, newest first."""
    reports = Store(data / 'harness.db').list(active)
    click.echo(json.dumps([{k: v for k, v in r.items() if k != 'tasks'} for r in reports], indent=2))


@cli.command()
@click.option('--run_id', '--run-id', required=True)
@click.option('--format', 'fmt', type=click.Choice(['json', 'table', 'summary'], case_sensitive=False), default='json')
@click.pass_obj
@checked
def results(data, run_id, fmt):
    """Retrieve results, including partial results for active runs."""
    report = Store(data / 'harness.db').get(run_id)
    if fmt.lower() == 'json':
        click.echo(json.dumps(report, indent=2))
    else:
        click.echo(f"{report['run_id']}  {report['state']}  {report['passed']}/{report['total']} passed  accuracy={report['accuracy']:.1%}  mean_score={report['mean_score']:.3f}")
        if fmt.lower() == 'table':
            click.echo('TASK                  STATE       PASSED  SCORE  ERROR')
            for task in report['tasks']:
                result = task['result'] or {}
                click.echo(f"{task['task_id']:<22}{task['state']:<12}{str(result.get('passed', '-')):<8}{str(result.get('score', '-')):<7}{task['error'] or ''}")


@cli.command('doctor')
@checked
def check_environment():
    """Check that Docker is installed and running."""
    click.echo('Docker server: ' + doctor())


def main():
    cli()


if __name__ == '__main__':
    main()
