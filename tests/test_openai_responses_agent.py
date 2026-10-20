import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / 'docker' / 'openai_responses_agent.py'
SPEC = importlib.util.spec_from_file_location('openai_responses_agent', SCRIPT)
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


class Call:
    type = 'function_call'
    name = 'run_shell'
    call_id = 'call_1'
    arguments = '{"command":"printf hello > answer.txt"}'


class Response:
    def __init__(self, identifier, output):
        self.id = identifier
        self.output = output


class Responses:
    def __init__(self):
        self.calls = []
        self.items = [Response('first', [Call()]), Response('second', [])]

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.items.pop(0)


class Client:
    def __init__(self):
        self.responses = Responses()


def test_tool_loop_writes_workspace_and_returns_output(tmp_path):
    client = Client()
    response = agent.run_agent('write a file', workspace=tmp_path, client=client, model='test-model')
    assert (tmp_path / 'answer.txt').read_text() == 'hello'
    assert response.id == 'second'
    assert client.responses.calls[0]['model'] == 'test-model'
    follow_up = client.responses.calls[1]
    assert follow_up['previous_response_id'] == 'first'
    assert '"exit_code": 0' in follow_up['input'][0]['output']


def test_invalid_tool_arguments_are_returned_to_the_model(tmp_path):
    broken = type('BrokenCall', (), {'name': 'run_shell', 'call_id': 'call_1', 'arguments': '{}'})()
    output = agent.tool_output(broken, tmp_path)
    assert 'Invalid run_shell call' in output['output']
