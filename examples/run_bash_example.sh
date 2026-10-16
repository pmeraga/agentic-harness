#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv run harness run --agent mini-swe-agent --benchmark bash-operations --task_ids bash-001 --wait
