DOCKER ?= docker

.PHONY: install images test smoke
install:
	uv sync --frozen
images:
	$(DOCKER) build -t agentic-harness-evaluator:1 -f docker/Dockerfile.evaluator docker
	$(DOCKER) build -t agentic-harness-mini-python:1 -f docker/Dockerfile.mini-python docker
	$(DOCKER) build -t agentic-harness-mini-alpine:1 -f docker/Dockerfile.mini-alpine docker
test:
	uv run pytest -m 'not docker'
smoke:
	$(DOCKER) build -t agentic-harness-evaluator:1 -f docker/Dockerfile.evaluator docker
	$(DOCKER) build -t agentic-harness-fixture:1 -f docker/Dockerfile.fixture .
	HARNESS_DOCKER_TEST=1 uv run pytest -m docker -v

.PHONY: bash-example py-example
bash-example:
	bash examples/run_bash_example.sh
py-example:
	bash examples/run_python_example.sh

.PHONY: mini-smoke
mini-smoke: images
	HARNESS_MINI_TEST=1 uv run pytest -m docker -k real_mini -v
