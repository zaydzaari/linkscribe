.PHONY: install test lint skills build check run worker

install:
	python -m pip install --upgrade pip
	python -m pip install -e ".[dev]"

test:
	python -m pytest --cov=linkscribe --cov=clients --cov-report=term-missing

lint:
	ruff check .

skills:
	python scripts/validate-skills.py

build:
	python -m build

check: lint skills test build

run:
	uvicorn linkscribe.api:app --reload --host 127.0.0.1 --port 8080

worker:
	linkscribe-worker
