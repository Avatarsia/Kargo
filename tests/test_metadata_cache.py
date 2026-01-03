"""Unit tests for metadata cache"""

import pytest
import tempfile
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
