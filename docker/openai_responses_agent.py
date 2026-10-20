"""A small coding agent backed by the OpenAI Responses API.

The harness owns the isolation boundary.  This process receives a task prompt,
asks a model to use one narrow shell tool, and executes that tool only inside
the task's mounted /workspace volume.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

MAX_STEPS = 12
MAX_OUTPUT = 12_000
SHELL_TIMEOUT_SECONDS = 30
SHELL_TOOL = {
    'type': 'function',
    'name': 'run_shell',
    'description': 'Run one shell command in the task workspace. Use it to inspect files, write the solution, and verify it. The command runs with /workspace as its current directory.',
    'parameters': {
        'type': 'object',
        'properties': {'command': {'type': 'string', 'description': 'A POSIX shell command to run.'}},
        'required': ['command'],
        'additionalProperties': False,
    },
    'strict': True,
}
INSTRUCTIONS = """You are a coding agent working on a task in /workspace.
Inspect the workspace before acting. Keep file changes under /workspace. Use
run_shell to make the required changes and run relevant checks. Follow the task
requirements and handle relevant edge cases; do not assume details that the
task does not specify. Keep changes focused on the task, and do not claim work
was completed unless you performed it. When finished, respond briefly without
a tool call."""


def shell_result(command: str, workspace: Path) -> dict:
    """Execute a bounded shell command and return safe, compact tool output."""
    try:
        completed = subprocess.run(
            ['/bin/sh', '-lc', command], cwd=workspace, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=SHELL_TIMEOUT_SECONDS, check=False,
        )
        output = completed.stdout
        if len(output) > MAX_OUTPUT:
            output = output[-MAX_OUTPUT:]
        return {'exit_code': completed.returncode, 'output': output}
    except subprocess.TimeoutExpired:
        return {'exit_code': 124, 'output': f'Command timed out after {SHELL_TIMEOUT_SECONDS} seconds'}


def tool_output(call, workspace: Path) -> dict:
    if call.name != 'run_shell':
        result = {'exit_code': 2, 'output': f'Unknown tool: {call.name}'}
    else:
        try:
            arguments = json.loads(call.arguments)
            command = arguments['command']
            if not isinstance(command, str) or not command.strip():
                raise ValueError('command must be a nonempty string')
            result = shell_result(command, workspace)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            result = {'exit_code': 2, 'output': f'Invalid run_shell call: {error}'}
    return {'type': 'function_call_output', 'call_id': call.call_id, 'output': json.dumps(result)}


def run_agent(prompt: str, workspace=Path('/workspace'), client=None, model=None):
    """Run the tool loop. The API retains response state through response IDs."""
    if not os.environ.get('OPENAI_API_KEY') and client is None:
        raise RuntimeError('OPENAI_API_KEY is required')
    if client is None:
        # Keep this import here so the pure shell/tool-loop tests do not need
        # the provider SDK installed on the host.
        from openai import OpenAI
        client = OpenAI()
    model = model or os.environ.get('OPENAI_RESPONSES_MODEL', 'gpt-5.4-mini')
    response = client.responses.create(
        model=model, input=prompt, instructions=INSTRUCTIONS, tools=[SHELL_TOOL],
        parallel_tool_calls=False,
    )
    for _ in range(MAX_STEPS):
        calls = [item for item in response.output if item.type == 'function_call']
        if not calls:
            return response
        outputs = [tool_output(call, workspace) for call in calls]
        response = client.responses.create(
            model=model, previous_response_id=response.id, input=outputs,
            instructions=INSTRUCTIONS, tools=[SHELL_TOOL], parallel_tool_calls=False,
        )
    # The last permitted tool call can return a final answer. Check that
    # response before treating the limit as exceeded.
    if not any(item.type == 'function_call' for item in response.output):
        return response
    raise RuntimeError(f'Agent exceeded {MAX_STEPS} tool rounds')


def main():
    if len(sys.argv) != 2:
        raise SystemExit('usage: openai_responses_agent.py <task prompt>')
    run_agent(sys.argv[1])


if __name__ == '__main__':
    main()
