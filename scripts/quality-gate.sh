#!/usr/bin/env sh
set -eu

uv run ruff check .
uv run ruff format --check .
uv run mypy

# Bound cumulative native-library memory in long-lived pytest processes. Every test file
# is still collected, but each process receives at most eight files before releasing RAM.
find tests -type f -name 'test_*.py' -print | sort | (
  set --
  count=0
  while IFS= read -r test_file; do
    set -- "$@" "$test_file"
    count=$((count + 1))
    if [ "$count" -eq 8 ]; then
      uv run pytest "$@"
      set --
      count=0
    fi
  done
  if [ "$count" -gt 0 ]; then
    uv run pytest "$@"
  fi
)
uv run python -m gisnet.cli status
