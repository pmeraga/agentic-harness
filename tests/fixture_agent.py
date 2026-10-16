"""Known solutions for integration tests only; this is not an LLM agent."""
import sys
from pathlib import Path


def solve(task_id, root):
    root = Path(root)
    if task_id == 'bash-001':
        (root / 'hello.txt').write_text('Hello World')
    elif task_id == 'bash-002':
        (root / 'test_dir').mkdir()
        for i in range(1, 4):
            (root / f'test_dir/file{i}.txt').touch()
    elif task_id == 'bash-003':
        (root / 'count.txt').write_text(str(len((root / 'input.txt').read_text().splitlines())))
    elif task_id == 'bash-004':
        (root / 'txt_files.txt').write_text('\n'.join(sorted(p.name for p in root.glob('*.txt'))))
    elif task_id == 'bash-005':
        (root / 'numbers.txt').write_text('\n'.join(map(str, range(1, 11))))
    else:
        solutions = {
            'py-001': 'def add():\n    return 4\n',
            'py-002': 'def fibonacci(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a\n',
            'py-003': 'def get_nth(lst, n):\n    return lst[n] if 0 <= n < len(lst) else None\n',
        }
        (root / 'solution.py').write_text(solutions[task_id])


if __name__ == '__main__':
    solve(sys.argv[1], Path.cwd())
