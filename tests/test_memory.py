from concurrent.futures import ThreadPoolExecutor


def seed(runtime, tmp_path):
    source = tmp_path / "source.md"
    source.write_text("Pricing contract: every started kilogram rounds up.")
    runtime.memory.seed_sources([(str(source), "contract", "test")])
    return source


def test_changed_or_deleted_source_is_not_recalled(runtime, tmp_path):
    source = seed(runtime, tmp_path)
    assert runtime.memory.recall("pricing")["hit"]
    source.write_text("The contract changed.")
    assert not runtime.memory.recall("pricing")["hit"]
    assert runtime.memory.stats()["stale"] == 1
    source.unlink()
    assert not runtime.memory.recall("pricing")["hit"]


def test_shared_memory_connection_handles_concurrent_snapshots_and_recalls(runtime, tmp_path):
    seed(runtime, tmp_path)
    def read(index):
        if index % 2:
            return runtime.memory.recall("pricing")["hit"]
        return runtime.memory.stats()["verified"] == 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(read, range(64)))
    assert runtime.memory.stats()["recall_queries"] == 32
