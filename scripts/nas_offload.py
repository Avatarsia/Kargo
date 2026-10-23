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
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from .utils import (
    fetch, offload, MetadataCache, sanitize_path
)
from .utils.file_ops import get_offload_state, FileOperationError
from .utils.path_utils import InvalidPathError, ALLOWED_EXTENSIONS


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
            str(self.local_gcodes_dir.parent / 'systemd' / 'nas-offload-locks')
        )).expanduser()
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
        self._print_stats = {}
        self._print_ended = False
        self._print_state_revision = 0
        self._print_start_pending = False
        self._start_generation = 0
        self._background_tasks = set()
        self._processing_pending = False
        self._closing = False
        self._lifecycle_cancelled = set()
        
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
        
        for event in ('server:klippy_disconnect', 'server:klippy_shutdown',
                      'klippy_apis:cancel_requested'):
            self.server.register_event_handler(event, self._invalidate_print_start)

        logger.info(f"KARGO initialized: {self.nas_gcodes_dir}")
        logger.info(f"Offload mode: {self.offload_mode}")
        logger.info(f"Triggers: complete={self.offload_on_complete}, "
                   f"cancelled={self.offload_on_cancelled}, error={self.offload_on_error}")
        logger.info(f"Versioning: enabled={self.enable_versioning}, keep={self.keep_versions}")
        
        # Start background tasks
        self.server.register_notification("nas_offload:ready")
    
    async def component_init(self):
        """Called after component is loaded"""
        if self._closing:
            return
        await self._refresh_health()
        self._start_task(self._health_check_loop())
        self._start_task(self._retry_loop())

    async def close(self):
        """Cancel and await component-owned work at server shutdown."""
        self._closing = True
        self._invalidate_print_start()
        tasks = list(self._background_tasks)
        self._lifecycle_cancelled.update(tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.difference_update(tasks)
        self._lifecycle_cancelled.difference_update(tasks)

    async def _run_io(self, callback, *args):
        return await asyncio.get_event_loop().run_in_executor(None, callback, *args)
    
    def _invalidate_print_start(self):
        self._start_generation += 1

    async def _run_transfer(self, callback, *args):
        # Moonraker's actual upload/move/copy/delete lock (pinned API contract).
        manager = self.server.lookup_component('file_manager')
        async with manager.sync_lock:
            if self._closing:
                return False
            future = asyncio.get_event_loop().run_in_executor(None, callback, *args)
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                # A thread cannot be cancelled. Never release the upload lock
                # while it can still replace/remove local directory entries.
                while not future.done():
                    try:
                        await asyncio.shield(future)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not future.cancelled():
                    future.exception()
                raise

    async def _handle_klippy_ready(self):
        """Handle Klippy ready event - subscribe to print_stats"""
        self._invalidate_print_start()
        generation = self._start_generation
        if self._closing:
            return
        klippy = self.server.lookup_component('klippy_apis')
        result = await klippy.subscribe_objects(
            {'print_stats': None}, callback=self._status_update
        )
        if self._closing or generation != self._start_generation:
            return
        # Seed the snapshot without re-offloading a print finished before startup.
        self._print_stats = dict(result.get('print_stats', {}))
        self.last_print_state = self._print_stats.get('state')
        self.last_print_filename = self._print_stats.get('filename', '')
        self._print_ended = self.last_print_state in ('complete', 'cancelled', 'error')
        logger.info("Subscribed to print_stats")

    def _start_task(self, coroutine):
        if self._closing:
            coroutine.close()
            return None
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task):
        self._background_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("NAS background task failed: %s", task.exception())
    
    async def _status_update(self, data, eventtime):
        """
        Handle print_stats status updates.
        
        Args:
            data: Status update data
            eventtime: Event timestamp
        """
        print_stats = data.get('print_stats', {})
        
        if self._closing or not print_stats:
            return
        
        self._print_stats.update(print_stats)
        if 'state' in print_stats:
            self._print_state_revision += 1
            if print_stats['state'] in ('cancelled', 'error'):
                self._invalidate_print_start()
        state = self._print_stats.get('state')
        filename = self._print_stats.get('filename', '')
        self.last_print_state = state
        self.last_print_filename = filename
        if state in ('printing', 'paused'):
            self._print_ended = False
        elif state in ('complete', 'cancelled', 'error') and not self._print_ended:
            self._print_ended = True
            should_offload = (
                (state == 'complete' and self.offload_on_complete) or
                (state == 'cancelled' and self.offload_on_cancelled) or
                (state == 'error' and self.offload_on_error)
            )
            if should_offload and filename:
                self._start_task(self.offload_file(filename, state))
    
    async def _health_check_loop(self):
        """Periodic NAS health monitoring"""
        while True:
            await asyncio.sleep(60)
            try:
                await self._refresh_health()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Health check error: {e}")
                self.nas_healthy = False

    def _probe_health(self):
        """All mount and write checks run in an executor, including HTTP checks."""
        mounted = os.path.ismount(str(self.nas_mount_point))
        writable = False
        if mounted:
            try:
                with tempfile.TemporaryFile(dir=str(self.nas_mount_point),
                                            prefix='.nas_offload_health_') as test_file:
                    test_file.write(b'health-check')
                    test_file.flush()
                writable = True
            except OSError:
                pass
        return {'nas_healthy': mounted and writable,
                'nas_mounted': mounted, 'nas_writable': writable}

    async def _refresh_health(self):
        health = await self._run_io(self._probe_health)
        self.nas_healthy = health['nas_healthy']
        return health

    async def _retry_loop(self):
        while True:
            try:
                if self.nas_healthy:
                    await self._process_pending_offloads()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Retry queue error: {e}")
            await asyncio.sleep(60)

    def _retry_is_due(self, item):
        last_retry = item.get('last_retry')
        if not last_retry:
            return True
        if isinstance(last_retry, str):
            last_retry = datetime.fromisoformat(last_retry)
        delay = min(3600, 60 * 2 ** min(6, max(0, item.get('retry_count', 0) - 1)))
        return (datetime.now() - last_retry).total_seconds() >= delay

    async def _process_pending_offloads(self):
        """Drain ordered pages once per pass; failed early rows cannot starve later rows."""
        if self._processing_pending or not self.nas_healthy:
            return
        self._processing_pending = True
        try:
            after_id = 0
            while self.nas_healthy:
                pending = await self._run_io(self.cache.get_pending_offloads, 10, after_id)
                if not pending:
                    return
                for item in pending:
                    after_id = item['id']
                    if not self.nas_healthy:
                        return
                    if not self._retry_is_due(item):
                        continue
                    try:
                        success = await self.offload_file(
                            item['rel_path'], item['end_state'], retry_item_id=item['id']
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.error(f"Retry offload failed for {item['rel_path']}: {e}")
                        success = False
                    if success:
                        await self._run_io(self.cache.remove_from_queue, item['id'], item['revision'])
                    else:
                        await self._run_io(self.cache.increment_retry, item['id'])
        finally:
            self._processing_pending = False
    
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
            result = await self._run_transfer(
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
        except asyncio.CancelledError:
            raise
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
            result = await self._run_transfer(
                offload,
                rel_path,
                self.local_gcodes_dir,
                self.nas_gcodes_dir,
                self.cache,
                self.lock_dir,
                end_state,
                self.offload_mode,
                self.enable_versioning,
                self.keep_versions,
                self.nas_mount_point
            )
            
            if result:
                # Notify Moonraker file manager of change
                await self._notify_file_manager_change()
            
            return result
            
        except asyncio.CancelledError:
            raise
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
        stats = await self._run_io(self.cache.get_statistics)
        
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
        offset = max(0, web_request.get_int('offset', 0))
        limit = min(1000, max(1, web_request.get_int('limit', 100)))
        return await self._run_io(self._list_offloaded_files, offset, limit)

    def _list_offloaded_files(self, offset, limit):
        paths = set()
        for path in self.local_gcodes_dir.rglob('*'):
            relative = str(path.relative_to(self.local_gcodes_dir))
            if any(part.startswith('.') for part in Path(relative).parts):
                continue  # Incomplete transfers and retained conflict recovery.
            if relative.endswith('.offloaded'):
                original = relative[:-len('.offloaded')]
                original_path = self.local_gcodes_dir / original
                if original_path.exists() or original_path.is_symlink():
                    continue
                relative = original
            elif not path.is_symlink():
                continue
            if any(relative.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS):
                paths.add(relative)
        paths = sorted(paths)
        page = paths[offset:offset + limit]
        metadata_by_path = self.cache.get_files_metadata(page)
        offloaded_files = []
        for rel_path in page:
            metadata = metadata_by_path.get(rel_path, {})
            try:
                state = get_offload_state(rel_path, self.local_gcodes_dir, self.nas_gcodes_dir)
            except (OSError, ValueError, FileOperationError) as exc:
                state = {'nas_available': False, 'error': str(exc), 'effective_mode': 'unknown'}
            if state is None:
                continue  # A concurrent fetch/upload materialized this entry.
            offloaded_files.append(dict(state, filename=rel_path,
                                        size=metadata.get('size'),
                                        offloaded_at=metadata.get('offloaded_at')))
        return {'files': offloaded_files, 'offset': offset, 'limit': limit,
                'total': len(paths), 'has_more': offset + limit < len(paths)}

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
        response = {'success': success, 'filename': filename,
                    'requested_mode': self.offload_mode}
        if success:
            state = await self._run_io(get_offload_state, filename,
                                       self.local_gcodes_dir, self.nas_gcodes_dir)
            response.update(state or {'effective_mode': 'local'})
        return response
    
    async def _handle_health_request(self, web_request):
        """GET /server/nas_offload/health"""
        return await self._refresh_health()
    
    # Remote method for Klipper macros
    
    async def remote_fetch(self, filename: str, start_print: bool = False) -> Dict[str, Any]:
        """
        Remote method called from Klipper macros.
        
        Args:
            filename: Relative path to file
        
        Returns:
            Dict with success status
        """
        if self._closing:
            return {'success': False, 'filename': filename, 'error': 'Component is closing'}
        # The framework owns the dispatcher; the component owns this workflow.
        generation = self._start_generation
        task = self._start_task(self._remote_fetch(filename, start_print, generation))
        completed = asyncio.get_event_loop().create_future()

        def finish(workflow):
            try:
                if completed.done():
                    return
                if workflow.cancelled():
                    if workflow in self._lifecycle_cancelled:
                        completed.set_result({'success': False, 'filename': filename,
                                              'error': 'Component is closing'})
                    else:
                        completed.cancel()
                elif workflow.exception() is not None:
                    completed.set_exception(workflow.exception())
                else:
                    completed.set_result(workflow.result())
            finally:
                self._lifecycle_cancelled.discard(workflow)

        task.add_done_callback(finish)
        try:
            return await completed
        except asyncio.CancelledError:
            task.cancel()
            raise

    async def _remote_fetch(self, filename: str, start_print: bool, generation: int) -> Dict[str, Any]:
        if start_print and generation != self._start_generation:
            return {'success': False, 'filename': filename, 'error': 'Print request invalidated'}
        if not filename or any(char in filename for char in ('\r', '\n', '\0')):
            return {'success': False, 'filename': filename, 'error': 'Invalid filename'}
        if start_print and (
            self._print_start_pending or self.last_print_state in ('printing', 'paused')
        ):
            return {'success': False, 'filename': filename, 'error': 'Print already active'}
        if start_print:
            self._print_start_pending = True
        try:
            result = await self.fetch_file(filename)
            if self._closing:
                return {'success': False, 'filename': filename, 'error': 'Component is closing'}
            if start_print and generation != self._start_generation:
                return {'success': False, 'filename': filename, 'error': 'Print request invalidated'}
            if result and start_print:
                # A separate print may have started while the transfer was running.
                if self.last_print_state in ('printing', 'paused'):
                    return {'success': False, 'filename': filename, 'error': 'Print already active'}
                escaped = filename.replace('\\', '\\\\').replace('"', '\\"')
                klippy = self.server.lookup_component('klippy_apis')
                state_revision = self._print_state_revision
                if self._closing:
                    return {'success': False, 'filename': filename, 'error': 'Component is closing'}
                await klippy.run_gcode('_SDCARD_PRINT_FILE_ORIGINAL FILENAME="%s"' % escaped)
                # Close the gap until the next print_stats notification arrives.
                if state_revision == self._print_state_revision:
                    self._print_stats.update(state='printing', filename=filename)
                    self.last_print_state = 'printing'
                    self.last_print_filename = filename
                    self._print_ended = False
            return {'success': result, 'filename': filename}
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Remote FETCH failed: {e}")
            return {'success': False, 'filename': filename, 'error': str(e)}
        finally:
            if start_print:
                self._print_start_pending = False


def load_component(config):
    """Moonraker component loader"""
    return NasOffload(config)
