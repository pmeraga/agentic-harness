"""mini v1 catches exceptions; translate trajectory status into an exit code."""
import json
import subprocess
import sys
from pathlib import Path


def main(prompt, trajectory=Path('/workspace/trajectory.json'), config='/opt/harness/mini.yaml'):
    trajectory.unlink(missing_ok=True)
    code = subprocess.call(['mini', '--exit-immediately', '-y', '-c', config,
                            '-t', prompt, '-o', str(trajectory)])
    if code:
        raise SystemExit(code)
    if not trajectory.exists():
        raise SystemExit('mini did not produce a trajectory')
    status = json.loads(trajectory.read_text()).get('info', {}).get('exit_status')
    if status != 'Submitted':
        raise SystemExit(f'mini stopped without submitting: {status}')


if __name__ == '__main__':
    main(sys.argv[1])
