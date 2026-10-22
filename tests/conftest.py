"""Shared by every test: never write in a real H-MEM memory, whatever the shell or .env says."""
import pytest


@pytest.fixture(autouse=True)
def no_real_memory(monkeypatch):
    monkeypatch.setenv("HMEM_URL", "")  # tests that need H-MEM start their own server (tests/test_hmem.py)
