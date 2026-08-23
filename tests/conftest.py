"""Shared fixtures for the gateway test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
import token_manager


@pytest.fixture(autouse=True)
def _reset_token_manager_process_state():
    """Isolate module-level token state between tests.

    The refresh negative cache, shared refresh task, and loop-aware lock are
    process-global; without a reset a transient-failure marker recorded in one
    test would suppress refreshes in every later test.
    """
    token_manager._clear_refresh_failure()
    yield
    token_manager._clear_refresh_failure()
    token_manager._refresh_task = None


@pytest.fixture
def loopback_client():
    """Build a TestClient whose Host header passes the loopback boundary middleware.

    Tests monkeypatch auth/state on their own before entering the client context;
    this fixture only pins the base_url so the Host allowlist accepts requests.
    """

    def _build(**kwargs):
        return TestClient(main.app, base_url="http://127.0.0.1", **kwargs)

    return _build
