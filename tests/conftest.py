"""Isolated local tests: no provider calls, live service writes or live ledger."""
import os
import tempfile
import threading
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_test_directory = tempfile.TemporaryDirectory(prefix="aeion-tests-")
os.environ["AEION_DB_PATH"] = str(Path(_test_directory.name) / "bootstrap.db")
os.environ["AEION_AUDIT_PATH"] = str(Path(_test_directory.name) / "events.jsonl")
os.environ.setdefault("AEION_TEST_BACKEND", "macos" if sys.platform == "darwin" else "docker")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from app import main, observability, real_engine
    from app.evidence import EvidenceWriter
    from app.memory import MemoryStore
    from app.sandbox import SandboxManager
    from app.store import Store

    ledger = Store(tmp_path / "ledger.db")
    memory = MemoryStore(tmp_path / "memory.db")
    workspace = SandboxManager(tmp_path / "runs")
    graph = SimpleNamespace(
        stats=lambda: {"available": False, "provider": "graphify", "mode": "not-generated", "files": 0, "nodes": 0, "edges": 0, "symbols": 0, "digest": "—"},
        view=lambda: {"mode": "not-generated", "files": 0, "nodes": [], "links": [], "edges": 0, "symbols": 0, "digest": "—", "nodes_total": 0},
    )
    workboard = SimpleNamespace(
        health=lambda force=False: {"available": False, "url": "http://127.0.0.1:3100", "error": "Paperclip offline in test"},
        list_issues=lambda **kwargs: {"available": False, "issues": [], "error": "Paperclip offline in test"},
        create_issue=lambda record: {"created": False, "error": "Paperclip offline in test"},
    )
    for name, value in [("store", ledger), ("memory_store", memory), ("sandbox", workspace), ("graph_projection", graph), ("paperclip", workboard), ("evidence_writer", EvidenceWriter(tmp_path / "packets")), ("emit", lambda event: None)]:
        monkeypatch.setattr(real_engine, name, value)
    monkeypatch.setattr(observability, "store", ledger)
    monkeypatch.setattr(main, "store", ledger)
    engine = real_engine.RealEngine()
    # Legacy regression cases keep their admitted pinned-runtime behavior.
    # Dedicated routing tests enable their own explicit, isolated policy.
    monkeypatch.setattr(real_engine, "load_routing_policy", lambda: None)
    monkeypatch.setattr(engine, "agent_probe", lambda force=False: {"ready": True, "model": "opencode/deepseek-test", "runtime": "opencode-cli", "auth": "test"})
    monkeypatch.setattr(engine, "start", lambda: None)
    monkeypatch.setattr(engine, "stop", lambda: None)
    monkeypatch.setattr(main, "engine", engine)
    # Keep mission execution synchronous and explicit in each test.
    monkeypatch.setattr(real_engine, "threading", SimpleNamespace(RLock=threading.RLock, Event=threading.Event, Thread=lambda **kwargs: SimpleNamespace(start=lambda: None)))
    yield SimpleNamespace(engine=engine, store=ledger, sandbox=workspace, module=real_engine, main=main, memory=memory, monkeypatch=monkeypatch)
    memory.close()
    ledger._conn.close()
