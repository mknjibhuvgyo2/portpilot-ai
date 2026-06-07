"""Pytest bootstrap: ensure the backend dir is importable as the package root.

Running ``pytest`` (the console script) does not add the current directory to
``sys.path``, so ``import app`` would fail in CI. Placing this conftest at the
backend root makes pytest add this directory to ``sys.path``, so tests can
``import app`` regardless of how pytest is invoked.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
