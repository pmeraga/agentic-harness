#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv run harness run --agent mini-swe-agent --benchmark python-tasks --task_ids py-001 --wait
