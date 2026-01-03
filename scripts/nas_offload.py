"""
KARGO - Klipper Archive & Remote G-code Operations
Moonraker Component

Automatically offloads G-code files to NAS after print completion
and fetches them back for reprints.

Configuration in moonraker.conf:
    [nas_offload]
    nas_mount_point: /mnt/nas
    nas_gcodes_dir: /mnt/nas/klipper_gcodes
    offload_mode: hybrid
    offload_on_complete: True
"""

import os
import logging
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional

from .utils import (
    fetch, offload, MetadataCache, 
    sanitize_path, InvalidPathError
)


logger = logging.getLogger(__name__)


class NasOffload:
    """Moonraker component for NAS offload functionality"""
    
    def __init__(self, config):
        """
        Initialize NAS Offload component.
        
        Args:
            config: Moonraker configuration object
        """
        self.server = config.get_server()
        self.name = config.get_name()
        
        # Load configuration
        self.nas_mount_point = Path(config.get('nas_mount_point', '/mnt/nas'))
        self.nas_gcodes_dir = Path(config.get('nas_gcodes_dir', '/mnt/nas/klipper_gcodes'))
        self.offload_mode = config.get('offload_mode', 'hybrid')
        
        # Offload triggers
        self.offload_on_complete = config.getboolean('offload_on_complete', True)
        self.offload_on_cancelled = config.getboolean('offload_on_cancelled', False)
        self.offload_on_error = config.getboolean('offload_on_error', False)
        
        # Versioning settings
        self.enable_versioning = config.getboolean('enable_versioning', True)
        self.keep_versions = config.getint('keep_versions', 5)
        
        # Paths
        self.local_gcodes_dir = Path(config.get(
            'local_gcodes_dir',
            '~/printer_data/gcodes'
        )).expanduser()
        
        self.lock_dir = Path(config.get(
            'lock_dir',
            '/var/run/klipper-nas-offload'
        ))
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize metadata cache
        cache_db_path = Path(config.get(
            'cache_db',
            '~/printer_data/database/nas_offload.db'
        )).expanduser()
        self.cache = MetadataCache(cache_db_path)
        
        # State tracking
        self.last_print_filename = None
        self.last_print_state = None
        self.is_fetching = False
        self.nas_healthy = False
        
        # Register API endpoints
        self.server.register_endpoint(
            "/server/nas_offload/status",
            ['GET'],
            self._handle_status_request
        )
        self.server.register_endpoint(
            "/server/nas_offload/list",
            ['GET'],
            self._handle_list_request
        )
        self.server.register_endpoint(
            "/server/nas_offload/fetch",
            ['POST'],
            self._handle_fetch_request
        )
        self.server.register_endpoint(
            "/server/nas_offload/offload",
            ['POST'],
            self._handle_offload_request
        )
        self.server.register_endpoint(
            "/server/nas_offload/health",
            ['GET'],
            self._handle_health_request
        )
        
        # Register remote method for Klipper macros
        self.server.register_remote_method(
            "nas_offload_fetch",
            self.remote_fetch
        )
        
        # Subscribe to Klippy events
        self.server.register_event_handler(
            "server:klippy_ready",
            self._handle_klippy_ready
        )
        
        logger.info(f"KARGO initialized: {self.nas_gcodes_dir}")
        logger.info(f"Offload mode: {self.offload_mode}")
        logger.info(f"Triggers: complete={self.offload_on_complete}, "
                   f"cancelled={self.offload_on_cancelled}, error={self.offload_on_error}")
        logger.info(f"Versioning: enabled={self.enable_versioning}, keep={self.keep_versions}")
        
        # Start background tasks
        self.server.register_notification("nas_offload:ready")
    
    async def component_init(self):
        """Called after component is loaded"""
        # Start health check loop
        asyncio.create_task(self._health_check_loop())
        
        # Process any pending offloads from previous session
        asyncio.create_task(self._process_pending_offloads())
    
    async def _handle_klippy_ready(self):
        """Handle Klippy ready event - subscribe to print_stats"""
        klippy = self.server.lookup_component('klippy_connection')
        
        # Subscribe to print_stats updates
        result = await klippy.subscribe_objects({'print_stats': None})
        logger.info("Subscribed to print_stats")
        
        # Register status update callback
        klippy.register_method('notify_status_update', self._status_update)
    
    async def _status_update(self, data, eventtime):
        """
        Handle print_stats status updates.
        
        Args:
            data: Status update data
            eventtime: Event timestamp
        """
        print_stats = data.get('print_stats', {})
        
        if not print_stats:
            return
        
        state = print_stats.get('state')
        filename = print_stats.get('filename', '')
        
        # Detect state transitions to end states
        if state in ['complete', 'cancelled', 'error']:
            if (state != self.last_print_state or 
                filename != self.last_print_filename):
                
                logger.info(f"Print ended: {filename} (state={state})")
                
                # Check if should offload for this state
                should_offload = (
                    (state == 'complete' and self.offload_on_complete) or
                    (state == 'cancelled' and self.offload_on_cancelled) or
                    (state == 'error' and self.offload_on_error)
                )
                
                if should_offload and filename:
                    # Offload in background
                    asyncio.create_task(self.offload_file(filename, state))
                
                # Update state tracking
                self.last_print_state = state
                self.last_print_filename = filename
    
    async def _health_check_loop(self):
        """Periodic NAS health monitoring"""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                
                # Check if NAS is mounted and writable
                if not self.nas_mount_point.exists():
                    if self.nas_healthy:
                        logger.warning(f"NAS mount point missing: {self.nas_mount_point}")
                        self.nas_healthy = False
                    continue
                
                if not os.path.ismount(str(self.nas_mount_point)):
                    if self.nas_healthy:
                        logger.warning(f"NAS not mounted: {self.nas_mount_point}")
                        self.nas_healthy = False
                    continue
                
                # Write test
                test_file = self.nas_mount_point / '.nas_offload_health'
                try:
                    test_file.write_text(f'health-check')
                    test_file.unlink()
                    
                    if not self.nas_healthy:
                        logger.info("NAS is healthy")
                        self.nas_healthy = True
                        
                        # Trigger pending offloads on recovery
                        asyncio.create_task(self._process_pending_offloads())
                        
                except Exception as e:
                    if self.nas_healthy:
                        logger.warning(f"NAS write test failed: {e}")
                        self.nas_healthy = False
                        
            except Exception as e:
                logger.error(f"Health check error: {e}")
    
    async def _process_pending_offloads(self):
        """Process offload retry queue"""
        pending = self.cache.get_pending_offloads(limit=10)
        
        if not pending:
            return
        
        logger.info(f"Processing {len(pending)} pending offloads")
        
        for item in pending:
            try:
                success = await self.offload_file(
                    item['rel_path'],
                    item['end_state'],
                    retry_item_id=item['id']
                )
                
                if success:
                    self.cache.remove_from_queue(item['id'])
                else:
                    self.cache.increment_retry(item['id'])
                    
                # Don't overwhelm system
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Retry offload failed for {item['rel_path']}: {e}")
                self.cache.increment_retry(item['id'])
    
    async def fetch_file(self, rel_path: str) -> bool:
        """
        FETCH operation - ensure file exists locally.
        
        Args:
            rel_path: Relative path to file
        
        Returns:
            True if file ready, False on error
        """
        try:
            self.is_fetching = True
            
            # Run fetch in executor (blocking I/O)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                fetch,
                rel_path,
                self.local_gcodes_dir,
                self.nas_gcodes_dir,
                self.cache,
                self.lock_dir
            )
            
            return result
            
        except InvalidPathError as e:
            logger.error(f"Invalid path: {e}")
            return False
        except Exception as e:
            logger.error(f"FETCH failed: {e}")
            return False
        finally:
            self.is_fetching = False
    
    async def offload_file(self, rel_path: str, end_state: str, 
                          retry_item_id: Optional[int] = None) -> bool:
        """
        OFFLOAD operation - archive file to NAS.
        
        Args:
            rel_path: Relative path to file
            end_state: Print end state
            retry_item_id: Queue item ID if retry
        
        Returns:
            True if offloaded, False if queued/failed
        """
        try:
            # Run offload in executor (blocking I/O)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                offload,
                rel_path,
                self.local_gcodes_dir,
                self.nas_gcodes_dir,
                self.cache,
                self.lock_dir,
                end_state,
                self.offload_mode,
                self.enable_versioning,
                self.keep_versions
            )
            
            if result:
                # Notify Moonraker file manager of change
                await self._notify_file_manager_change()
            
            return result
            
        except Exception as e:
            logger.error(f"OFFLOAD failed: {e}")
            return False
    
    async def _notify_file_manager_change(self):
        """Notify Moonraker's file manager that files changed"""
        try:
            self.server.send_event("file_manager:filelist_changed", {"root": "gcodes"})
        except Exception as e:
            logger.warning(f"Could not notify file manager: {e}")
    
    # API endpoint handlers
    
    async def _handle_status_request(self, web_request):
        """GET /server/nas_offload/status"""
        stats = self.cache.get_statistics()
        
        return {
            'nas_healthy': self.nas_healthy,
            'nas_mount_point': str(self.nas_mount_point),
            'nas_gcodes_dir': str(self.nas_gcodes_dir),
            'offload_mode': self.offload_mode,
            'is_fetching': self.is_fetching,
            'statistics': stats,
            'config': {
                'offload_on_complete': self.offload_on_complete,
                'offload_on_cancelled': self.offload_on_cancelled,
                'offload_on_error': self.offload_on_error
            }
        }
    
    async def _handle_list_request(self, web_request):
        """GET /server/nas_offload/list"""
        offloaded_files = []
        
        # Scan local directory for symlinks
        for file_path in self.local_gcodes_dir.rglob('*.gcode*'):
            if file_path.is_symlink():
                rel_path = str(file_path.relative_to(self.local_gcodes_dir))
                metadata = self.cache.get_file_metadata(rel_path)
                
                offloaded_files.append({
                    'filename': rel_path,
                    'nas_path': str(os.readlink(file_path)),
                    'size': metadata.get('size') if metadata else None,
                    'offloaded_at': metadata.get('offloaded_at') if metadata else None,
                    'nas_available': file_path.exists()
                })
        
        return {'files': offloaded_files}
    
    async def _handle_fetch_request(self, web_request):
        """POST /server/nas_offload/fetch"""
        filename = web_request.get_str('filename')
        success = await self.fetch_file(filename)
        
        return {'success': success, 'filename': filename}
    
    async def _handle_offload_request(self, web_request):
        """POST /server/nas_offload/offload"""
        filename = web_request.get_str('filename')
        end_state = web_request.get_str('end_state', 'manual')
        
        success = await self.offload_file(filename, end_state)
        
        return {'success': success, 'filename': filename}
    
    async def _handle_health_request(self, web_request):
        """GET /server/nas_offload/health"""
        return {
            'nas_healthy': self.nas_healthy,
            'nas_mounted': os.path.ismount(str(self.nas_mount_point)),
            'nas_writable': self.nas_mount_point.exists() and 
                           os.access(self.nas_mount_point, os.W_OK)
        }
    
    # Remote method for Klipper macros
    
    def remote_fetch(self, filename: str) -> Dict[str, Any]:
        """
        Remote method called from Klipper macros.
        
        Args:
            filename: Relative path to file
        
        Returns:
            Dict with success status
        """
        # Create async task and wait for result
        loop = asyncio.get_event_loop()
        
        # Run fetch synchronously in this context
        try:
            result = fetch(
                filename,
                self.local_gcodes_dir,
                self.nas_gcodes_dir,
                self.cache,
                self.lock_dir
            )
            
            return {'success': result, 'filename': filename}
            
        except Exception as e:
            logger.error(f"Remote FETCH failed: {e}")
            return {'success': False, 'filename': filename, 'error': str(e)}


def load_component(config):
    """Moonraker component loader"""
    return NasOffload(config)
