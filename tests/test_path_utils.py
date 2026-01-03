"""Unit tests for path_utils module"""

import pytest
from scripts.utils.path_utils import sanitize_path, InvalidPathError


class TestSanitizePath:
    """Test path sanitization"""
    
    def test_valid_simple_path(self):
        """Test simple valid path"""
        result = sanitize_path("test.gcode")
        assert result == "test.gcode"
    
    def test_valid_nested_path(self):
        """Test nested valid path"""
        result = sanitize_path("subfolder/test.gcode")
        assert result == "subfolder/test.gcode"
    
    def test_valid_compressed(self):
        """Test compressed G-code"""
        result = sanitize_path("test.gcode.gz")
        assert result == "test.gcode.gz"
    
    def test_reject_absolute_path(self):
        """Test absolute paths are rejected"""
        with pytest.raises(InvalidPathError, match="Absolute paths"):
            sanitize_path("/etc/passwd")
    
    def test_reject_parent_traversal(self):
        """Test parent directory traversal is rejected"""
        with pytest.raises(InvalidPathError, match="Parent directory"):
            sanitize_path("../../../etc/passwd")
        
        with pytest.raises(InvalidPathError, match="Parent directory"):
            sanitize_path("folder/../../../etc/passwd")
    
    def test_reject_null_bytes(self):
        """Test null bytes are rejected"""
        with pytest.raises(InvalidPathError, match="Null bytes"):
            sanitize_path("test\x00.gcode")
    
    def test_reject_invalid_extension(self):
        """Test invalid file extensions are rejected"""
        with pytest.raises(InvalidPathError, match="Invalid file extension"):
            sanitize_path("test.txt")
        
        with pytest.raises(InvalidPathError, match="Invalid file extension"):
            sanitize_path("test.sh")
    
    def test_reject_empty_path(self):
        """Test empty paths are rejected"""
        with pytest.raises(InvalidPathError, match="cannot be empty"):
            sanitize_path("")
        
        with pytest.raises(InvalidPathError, match="cannot be empty"):
            sanitize_path("   ")
    
    def test_reject_hidden_directories(self):
        """Test hidden directories are rejected"""
        with pytest.raises(InvalidPathError, match="Hidden directories"):
            sanitize_path(".hidden/test.gcode")
    
    def test_allow_gcode_variants(self):
        """Test various G-code extensions"""
        assert sanitize_path("test.gcode") == "test.gcode"
        assert sanitize_path("test.gcode.gz") == "test.gcode.gz"
        assert sanitize_path("test.ufp") == "test.ufp"
        assert sanitize_path("test.nc") == "test.nc"
        assert sanitize_path("test.gco") == "test.gco"
    
    def test_normalize_slashes(self):
        """Test path normalization"""
        result = sanitize_path("folder//subfolder/test.gcode")
        assert result == "folder/subfolder/test.gcode"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
