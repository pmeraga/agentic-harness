"""Explicit adapter boundaries: no benchmark-specific branches in the worker."""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
import json
import math
import re


@dataclass(frozen=True)
class Task:
    id: str
    prompt: str
    image: str


class Benchmark(Protocol):
    def tasks(self) -> list[Task]: ...
    def command(self, phase: str, task: Task, workspace: str, root: str) -> list[str]: ...
    def evaluate(self, stdout: str, returncode: int, task: Task) -> dict: ...


class Agent(Protocol):
    def command(self, task: Task) -> list[str]: ...


def read_config(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f'{path}: expected a JSON object')
    return value


def argv(template, values):
    if not isinstance(template, list) or not template or not all(isinstance(x, str) for x in template):
        raise ValueError('Commands must be nonempty arrays of strings')
    return [x.format_map(values) for x in template]


class CommandBenchmark:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.config = read_config(self.root / 'harness.json')
        # Validate commands before accepting work.
        for phase in ('setup', 'evaluate'):
            self.command(phase, Task('id', 'prompt', 'image'), '/workspace', '/benchmark')

    def tasks(self):
        cfg = self.config
        dataset = (self.root / cfg.get('dataset', 'dataset.json')).resolve()
        if not dataset.is_relative_to(self.root):
            raise ValueError('Dataset must be inside the benchmark directory')
        records = json.loads(dataset.read_text())
        tasks = [Task(str(r[cfg['id_field']]), r[cfg['prompt_field']], r['docker_image']) for r in records]
        if not tasks or len({t.id for t in tasks}) != len(tasks):
            raise ValueError('Dataset must contain unique task IDs and at least one task')
        if any(not t.id or not isinstance(t.prompt, str) or not isinstance(t.image, str) for t in tasks):
            raise ValueError('Invalid task fields')
        return tasks

    def command(self, phase, task, workspace, root):
        return argv(self.config[phase], {'task_id': task.id, 'workspace': workspace, 'benchmark': root})

    def evaluate(self, stdout, returncode, task):
        if returncode not in self.config.get('evaluation_exit_codes', [0]):
            raise ValueError(f'Evaluator exited with unexpected code {returncode}')
        result = json.loads(stdout)
        score = result.get('score')
        if (result.get('task_id') != task.id or type(result.get('passed')) is not bool
                or type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1):
            raise ValueError('Evaluator returned an invalid result')
        return {'task_id': task.id, 'passed': result['passed'], 'score': float(score),
                'details': result.get('details', {})}


class CommandAgent:
    def __init__(self, config: dict):
        self.config = config
        self.command(Task('id', 'prompt', 'image'))
        if not isinstance(config.get('env', []), list) or not all(isinstance(x, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', x) for x in config.get('env', [])):
            raise ValueError('Agent env must list environment variable names, never secret values')

    def command(self, task):
        return argv(self.config['command'], {'prompt': task.prompt, 'task_id': task.id, 'workspace': '/workspace'})
