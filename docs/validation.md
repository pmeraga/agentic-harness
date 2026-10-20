# Validation record

The following results were recorded during development on macOS / Apple Silicon using Python 3.11 and Docker Desktop. The source archive includes the corresponding result JSON files. Docker itself is not available in every environment where the source may be reviewed.

| Check | Recorded outcome |
| --- | --- |
| Unit + local integration (`make test`) | 25 originally; 26 after a CLI-alias regression test |
| Docker fixture lifecycle (`make smoke`) | 2 passed, covering 5 Bash and 3 Python tasks |
| Real mini-swe-agent CLI with simulated model calls (`make mini-smoke`) | 2 passed, covering both benchmarks |
| Runtime image builds | Evaluator, both mini-swe-agent images, both Responses agent images built |
| Live mini-swe-agent / GPT-5 Mini | 8/8 across both benchmarks |
| Live OpenAI Responses agent / GPT-5.4 Mini | 8/8 across both benchmarks |

The two Docker tests use the real setup and grading containers, with a deterministic fixture replacing the LLM. The real-mini Docker tests run the actual mini CLI and wrapper with simulated model responses; they do not make billed requests.

The `mini-swe-agent` live run reported 27 API calls and $0.05837265 in model cost. This is the agent's own estimate, not a provider billing receipt. Its task-level results and run IDs are recorded in [`../LIVE_RESULTS.json`](../LIVE_RESULTS.json). The independent Responses agent's run IDs and aggregate results are recorded in [`../RESPONSES_LIVE_RESULTS.json`](../RESPONSES_LIVE_RESULTS.json); its wrapper does not report token cost.

The local tests were also rerun during repository review on Python 3.13: **26 passed, 4 Docker-marked tests deselected**. Docker and live API runs were not repeated in that environment.

To verify on another machine, run `make test`, `make smoke`, and `make mini-smoke` after building the images. Live API checks require valid credentials in the worker process. Nothing in these files proves broader benchmark performance beyond the supplied task set.
