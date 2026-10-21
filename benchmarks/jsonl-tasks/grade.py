"""Compare the submitted file with the expected contents outside the agent container."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('task_id')
parser.add_argument('--workspace', required=True)
args = parser.parse_args()
records = [json.loads(line) for line in (Path(__file__).parent / 'tasks.jsonl').read_text().splitlines() if line.strip()]
record = next(r for r in records if r['id'] == args.task_id)
answer = Path(args.workspace) / 'answer.txt'
passed = answer.is_file() and answer.read_text() == record['expected']
print(json.dumps({'task_id': args.task_id, 'passed': passed, 'score': 1.0 if passed else 0.0}))
