"""File operations for FETCH and OFFLOAD"""

import os
import shutil
import fcntl
import json
import logging
import hashlib
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any
from contextlib import contextmanager
from datetime import datetime

from .path_utils import sanitize_path
from .metadata_cache import MetadataCache


logger = logging.getLogger(__name__)


class FileOperationError(Exception):
    """Raised when file operation fails"""
    pass


@contextmanager
def file_lock(lock_path: Path, timeout: int = 30):
    """
    Advisory file locking using fcntl.
    
    Args:
        lock_path: Path to lock file
        timeout: Timeout in seconds (not implemented, advisory only)
    
    Raises:
        FileOperationError: If lock cannot be acquired
    
    Yields:
        None
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(str(lock_path), 'w')
    
    try:
        # Non-blocking exclusive lock
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        logger.debug(f"Acquired lock: {lock_path}")
        yield
    except IOError as e:
       raise FileOperationError(f"Could not acquire lock (operation already in progress): {lock_path}") from e
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
        logger.debug(f"Released lock: {lock_path}")


def safe_copy(src: Path, dst: Path, verify: bool = True, use_rsync: bool = None) -> None:
    """
    Atomic copy with optional verification.
    
    Args:
        src: Source file path
        dst: Destination file path
        verify: If True, verify size after copy
        use_rsync: If True use rsync, if None auto-detect based on size
    
    Raises:
        FileOperationError: If copy or verification fails
    """
    src = Path(src)
    dst = Path(dst)
    
    # Ensure destination directory exists
    dst.parent.mkdir(parents=True, exist_ok=True)
    
    # Auto-detect rsync usage for large files
    if use_rsync is None:
        size = src.stat().st_size
        use_rsync = size > 100 * 1024 * 1024 and shutil.which('rsync') is not None
    
    # Temporary destination
    dst_tmp = dst.with_suffix(dst.suffix + '.tmp')
    
    try:
        if use_rsync:
            logger.info(f"Copying with rsync: {src} -> {dst}")
            result = subprocess.run(
                ['rsync', '-ah', '--no-perms', '--no-owner', '--no-group', str(src), str(dst_tmp)],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                raise FileOperationError(f"rsync failed: {result.stderr}")
        else:
            logger.debug(f"Copying: {src} -> {dst}")
            shutil.copy2(src, dst_tmp)
        
        # Verify size if requested
        if verify:
            src_size = src.stat().st_size
            dst_size = dst_tmp.stat().st_size
            if src_size != dst_size:
                dst_tmp.unlink()
                raise FileOperationError(
                    f"Size mismatch: src={src_size}, dst={dst_size}"
                )
        
        # Atomic rename
        dst_tmp.rename(dst)
        logger.info(f"Successfully copied: {src} -> {dst}")
        
    except Exception as e:
        # Cleanup temp file on error
        if dst_tmp.exists():
            dst_tmp.unlink()
        raise FileOperationError(f"Copy failed: {e}") from e


def create_stub_file(stub_path: Path, metadata: Dict[str, Any]) -> None:
    """
    Create stub file with metadata.
    
    Args:
        stub_path: Path to stub file
        metadata: Dictionary with file metadata
    """
    stub_data = {
        'type': 'klipper_nas_offload_placeholder',
        'created_at': datetime.now().isoformat(),
        **metadata
    }
    
    with open(stub_path, 'w') as f:
        json.dump(stub_data, f, indent=2)
    
    logger.debug(f"Created stub file: {stub_path}")


def read_stub_file(stub_path: Path) -> Optional[Dict[str, Any]]:
    """
    Read stub file metadata.
    
    Args:
        stub_path: Path to stub file
    
    Returns:
        Metadata dict or None if invalid
    """
    try:
        with open(stub_path, 'r') as f:
            data = json.load(f)
        
        if data.get('type') == 'klipper_nas_offload_placeholder':
            return data
        return None
    except Exception as e:
        logger.warning(f"Failed to read stub file {stub_path}: {e}")
        return None


def _cleanup_old_versions(base_path: Path, keep_versions: int) -> None:
    """
    Remove old versions of a file, keeping only the newest N versions.
    
    Args:
        base_path: Base path without timestamp (e.g., /nas/test.gcode)
        keep_versions: Number of versions to keep
    """
    try:
        # Pattern: test_*.gcode or test_*.gcode.gz
        stem = base_path.stem
        suffixes = ''.join(base_path.suffixes)
        pattern = f"{stem}_*{suffixes}"
        
        # Find all versions
        versions = sorted(
            base_path.parent.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True  # Newest first
        )
        
        # Remove old versions
        for old_file in versions[keep_versions:]:
            logger.info(f"Removing old version: {old_file.name}")
            old_file.unlink()
            
    except Exception as e:
        logger.warning(f"Failed to cleanup old versions: {e}")


def is_nas_available(mount_point: Path) -> bool:
    """
    Check if NAS mount point is available.
    
    Args:
        mount_point: Path to NAS mount point
    
    Returns:
        True if mounted and writable
    """
    if not mount_point.exists():
        return False
    
    # Check if actually mounted
    if not os.path.ismount(str(mount_point)):
        return False
    
    # Check write permission with test file
    try:
        test_file = mount_point / '.nas_offload_test'
        test_file.write_text(f'test-{datetime.now().timestamp()}')
        test_file.unlink()
        return True
    except Exception as e:
        logger.warning(f"NAS write test failed: {e}")
        return False


def fetch(rel_path: str, local_dir: Path, nas_dir: Path, 
          cache: MetadataCache, lock_dir: Path) -> bool:
    """
    FETCH operation: Ensure file exists locally as regular file.
    
    Args:
        rel_path: Relative path to file
        local_dir: Local gcodes directory
        nas_dir: NAS gcodes directory
        cache: Metadata cache instance
        lock_dir: Directory for lock files
    
    Returns:
        True if file ready to print, False on error
    
    Raises:
        FileOperationError: On critical errors
    """
    # Sanitize path
    rel_path = sanitize_path(rel_path)
    
    local_path = local_dir / rel_path
    nas_path = nas_dir / rel_path
    lock_path = lock_dir / f"{hashlib.md5(rel_path.encode()).hexdigest()}.lock"
    
    logger.info(f"FETCH: {rel_path}")
    
    with file_lock(lock_path):
        # Case 1: Already a regular file
        if local_path.is_file() and not local_path.is_symlink():
            logger.debug(f"File already local: {rel_path}")
            cache.record_access(rel_path)
            return True
        
        # Case 2: Symlink to NAS
        if local_path.is_symlink():
            target = Path(os.readlink(local_path))
            
            # Check if target exists
            if not target.exists():
                logger.error(f"Symlink target missing: {target}")
                return False
            
            logger.info(f"Fetching from symlink target: {target}")
            
            # Copy from NAS to local
            try:
                # Use string concat to preserve full filename
                temp_path = Path(str(local_path) + '.fetching')
                safe_copy(target, temp_path, verify=True)
                
                # Remove symlink and rename fetched file
                local_path.unlink()
                temp_path.rename(local_path)
                
                # Remove stub file if exists
                stub_path = Path(str(local_path) + '.offloaded')
                if stub_path.exists():
                    stub_path.unlink()
                
                cache.record_access(rel_path)
                logger.info(f"Successfully fetched: {rel_path}")
                return True
                
            except FileOperationError as e:
                logger.error(f"FETCH failed: {e}")
                return False
        
        # Case 3: Only stub file exists
        stub_path = Path(str(local_path) + '.offloaded')
        if stub_path.exists():
            stub_data = read_stub_file(stub_path)
            if stub_data:
                nas_path_from_stub = Path(stub_data.get('nas_path', nas_path))
                
                if not nas_path_from_stub.exists():
                    logger.error(f"NAS file missing: {nas_path_from_stub}")
                    return False
                
                logger.info(f"Fetching from stub: {nas_path_from_stub}")
                
                try:
                    safe_copy(nas_path_from_stub, local_path, verify=True)
                    stub_path.unlink()
                    cache.record_access(rel_path)
                    return True
                except FileOperationError as e:
                    logger.error(f"FETCH from stub failed: {e}")
                    return False
        
        # Case 4: Check if file exists on NAS (no local reference)
        if nas_path.exists():
            logger.info(f"File found on NAS: {nas_path}")
            try:
                safe_copy(nas_path, local_path, verify=True)
                cache.record_access(rel_path)
                return True
            except FileOperationError as e:
                logger.error(f"FETCH from NAS failed: {e}")
                return False
        
        # File doesn't exist anywhere
        logger.error(f"File not found: {rel_path}")
        return False


def offload(rel_path: str, local_dir: Path, nas_dir: Path,
            cache: MetadataCache, lock_dir: Path, end_state: str,
            mode: str = 'hybrid', enable_versioning: bool = True,
            keep_versions: int = 5) -> bool:
    """
    OFFLOAD operation: Archive file to NAS and create symlink + stub.
    
    Args:
        rel_path: Relative path to file
        local_dir: Local gcodes directory
        nas_dir: NAS gcodes directory
        cache: Metadata cache instance
        lock_dir: Directory for lock files
        end_state: Print end state (complete, cancelled, error)
        mode: Offload mode (hybrid, symlink, stub)
        enable_versioning: If True, append timestamp to prevent overwrites
        keep_versions: Number of versions to keep (0 = unlimited)
    
    Returns:
        True if offloaded successfully, False if kept local
    """
    # Sanitize path
    rel_path = sanitize_path(rel_path)
    
    local_path = local_dir / rel_path
    nas_path = nas_dir / rel_path
    lock_path = lock_dir / f"{hashlib.md5(rel_path.encode()).hexdigest()}.lock"
    
    logger.info(f"OFFLOAD: {rel_path} (state={end_state}, mode={mode})")
    
    with file_lock(lock_path):
        # Already offloaded?
        if local_path.is_symlink():
            logger.debug(f"Already offloaded: {rel_path}")
            return True
        
        # Must be regular file
        if not local_path.is_file():
            logger.warning(f"Not a regular file: {rel_path}")
            return False
        
        # Check NAS availability - check the mount point, not nas_dir itself
        # nas_dir is typically /mnt/nas/klipper_gcodes, we want to check /mnt/nas
        mount_to_check = nas_dir
        while mount_to_check != mount_to_check.parent:
            if os.path.ismount(str(mount_to_check)):
                break
            mount_to_check = mount_to_check.parent
        
        if not is_nas_available(mount_to_check):
            logger.warning(f"NAS unavailable, queuing for retry: {rel_path}")
            cache.queue_offload(rel_path, end_state)
            return False
        
        try:
            # Get file size before offload
            file_size = local_path.stat().st_size
            
            # Build NAS path with versioning
            if enable_versioning:
                # Add timestamp to filename to prevent overwrites
                # test.gcode -> test_20260103_220155.gcode
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                stem = nas_path.stem  # filename without extension
                suffixes = ''.join(nas_path.suffixes)  # all extensions (.gcode or .gcode.gz)
                versioned_name = f"{stem}_{timestamp}{suffixes}"
                nas_path_versioned = nas_path.parent / versioned_name
            else:
                nas_path_versioned = nas_path
            
            # Copy to NAS
            safe_copy(local_path, nas_path_versioned, verify=True)
            
            # Update metadata cache with actual NAS path
            cache.update_file(rel_path, file_size, str(nas_path_versioned))
            
            # Cleanup old versions if enabled
            if enable_versioning and keep_versions > 0:
                _cleanup_old_versions(nas_path, keep_versions)
            
            # Create symlink pointing to versioned file
            local_path.unlink()
            os.symlink(str(nas_path_versioned), str(local_path))
            
            # Create stub file (hybrid mode) - reference versioned path
            if mode in ['hybrid', 'stub']:
                stub_path = Path(str(local_path) + '.offloaded')
                create_stub_file(stub_path, {
                    'nas_path': str(nas_path_versioned),
                    'size': file_size,
                    'offloaded_at': datetime.now().isoformat(),
                    'end_state': end_state
                })
            
            logger.info(f"Successfully offloaded: {rel_path} -> {nas_path_versioned.name} ({file_size} bytes)")
            return True
            
        except Exception as e:
            logger.error(f"OFFLOAD failed: {e}")
            # Queue for retry
            cache.queue_offload(rel_path, end_state)
            return False
