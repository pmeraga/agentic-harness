"""Docker lifecycle. The agent sees only its own named workspace volume."""
import os
from pathlib import Path
import subprocess
import uuid


class CommandError(RuntimeError):
    pass


def execute(command, timeout, log: Path, env=None):
    """Stream output to disk; bounded diagnostic tails avoid unbounded RAM use."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('wb') as stream:
        process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT,
                                   env=env, stdin=subprocess.DEVNULL)
        try:
            code = process.wait(timeout=timeout)
        except BaseException:
            process.kill()
            process.wait()
            raise
    with log.open('rb') as stream:
        stream.seek(max(0, log.stat().st_size - 1_000_000))
        tail = stream.read().decode(errors='replace')
    return code, tail


class DockerRuntime:
    def __init__(self, job, directory: Path):
        self.job = job
        self.directory = directory
        self.volume = f"harness-{job['run_id']}-{job['id']}"
        self.containers = []
        self.timeout = job['config']['timeout']
        self.agent_config = job['config']['agent']

    def _docker(self, args, phase, timeout=None, env=None, check=True):
        code, output = execute(['docker', *args], timeout or self.timeout,
                               self.directory / f'{phase}.log', env)
        if check and code:
            raise CommandError(f'{phase} exited {code}: {output[-4000:]}')
        return code, output

    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        self._docker(['volume', 'create', self.volume], 'volume', 30)
        return self

    def _run(self, image, command, phase, benchmark=None, agent=False):
        name = f'{self.volume}-{phase}-{uuid.uuid4().hex[:8]}'
        self.containers.append(name)
        args = ['run', '--name', name, '--init', '--cpus', str(self.job['config']['cpus']),
                '--memory', self.job['config']['memory'], '--pids-limit', '256',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--mount', f'type=volume,src={self.volume},dst=/workspace', '-w', '/workspace']
        if benchmark:
            args += ['--mount', f'type=bind,src={benchmark},dst=/benchmark,readonly', '--network', 'none']
        env = os.environ.copy()
        if agent:
            # Only explicitly allowlisted names reach the container; values never enter argv or DB.
            for key in self.agent_config.get('env', []):
                if key in os.environ:
                    args += ['--env', key]
            args += ['--env', 'MSWEA_CONFIGURED=true', '--env', 'MSWEA_GLOBAL_CONFIG_DIR=/tmp/mini']
        args += [image, *command]
        try:
            return self._docker(args, phase, env=env, check=False)
        except subprocess.TimeoutExpired:
            raise CommandError(f'{phase} timed out after {self.timeout}s') from None
        finally:
            # Killing docker CLI alone does not kill a running container.
            self._docker(['rm', '-f', name], f'{phase}-cleanup', 30, check=False)

    def prepare(self, benchmark, task):
        code, output = self._run(benchmark.config.get('evaluator_image', 'agentic-harness-evaluator:1'),
            benchmark.command('setup', task, '/workspace', '/benchmark'), 'setup', str(benchmark.root))
        if code:
            raise CommandError(f'Setup exited {code}: {output[-4000:]}')

    def agent(self, agent, task):
        image = self.agent_config.get('image') or self.agent_config.get('images', {}).get(task.image)
        if not image:
            raise ValueError(f'No agent runtime image configured for task image {task.image}')
        code, output = self._run(image, agent.command(task), 'agent', agent=True)
        if code:
            raise CommandError(f'Agent exited {code}: {output[-4000:]}')

    def evaluate(self, benchmark, task):
        code, output = self._run(benchmark.config.get('evaluator_image', 'agentic-harness-evaluator:1'),
            benchmark.command('evaluate', task, '/workspace', '/benchmark'), 'evaluate', str(benchmark.root))
        return benchmark.evaluate(output, code, task)

    def __exit__(self, *args):
        for name in self.containers:
            self._docker(['rm', '-f', name], f'cleanup-{name[-8:]}', 30, check=False)
        if not self.job['config'].get('keep_workspaces'):
            self._docker(['volume', 'rm', '-f', self.volume], 'volume-cleanup', 30, check=False)


def doctor():
    try:
        result = subprocess.run(['docker', 'info', '--format', '{{json .ServerVersion}}'],
                                capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        raise ValueError('Docker is not installed. Install and start Docker Desktop before running workers.') from None
    except subprocess.TimeoutExpired:
        raise ValueError('Docker did not respond within 15 seconds') from None
    if result.returncode:
        raise ValueError('Docker daemon is unavailable: ' + result.stderr.strip())
    return result.stdout.strip()
