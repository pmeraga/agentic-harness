# Validation record

The following results were recorded during development on macOS / Apple Silicon using Python 3.11 and Docker Desktop. The source archive includes the corresponding result JSON files. Docker itself is not available in every environment where the source may be reviewed.

| Check | Recorded outcome |
| --- | --- |
| Unit + local integration (`make test`) | 25 originally; 26 after a CLI-alias regression test; **29 after prompt and limit regression tests (4 Docker deselected)** |
| Docker fixture lifecycle (`make smoke`) | 2 passed, covering 5 Bash and 3 Python tasks |
| Real mini-swe-agent CLI with simulated model calls (`make mini-smoke`) | 2 passed, covering both benchmarks |
| Runtime image builds | Evaluator, both mini-swe-agent images, both Responses agent images built |
| Live mini-swe-agent / GPT-5 Mini | 8/8 across both benchmarks |
| Live OpenAI Responses agent / GPT-5.4 Mini | 8/8 across both benchmarks |

The two Docker tests use the real setup and grading containers, with a deterministic fixture replacing the LLM. The real-mini Docker tests run the actual mini CLI and wrapper with simulated model responses; they do not make billed requests.

The `mini-swe-agent` live run reported 27 API calls and $0.05837265 in model cost. This is the agent's own estimate, not a provider billing receipt. Its task-level results and run IDs are recorded in [`../LIVE_RESULTS.json`](../LIVE_RESULTS.json). The independent Responses agent's run IDs and aggregate results are recorded in [`../RESPONSES_LIVE_RESULTS.json`](../RESPONSES_LIVE_RESULTS.json); its wrapper does not report token cost.

During the initial repository review on Python 3.13, **26 passed and 4 Docker-marked tests were deselected**. Following that review, the Responses agent system prompt was made task-neutral and its final-response behavior at the step limit was fixed. Three regression tests were added; the local suite was rerun on Python 3.13 with **29 passed and 4 Docker-marked tests deselected**. The live Responses 8/8 result was recorded **before** these changes and must be revalidated before being attributed to the updated implementation. Docker and live API runs were not repeated in the review environment.

To verify on another machine, run `make test`, `make smoke`, and `make mini-smoke` after building the images. Live API checks require valid credentials in the worker process. Nothing in these files proves broader benchmark performance beyond the supplied task set.

## Benchmark adapter extension (latest change)

The CLI and worker now resolve benchmarks through `load_benchmark()`, supporting built-in `command` and `jsonl` adapters and installed entry-point plugins. A two-task `jsonl-tasks` benchmark exercises the second adapter with a different dataset schema. Six new local adapter tests passed independently during preparation, including an end-to-end CLI → worker → grader test and a third-party entry-point discovery test. A fifth Docker-marked case was added for the JSONL benchmark but has not yet been run in the review environment. Historical 8/8 agent results concern only the original supplied benchmarks.

Run `make test`, `make smoke`, and `make mini-smoke` on a Docker-enabled machine before relying on the updated test counts.
