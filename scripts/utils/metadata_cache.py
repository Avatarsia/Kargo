"""SQLite metadata cache for tracking offloaded files"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager


logger = logging.getLogger(__name__)


class MetadataCache:
    """SQLite cache for file metadata and offload queue"""
    
    def __init__(self, db_path: Path):
        """
        Initialize metadata cache.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()
    
    @contextmanager
    def _get_connection(self):
        """Context manager for database connections"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row  # Return dicts instead of tuples
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def _init_database(self):
        """Create database schema if not exists"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Files table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS files (
                    rel_path TEXT PRIMARY KEY,
                    size INTEGER NOT NULL,
                    nas_path TEXT NOT NULL,
                    nas_target TEXT DEFAULT 'primary',
                    offloaded_at TIMESTAMP NOT NULL,
                    last_accessed TIMESTAMP,
                    print_count INTEGER DEFAULT 0
                )
            ''')
            
            # Offload queue table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS offload_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rel_path TEXT NOT NULL,
                    end_state TEXT NOT NULL,
                    queued_at TIMESTAMP NOT NULL,
                    retry_count INTEGER DEFAULT 0,
                    last_retry TIMESTAMP
                )
            ''')
            
            # Config table for storing runtime config
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP NOT NULL
                )
            ''')
            
            # Create indices
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_files_nas_target 
                ON files(nas_target)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_queue_queued_at 
                ON offload_queue(queued_at)
            ''')
            columns = {row[1] for row in cursor.execute('PRAGMA table_info(offload_queue)')}
            if 'revision' not in columns:
                cursor.execute('ALTER TABLE offload_queue ADD COLUMN revision INTEGER NOT NULL DEFAULT 0')
            # Keep the oldest queued request and its retry history on upgrade.
            cursor.execute('''
                DELETE FROM offload_queue
                WHERE id NOT IN (
                    SELECT MIN(id) FROM offload_queue GROUP BY rel_path
                )
            ''')
            cursor.execute('''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_rel_path
                ON offload_queue(rel_path)
            ''')
            
            logger.info(f"Initialized metadata cache at {self.db_path}")
    
    def update_file(self, rel_path: str, size: int, nas_path: str, 
                    nas_target: str = 'primary') -> None:
        """
        Update or insert file metadata.
        
        Args:
            rel_path: Relative path to file
            size: File size in bytes
            nas_path: Full path on NAS
            nas_target: NAS target name
        """
        with self._get_connection() as conn:
            offloaded_at = datetime.now().isoformat(' ')
            cursor = conn.execute('''
                UPDATE files
                SET size = ?, nas_path = ?, nas_target = ?, offloaded_at = ?
                WHERE rel_path = ?
            ''', (size, nas_path, nas_target, offloaded_at, rel_path))
            if cursor.rowcount == 0:
                conn.execute('''
                    INSERT INTO files
                    (rel_path, size, nas_path, nas_target, offloaded_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (rel_path, size, nas_path, nas_target, offloaded_at))
        
        logger.debug(f"Updated metadata for {rel_path}: {size} bytes")
    
    def get_file_metadata(self, rel_path: str) -> Optional[Dict[str, Any]]:
        """
        Get file metadata.
        
        Args:
            rel_path: Relative path to file
        
        Returns:
            Dict with metadata or None if not found
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                'SELECT * FROM files WHERE rel_path = ?',
                (rel_path,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_files_metadata(self, rel_paths: List[str]) -> Dict[str, Dict[str, Any]]:
        """Get existing metadata in batches using one database connection."""
        if not rel_paths:
            return {}
        metadata = {}
        with self._get_connection() as conn:
            for start in range(0, len(rel_paths), 900):
                batch = rel_paths[start:start + 900]
                placeholders = ','.join('?' for _ in batch)
                cursor = conn.execute(
                    'SELECT * FROM files WHERE rel_path IN ({})'.format(placeholders),
                    batch
                )
                for row in cursor:
                    metadata[row['rel_path']] = dict(row)
        return metadata
    
    def get_file_size(self, rel_path: str) -> Optional[int]:
        """
        Get cached file size.
        
        Args:
            rel_path: Relative path to file
        
        Returns:
            File size in bytes or None if not found
        """
        metadata = self.get_file_metadata(rel_path)
        return metadata['size'] if metadata else None
    
    def record_access(self, rel_path: str) -> None:
        """
        Record file access (for statistics).
        
        Args:
            rel_path: Relative path to file
        """
        with self._get_connection() as conn:
            conn.execute('''
                UPDATE files 
                SET last_accessed = ?, print_count = print_count + 1
                WHERE rel_path = ?
            ''', (datetime.now().isoformat(' '), rel_path))
    
    def delete_file(self, rel_path: str) -> None:
        """
        Remove file from cache.
        
        Args:
            rel_path: Relative path to file
        """
        with self._get_connection() as conn:
            conn.execute('DELETE FROM files WHERE rel_path = ?', (rel_path,))
        logger.debug(f"Deleted metadata for {rel_path}")
    
    def queue_offload(self, rel_path: str, end_state: str) -> None:
        """
        Add file to offload retry queue.
        
        Args:
            rel_path: Relative path to file
            end_state: Print end state (complete, cancelled, error)
        """
        with self._get_connection() as conn:
            cursor = conn.execute('''
                UPDATE offload_queue SET end_state = ?, revision = revision + 1 WHERE rel_path = ?
            ''', (end_state, rel_path))
            if cursor.rowcount == 0:
                conn.execute('''
                    INSERT INTO offload_queue
                    (rel_path, end_state, queued_at)
                    VALUES (?, ?, ?)
                ''', (rel_path, end_state, datetime.now().isoformat(' ')))
        
        logger.info(f"Queued {rel_path} for retry offload")
    
    def get_pending_offloads(self, limit: int = 10,
                             after_id: int = 0) -> List[Dict[str, Any]]:
        """
        Get pending offload queue items.
        
        Args:
            limit: Maximum number of items to return
            after_id: Return only items with IDs greater than this cursor
        
        Returns:
            List of queued offload dicts
        """
        with self._get_connection() as conn:
            cursor = conn.execute('''
                SELECT * FROM offload_queue 
                WHERE id > ?
                ORDER BY id ASC
                LIMIT ?
            ''', (after_id, limit))
            return [dict(row) for row in cursor.fetchall()]
    
    def remove_from_queue(self, queue_id: int, revision: Optional[int] = None) -> None:
        """
        Remove item from offload queue.
        
        Args:
            queue_id: Queue item ID
        """
        with self._get_connection() as conn:
            if revision is None:
                conn.execute('DELETE FROM offload_queue WHERE id = ?', (queue_id,))
            else:
                conn.execute('DELETE FROM offload_queue WHERE id = ? AND revision = ?',
                             (queue_id, revision))
    
    def increment_retry(self, queue_id: int) -> None:
        """
        Increment retry count for queued item.
        
        Args:
            queue_id: Queue item ID
        """
        with self._get_connection() as conn:
            conn.execute('''
                UPDATE offload_queue 
                SET retry_count = retry_count + 1, last_retry = ?
                WHERE id = ?
            ''', (datetime.now().isoformat(' '), queue_id))
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dict with statistics
        """
        with self._get_connection() as conn:
            cursor = conn.execute('''
                SELECT 
                    COUNT(*) as total_files,
                    SUM(size) as total_size,
                    SUM(print_count) as total_prints
                FROM files
            ''')
            stats = dict(cursor.fetchone())
            
            cursor = conn.execute('SELECT COUNT(*) as queue_size FROM offload_queue')
            stats.update(dict(cursor.fetchone()))
            
            return stats
    
    def set_config(self, key: str, value: str) -> None:
        """
        Store config value.
        
        Args:
            key: Config key
            value: Config value (as string)
        """
        with self._get_connection() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO config (key, value, updated_at)
                VALUES (?, ?, ?)
            ''', (key, value, datetime.now().isoformat(' ')))
    
    def get_config(self, key: str, default: str = None) -> Optional[str]:
        """
        Get config value.
        
        Args:
            key: Config key
            default: Default value if not found
        
        Returns:
            Config value or default
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                'SELECT value FROM config WHERE key = ?',
                (key,)
            )
            row = cursor.fetchone()
            return row['value'] if row else default
