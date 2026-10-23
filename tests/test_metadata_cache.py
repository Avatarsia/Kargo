"""Unit tests for metadata cache"""

import pytest
import tempfile
import sqlite3
import warnings
from datetime import datetime
from pathlib import Path
from scripts.utils.metadata_cache import MetadataCache


@pytest.fixture
def cache():
    """Create temporary cache for testing"""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = Path(f.name)
    
    cache = MetadataCache(db_path)
    yield cache
    
    # Cleanup
    db_path.unlink(missing_ok=True)


class TestMetadataCache:
    """Test metadata cache operations"""
    
    def test_init_database(self, cache):
        """Test database initialization"""
        stats = cache.get_statistics()
        assert stats['total_files'] == 0
        assert stats['queue_size'] == 0
    
    def test_update_file(self, cache):
        """Test file metadata update"""
        cache.update_file("test.gcode", 12345, "/nas/test.gcode")
        
        metadata = cache.get_file_metadata("test.gcode")
        assert metadata is not None
        assert metadata['rel_path'] == "test.gcode"
        assert metadata['size'] == 12345
        assert metadata['nas_path'] == "/nas/test.gcode"
    
    def test_get_file_size(self, cache):
        """Test get file size"""
        cache.update_file("test.gcode", 54321, "/nas/test.gcode")
        
        size = cache.get_file_size("test.gcode")
        assert size == 54321
        
        # Non-existent file
        size = cache.get_file_size("missing.gcode")
        assert size is None
    
    def test_record_access(self, cache):
        """Test access recording"""
        cache.update_file("test.gcode", 1000, "/nas/test.gcode")
        
        # Record multiple accesses
        cache.record_access("test.gcode")
        cache.record_access("test.gcode")
        cache.record_access("test.gcode")
        
        metadata = cache.get_file_metadata("test.gcode")
        assert metadata['print_count'] == 3
        assert metadata['last_accessed'] is not None
    
    def test_delete_file(self, cache):
        """Test file deletion"""
        cache.update_file("test.gcode", 1000, "/nas/test.gcode")
        assert cache.get_file_metadata("test.gcode") is not None
        
        cache.delete_file("test.gcode")
        assert cache.get_file_metadata("test.gcode") is None
    
    def test_queue_offload(self, cache):
        """Test offload queue"""
        cache.queue_offload("test1.gcode", "complete")
        cache.queue_offload("test2.gcode", "cancelled")
        
        pending = cache.get_pending_offloads()
        assert len(pending) == 2
        assert pending[0]['rel_path'] == "test1.gcode"
        assert pending[0]['end_state'] == "complete"
    
    def test_remove_from_queue(self, cache):
        """Test queue removal"""
        cache.queue_offload("test.gcode", "complete")
        pending = cache.get_pending_offloads()
        assert len(pending) == 1
        
        queue_id = pending[0]['id']
        cache.remove_from_queue(queue_id)
        
        pending = cache.get_pending_offloads()
        assert len(pending) == 0
    
    def test_increment_retry(self, cache):
        """Test retry counter"""
        cache.queue_offload("test.gcode", "complete")
        pending = cache.get_pending_offloads()
        queue_id = pending[0]['id']
        
        assert pending[0]['retry_count'] == 0
        
        cache.increment_retry(queue_id)
        cache.increment_retry(queue_id)
        
        pending = cache.get_pending_offloads()
        assert pending[0]['retry_count'] == 2
    
    def test_statistics(self, cache):
        """Test statistics"""
        cache.update_file("file1.gcode", 1000, "/nas/file1.gcode")
        cache.update_file("file2.gcode", 2000, "/nas/file2.gcode")
        cache.record_access("file1.gcode")
        cache.record_access("file1.gcode")
        cache.record_access("file2.gcode")
        cache.queue_offload("file3.gcode", "complete")
        
        stats = cache.get_statistics()
        assert stats['total_files'] == 2
        assert stats['total_size'] == 3000
        assert stats['total_prints'] == 3
        assert stats['queue_size'] == 1
    
    def test_config_storage(self, cache):
        """Test config storage"""
        cache.set_config("key1", "value1")
        cache.set_config("key2", "value2")
        
        assert cache.get_config("key1") == "value1"
        assert cache.get_config("key2") == "value2"
        assert cache.get_config("missing", "default") == "default"

    def test_get_files_metadata_uses_one_connection_and_omits_missing(self, cache, monkeypatch):
        cache.update_file("first.gcode", 123, "/nas/first.gcode")
        cache.update_file("second.gcode", 456, "/nas/second.gcode", "backup")
        connections = []
        connect = sqlite3.connect

        def counted_connect(*args, **kwargs):
            conn = connect(*args, **kwargs)
            connections.append(conn)
            return conn

        monkeypatch.setattr(sqlite3, "connect", counted_connect)
        metadata = cache.get_files_metadata([
            "second.gcode", "missing.gcode", "first.gcode", "second.gcode",
            "quote' OR 1=1 --.gcode",
        ])

        assert len(connections) == 1
        assert set(metadata) == {"first.gcode", "second.gcode"}
        assert metadata["first.gcode"]["size"] == 123
        assert metadata["second.gcode"]["nas_target"] == "backup"
        assert metadata["second.gcode"]["nas_path"] == "/nas/second.gcode"

    def test_get_files_metadata_empty_opens_no_connection(self, cache, monkeypatch):
        def unexpected_connect(*args, **kwargs):
            pytest.fail("Empty metadata batch must not open a connection")

        monkeypatch.setattr(sqlite3, "connect", unexpected_connect)
        assert cache.get_files_metadata([]) == {}

    def test_get_files_metadata_chunks_large_batch_in_one_connection(self, cache, monkeypatch):
        paths = ["part-{}.gcode".format(i) for i in range(1801)]
        with sqlite3.connect(str(cache.db_path)) as conn:
            conn.executemany('''
                INSERT INTO files (rel_path, size, nas_path, offloaded_at)
                VALUES (?, ?, ?, ?)
            ''', [(path, i, "/nas/" + path, "2026-01-01 00:00:00")
                  for i, path in enumerate(paths)])
        connections = []
        connect = sqlite3.connect

        def limited_connect(*args, **kwargs):
            conn = connect(*args, **kwargs)
            conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 900)
            connections.append(conn)
            return conn

        monkeypatch.setattr(sqlite3, "connect", limited_connect)
        metadata = cache.get_files_metadata(paths + ["missing.gcode", paths[0]])

        assert len(connections) == 1
        assert set(metadata) == set(paths)
        assert metadata["part-0.gcode"]["size"] == 0
        assert metadata["part-1800.gcode"]["size"] == 1800

    def test_update_file_preserves_persistent_access_statistics(self, cache):
        cache.update_file("part.gcode", 100, "/nas/old.gcode")
        cache.record_access("part.gcode")
        cache.record_access("part.gcode")
        before = cache.get_file_metadata("part.gcode")

        cache.update_file("part.gcode", 250, "/backup/new.gcode", "backup")
        reopened = MetadataCache(cache.db_path)
        metadata = reopened.get_file_metadata("part.gcode")

        assert metadata["print_count"] == 2
        assert metadata["last_accessed"] == before["last_accessed"]
        assert metadata["size"] == 250
        assert metadata["nas_path"] == "/backup/new.gcode"
        assert metadata["nas_target"] == "backup"
        assert reopened.get_statistics()["total_prints"] == 2
        assert reopened.get_statistics()["total_size"] == 250

    def test_requeue_after_failure_preserves_id_and_retry_history(self, cache):
        cache.queue_offload("part.gcode", "error")
        initial = cache.get_pending_offloads()[0]
        cache.increment_retry(initial["id"])
        failed = cache.get_pending_offloads()[0]

        cache.queue_offload("part.gcode", "complete")
        cache.queue_offload("part.gcode", "cancelled")
        pending = MetadataCache(cache.db_path).get_pending_offloads()

        assert len(pending) == 1
        assert pending[0]["id"] == initial["id"]
        assert pending[0]["end_state"] == "cancelled"
        assert pending[0]["queued_at"] == initial["queued_at"]
        assert pending[0]["retry_count"] == 1
        assert pending[0]["last_retry"] == failed["last_retry"]
        assert pending[0]["last_retry"] is not None

    def test_queue_cursor_drains_batches_after_removals_without_skipping(self, cache):
        for i in range(15):
            cache.queue_offload("part-{}.gcode".format(i), "complete")
        first = cache.get_pending_offloads()
        assert len(first) == 10
        for item in first:
            cache.remove_from_queue(item["id"])

        remaining = cache.get_pending_offloads(10, first[-1]["id"])
        assert [item["rel_path"] for item in remaining] == [
            "part-10.gcode", "part-11.gcode", "part-12.gcode",
            "part-13.gcode", "part-14.gcode",
        ]
        for item in remaining:
            cache.remove_from_queue(item["id"])
        assert cache.get_pending_offloads(after_id=remaining[-1]["id"]) == []
        assert cache.get_statistics()["queue_size"] == 0

    def test_queue_cursor_skips_failed_older_item_and_orders_by_id(self, cache):
        cache.queue_offload("failed.gcode", "error")
        cache.queue_offload("next.gcode", "complete")
        initial = cache.get_pending_offloads()
        cache.increment_retry(initial[0]["id"])
        # Backdated timestamps must not move later IDs behind the cursor.
        with sqlite3.connect(str(cache.db_path)) as conn:
            conn.execute("UPDATE offload_queue SET queued_at = ? WHERE rel_path = ?",
                         ("2000-01-01 00:00:00", "next.gcode"))

        first = cache.get_pending_offloads(limit=1)
        assert first[0]["rel_path"] == "failed.gcode"
        next_batch = cache.get_pending_offloads(limit=1, after_id=first[0]["id"])
        assert [item["rel_path"] for item in next_batch] == ["next.gcode"]
        assert cache.get_pending_offloads()[0]["retry_count"] == 1

    def test_existing_queue_duplicates_migrate_preserving_oldest_row(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute('''
                CREATE TABLE offload_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rel_path TEXT NOT NULL,
                    end_state TEXT NOT NULL,
                    queued_at TIMESTAMP NOT NULL,
                    retry_count INTEGER DEFAULT 0,
                    last_retry TIMESTAMP
                )
            ''')
            conn.executemany("INSERT INTO offload_queue VALUES (?, ?, ?, ?, ?, ?)", [
                (3, "duplicate.gcode", "error", "2026-01-01 00:00:00", 4,
                 "2026-01-02 00:00:00"),
                (7, "other.gcode", "complete", "2026-01-03 00:00:00", 2,
                 "2026-01-04 00:00:00"),
                (9, "duplicate.gcode", "complete", "2026-01-05 00:00:00", 0, None),
            ])

        migrated = MetadataCache(db_path)
        pending = migrated.get_pending_offloads()
        assert pending == [
            {"id": 3, "revision": 0, "rel_path": "duplicate.gcode", "end_state": "error",
             "queued_at": "2026-01-01 00:00:00", "retry_count": 4,
             "last_retry": "2026-01-02 00:00:00"},
            {"id": 7, "revision": 0, "rel_path": "other.gcode", "end_state": "complete",
             "queued_at": "2026-01-03 00:00:00", "retry_count": 2,
             "last_retry": "2026-01-04 00:00:00"},
        ]
        assert MetadataCache(db_path).get_pending_offloads() == pending
        with sqlite3.connect(str(db_path)) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute('''
                    INSERT INTO offload_queue (rel_path, end_state, queued_at)
                    VALUES (?, ?, ?)
                ''', ("duplicate.gcode", "complete", "2026-01-06 00:00:00"))
        migrated.queue_offload("duplicate.gcode", "cancelled")
        assert migrated.get_pending_offloads()[0] == dict(pending[0], end_state="cancelled", revision=1)
        migrated.queue_offload("new.gcode", "complete")
        assert migrated.get_pending_offloads()[-1]["id"] > 9

    @pytest.mark.parametrize("operation", [
        "update_file", "record_access", "queue_offload", "increment_retry", "set_config",
    ])
    def test_datetime_writes_do_not_use_deprecated_sqlite_adapter(self, cache, operation):
        cache.update_file("part.gcode", 100, "/nas/part.gcode")
        cache.queue_offload("part.gcode", "error")
        queue_id = cache.get_pending_offloads()[0]["id"]
        actions = {
            "update_file": lambda: cache.update_file("part.gcode", 200, "/nas/new.gcode"),
            "record_access": lambda: cache.record_access("part.gcode"),
            "queue_offload": lambda: cache.queue_offload("new.gcode", "complete"),
            "increment_retry": lambda: cache.increment_retry(queue_id),
            "set_config": lambda: cache.set_config("target", "backup"),
        }
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            actions[operation]()
        metadata = cache.get_file_metadata("part.gcode")
        assert datetime.fromisoformat(metadata["offloaded_at"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
