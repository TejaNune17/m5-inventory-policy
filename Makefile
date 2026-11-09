# One command rebuilds everything from raw data: make all
# Windows (Git Bash / ezwinports make) and Unix both supported.

ifeq ($(OS),Windows_NT)
PY ?= .venv/Scripts/python
else
PY ?= .venv/bin/python
endif

SRC = src

.PHONY: all setup data dq segment forecast simulate report test clean

all: test data dq segment forecast simulate report

setup:
	python -m venv .venv
	$(PY) -m pip install -r requirements.txt

data:
	$(PY) $(SRC)/ingest.py

dq:
	$(PY) $(SRC)/dq.py

segment:
	$(PY) $(SRC)/segment.py

forecast:
	$(PY) $(SRC)/forecast.py

simulate:
	$(PY) $(SRC)/simulate.py

report:
	$(PY) $(SRC)/report.py

test:
	$(PY) -m pytest -q

clean:
	rm -f data/warehouse.duckdb data/warehouse.duckdb.wal
	rm -rf data/processed/*
