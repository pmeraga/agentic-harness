"""Initialize a blank task workspace for the JSONL benchmark."""
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('task_id')
parser.add_argument('--workspace', required=True)
args = parser.parse_args()
Path(args.workspace).mkdir(parents=True, exist_ok=True)
