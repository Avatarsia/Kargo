"""Component regressions against strict Moonraker API boundaries."""

import asyncio
import importlib
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest


class KlippyAPI:
    def __init__(self):
        self.snapshot = {'print_stats': {'state': 'standby', 'filename': ''}}
        self.callback = None
        self.scripts = []

    async def subscribe_objects(self, objects, callback=None):
        assert objects == {'print_stats': None}
        assert callable(callback)
        self.callback = callback
        return self.snapshot

    async def run_gcode(self, script):
        self.scripts.append(script)
        return 'ok'


class Server:
    def __init__(self):
        self.klippy = KlippyAPI()
        self.endpoints = {}
        self.events = {}
        self.remote_methods = {}
        self.sync_lock = asyncio.Lock()

    def register_endpoint(self, path, methods, callback):
        # Official APIDefinition.create accepts these legacy list forms too.
        assert methods in (['GET'], ['POST'])
        assert asyncio.iscoroutinefunction(callback)
        self.endpoints[path] = callback

    def register_remote_method(self, name, callback):
        self.remote_methods[name] = callback

    def register_event_handler(self, name, callback):
        self.events[name] = callback

    def register_notification(self, name):
        assert name == 'nas_offload:ready'

    def lookup_component(self, name):
        if name == 'file_manager':
            return self
        assert name == 'klippy_apis'
        return self.klippy

    def send_event(self, name, payload):
        assert name == 'file_manager:filelist_changed'
        assert payload == {'root': 'gcodes'}


class Config:
    def __init__(self, root):
        self.server = Server()
        self.values = {
            'local_gcodes_dir': str(root / 'gcodes'),
            'nas_mount_point': str(root / 'nas'),
            'nas_gcodes_dir': str(root / 'nas' / 'gcodes'),
            'cache_db': str(root / 'database' / 'cache.db'),
            'lock_dir': str(root / 'locks'),
        }

    def get_server(self):
        return self.server

    def get_name(self):
        return 'nas_offload'

    def get(self, name, default=None):
        return self.values.get(name, default)

    getboolean = get
    getint = get


class Request:
    def __init__(self, **values):
        self.values = values

    def get_int(self, name, default):
        return int(self.values.get(name, default))

    def get_str(self, name, default=None):
        return self.values.get(name, default)


@pytest.fixture
def module():
    return importlib.import_module('scripts.nas_offload')


@pytest.fixture
def component(module, tmp_path):
    config = Config(tmp_path)
    config_path = Path(config.values['local_gcodes_dir'])
    config_path.mkdir()
    Path(config.values['nas_gcodes_dir']).mkdir(parents=True)
    return module.NasOffload(config)


def test_component_imports_without_package_exception_reexport():
    from scripts.utils.path_utils import InvalidPathError
    module = importlib.import_module('scripts.nas_offload')
    assert module.InvalidPathError is InvalidPathError


def test_default_locks_are_under_data_root_and_expanduser(module, tmp_path, monkeypatch):
    config = Config(tmp_path)
    del config.values['lock_dir']
    instance = module.NasOffload(config)
    assert instance.lock_dir == tmp_path / 'systemd' / 'nas-offload-locks'
    assert instance.lock_dir.is_dir()
    monkeypatch.setenv('HOME', str(tmp_path))
    config.values['lock_dir'] = '~/custom-locks'
    assert module.NasOffload(config).lock_dir == tmp_path / 'custom-locks'


@pytest.mark.asyncio
async def test_official_subscription_merges_initial_snapshot(component):
    component.server.klippy.snapshot = {
        'print_stats': {'state': 'printing', 'filename': 'snapshot.gcode', 'total_duration': 5}
    }
    await component.server.events['server:klippy_ready']()
    assert component.server.klippy.callback == component._status_update
    assert component.last_print_state == 'printing'
    assert component.last_print_filename == 'snapshot.gcode'


@pytest.mark.asyncio
async def test_delta_status_repeated_prints_and_terminal_duplicates(component, monkeypatch):
    ended = []

    async def offload(filename, state):
        ended.append((filename, state))

    monkeypatch.setattr(component, 'offload_file', offload)
    for cycle in range(2):
        await component._status_update({'print_stats': {'state': 'printing', 'filename': 'same.gcode'}}, cycle)
        await component._status_update({'print_stats': {'state': 'paused'}}, cycle)
        await component._status_update({'print_stats': {'state': 'printing'}}, cycle)
        await component._status_update({'print_stats': {'state': 'complete'}}, cycle)
        await component._status_update({'print_stats': {'total_duration': 12}}, cycle)
        await component._status_update({'print_stats': {'state': 'complete'}}, cycle)
        await asyncio.sleep(0)
    assert ended == [('same.gcode', 'complete'), ('same.gcode', 'complete')]


@pytest.mark.asyncio
async def test_remote_start_waits_for_success_and_escapes_filename(component, monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()

    async def fetch(filename):
        started.set()
        await release.wait()
        return True

    monkeypatch.setattr(component, 'fetch_file', fetch)
    filename = 'folder/a "quote" \\part.gcode'
    task = asyncio.create_task(component.remote_fetch(filename, start_print=True))
    await started.wait()
    assert component.server.klippy.scripts == []
    duplicate = await component.remote_fetch('other.gcode', start_print=True)
    assert duplicate['success'] is False
    release.set()
    assert (await task)['success'] is True
    assert component.server.klippy.scripts == [
        '_SDCARD_PRINT_FILE_ORIGINAL FILENAME="folder/a \\"quote\\" \\\\part.gcode"'
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', [False, RuntimeError('NAS unavailable')])
async def test_failed_fetch_never_starts_and_releases_gate(component, monkeypatch, outcome):
    async def fetch(filename):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(component, 'fetch_file', fetch)
    assert (await component.remote_fetch('part.gcode', start_print=True))['success'] is False
    assert component.server.klippy.scripts == []
    assert (await component.remote_fetch('part.gcode', start_print=True))['success'] is False


@pytest.mark.asyncio
async def test_ordinary_remote_fetch_does_not_start(component, monkeypatch):
    async def fetch(filename):
        return True

    monkeypatch.setattr(component, 'fetch_file', fetch)
    assert (await component.remote_fetch('part.gcode'))['success'] is True
    assert component.server.klippy.scripts == []


@pytest.mark.asyncio
@pytest.mark.parametrize('start_print', [False, True])
async def test_close_cancels_dispatched_remote_fetch_before_completion(component, monkeypatch, start_print):
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def fetch(filename):
        entered.set()
        try:
            await release.wait()
            return True
        finally:
            finished.set()

    monkeypatch.setattr(component, 'fetch_file', fetch)
    existing_tasks = asyncio.all_tasks()
    dispatch = component.server.remote_methods['nas_offload_fetch']
    remote = asyncio.create_task(dispatch('pending.gcode', start_print=start_print))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(component.close(), 1)
        finished_before_release = finished.is_set()
    finally:
        release.set()
        outcome = await asyncio.gather(remote, return_exceptions=True)
    assert component.server.klippy.scripts == []
    assert finished_before_release, 'close returned with the remote fetch still running'
    assert outcome[0]['success'] is False
    assert not component._print_start_pending
    assert not component._background_tasks
    assert asyncio.all_tasks() <= existing_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize('start_print', [False, True])
async def test_remote_fetch_after_close_is_rejected_before_transfer(component, monkeypatch, start_print):
    attempted = []

    async def fetch(filename):
        attempted.append(filename)
        return True

    monkeypatch.setattr(component, 'fetch_file', fetch)
    await component.close()
    dispatch = component.server.remote_methods['nas_offload_fetch']
    result = await dispatch('late.gcode', start_print=start_print)
    assert result['success'] is False
    assert attempted == []
    assert component.server.klippy.scripts == []
    assert not component._background_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize('start_print', [False, True])
async def test_remote_fetch_returning_success_during_close_cannot_succeed_or_start(component, monkeypatch, start_print):
    entered, release = asyncio.Event(), asyncio.Event()

    async def fetch(filename):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            # An awaited dependency may finish successfully despite cancellation.
            return True
        return True

    monkeypatch.setattr(component, 'fetch_file', fetch)
    dispatch = component.server.remote_methods['nas_offload_fetch']
    remote = asyncio.create_task(dispatch('pending.gcode', start_print=start_print))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(component.close(), 1)
    finally:
        release.set()
        result = await remote
    assert component.server.klippy.scripts == []
    assert result['success'] is False
    assert not component._print_start_pending
    assert not component._background_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['printing', 'paused'])
async def test_start_rejected_during_active_print(component, monkeypatch, state):
    await component._status_update({'print_stats': {'state': state, 'filename': 'busy.gcode'}}, 0)

    async def forbidden(filename):
        pytest.fail('must reject before fetching while print is active')

    monkeypatch.setattr(component, 'fetch_file', forbidden)
    assert (await component.remote_fetch('new.gcode', start_print=True))['success'] is False
    assert component.server.klippy.scripts == []


@pytest.mark.asyncio
@pytest.mark.parametrize('filename', ['bad\rname.gcode', 'bad\nname.gcode', 'bad\0name.gcode'])
async def test_remote_rejects_gcode_control_characters(component, monkeypatch, filename):
    async def forbidden(filename):
        pytest.fail('control character reached fetch')

    monkeypatch.setattr(component, 'fetch_file', forbidden)
    assert (await component.remote_fetch(filename, start_print=True))['success'] is False
    assert component.server.klippy.scripts == []


@pytest.mark.asyncio
async def test_remote_transfer_keeps_event_loop_responsive(component, module, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow_fetch(*args):
        entered.set()
        return release.wait(0.5)

    monkeypatch.setattr(module, 'fetch', slow_fetch)
    task = asyncio.create_task(component.remote_fetch('part.gcode'))
    try:
        await asyncio.sleep(0.05)
        assert entered.is_set()
        assert not task.done(), 'transfer blocked the event loop until its timeout'
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_offload_passes_configured_mount_in_executor(component, module, monkeypatch):
    main_thread = threading.get_ident()
    received = []

    def transfer(*args):
        assert threading.get_ident() != main_thread
        received.append(args)
        return True

    monkeypatch.setattr(module, 'offload', transfer)
    assert await component.offload_file('part.gcode', 'complete')
    assert received == [(
        'part.gcode', component.local_gcodes_dir, component.nas_gcodes_dir,
        component.cache, component.lock_dir, 'complete', 'hybrid', True, 5,
        component.nas_mount_point,
    )]


@pytest.mark.asyncio
async def test_list_batches_page_metadata_in_worker_and_checks_live_targets(component, monkeypatch):
    for index in range(4):
        filename = '%02d.gcode' % index
        target = component.nas_gcodes_dir / filename
        target.write_text('G28')
        (component.local_gcodes_dir / filename).symlink_to(target)
        component.cache.update_file(filename, 3, str(target))
    main_thread = threading.get_ident()
    batches = []
    original = component.cache.get_files_metadata
    original_scan = Path.rglob

    def scan(path, pattern):
        assert threading.get_ident() != main_thread
        return original_scan(path, pattern)

    def batch(paths):
        assert threading.get_ident() != main_thread
        batches.append(paths)
        return original(paths)

    def forbidden(path):
        pytest.fail('per-file metadata lookup instead of batch')

    monkeypatch.setattr(component.cache, 'get_files_metadata', batch)
    monkeypatch.setattr(component.cache, 'get_file_metadata', forbidden)
    monkeypatch.setattr(Path, 'rglob', scan)
    result = await component._handle_list_request(Request(offset=1, limit=2))
    assert batches == [['01.gcode', '02.gcode']]
    assert result['total'] == 4
    assert result['offset'] == 1 and result['limit'] == 2 and result['has_more'] is True
    assert [item['filename'] for item in result['files']] == ['01.gcode', '02.gcode']
    assert result['files'][0]['size'] == 3
    assert result['files'][0]['offloaded_at']
    assert result['files'][0]['nas_available'] is True
    (component.nas_gcodes_dir / '01.gcode').unlink()
    assert (await component._handle_list_request(Request(offset=1, limit=1)))['files'][0]['nas_available'] is False


@pytest.mark.asyncio
async def test_list_default_and_maximum_page_size(component):
    for index in range(1005):
        (component.local_gcodes_dir / ('%04d.gcode' % index)).symlink_to('/missing/target')
    default = await component._handle_list_request(Request())
    assert len(default['files']) == 100
    assert default['limit'] == 100 and default['offset'] == 0
    maximum = await component._handle_list_request(Request(limit=9999))
    assert len(maximum['files']) == 1000
    assert maximum['limit'] == 1000 and maximum['has_more'] is True
    final = await component._handle_list_request(Request(offset=1000))
    assert len(final['files']) == 5 and final['has_more'] is False


@pytest.mark.asyncio
async def test_status_database_and_health_filesystem_run_in_executor(component, module, monkeypatch):
    main_thread = threading.get_ident()
    statistics = component.cache.get_statistics

    def stats():
        assert threading.get_ident() != main_thread
        return statistics()

    def mounted(path):
        assert threading.get_ident() != main_thread
        return True

    monkeypatch.setattr(component.cache, 'get_statistics', stats)
    monkeypatch.setattr(module.os.path, 'ismount', mounted)
    assert (await component._handle_status_request(Request()))['statistics']['total_files'] == 0
    health = await component._handle_health_request(Request())
    assert health == {'nas_healthy': True, 'nas_mounted': True, 'nas_writable': True}
    # A live mount loss must be reflected despite a previously healthy cache.
    monkeypatch.setattr(module.os.path, 'ismount', lambda path: False)
    assert (await component._handle_health_request(Request()))['nas_healthy'] is False


@pytest.mark.asyncio
async def test_retry_drains_later_pages_despite_first_page_failures(component, monkeypatch):
    for index in range(25):
        component.cache.queue_offload('%02d.gcode' % index, 'complete')
    component.nas_healthy = True
    attempted = []
    main_thread = threading.get_ident()
    pending = component.cache.get_pending_offloads
    increment = component.cache.increment_retry
    remove = component.cache.remove_from_queue

    def get_pending(limit=10, after_id=0):
        assert threading.get_ident() != main_thread
        return pending(limit, after_id)

    def retry(queue_id):
        assert threading.get_ident() != main_thread
        increment(queue_id)

    def delete(queue_id, revision=None):
        assert threading.get_ident() != main_thread
        remove(queue_id, revision)

    async def transfer(filename, end_state, retry_item_id=None):
        attempted.append(filename)
        if filename == '00.gcode':
            raise OSError('temporary NAS error')
        return int(filename[:2]) >= 10

    monkeypatch.setattr(component.cache, 'get_pending_offloads', get_pending)
    monkeypatch.setattr(component.cache, 'increment_retry', retry)
    monkeypatch.setattr(component.cache, 'remove_from_queue', delete)
    monkeypatch.setattr(component, 'offload_file', transfer)
    await component._process_pending_offloads()
    assert len(attempted) == 25
    remaining = pending(100)
    assert len(remaining) == 10
    assert all(item['retry_count'] == 1 for item in remaining)


@pytest.mark.asyncio
async def test_retry_exponential_backoff_caps_at_one_hour(component, monkeypatch):
    # count, age in seconds, expected due status; fixed cases prove doubling/cap.
    cases = [(1, 30, False), (1, 61, True), (2, 61, False),
             (2, 121, True), (20, 3500, False), (20, 3601, True)]
    for index, (count, age, _) in enumerate(cases):
        component.cache.queue_offload('%d.gcode' % index, 'complete')
        with component.cache._get_connection() as connection:
            connection.execute(
                'UPDATE offload_queue SET retry_count=?, last_retry=? WHERE rel_path=?',
                (count, (datetime.now() - timedelta(seconds=age)).isoformat(' '), '%d.gcode' % index),
            )
    attempted = []

    async def transfer(filename, end_state, retry_item_id=None):
        attempted.append(filename)
        return True

    component.nas_healthy = True
    monkeypatch.setattr(component, 'offload_file', transfer)
    await component._process_pending_offloads()
    assert attempted == ['1.gcode', '3.gcode', '5.gcode']


@pytest.mark.asyncio
async def test_retry_processors_do_not_overlap(component, monkeypatch):
    component.cache.queue_offload('part.gcode', 'complete')
    component.nas_healthy = True
    entered, release = asyncio.Event(), asyncio.Event()
    attempted = []

    async def transfer(filename, end_state, retry_item_id=None):
        attempted.append(filename)
        entered.set()
        await release.wait()
        return True

    monkeypatch.setattr(component, 'offload_file', transfer)
    first = asyncio.create_task(component._process_pending_offloads())
    try:
        await entered.wait()
        await asyncio.wait_for(component._process_pending_offloads(), 0.2)
    finally:
        release.set()
        await first
    assert attempted == ['part.gcode']


@pytest.mark.asyncio
async def test_periodic_health_retries_while_still_healthy_and_close_cancels(component, module, monkeypatch):
    component.cache.queue_offload('first.gcode', 'complete')
    monkeypatch.setattr(module.os.path, 'ismount', lambda path: True)
    attempted = []
    original_sleep = asyncio.sleep

    async def fast_sleep(delay):
        await original_sleep(0.01)

    async def transfer(filename, end_state, retry_item_id=None):
        attempted.append(filename)
        return True

    monkeypatch.setattr(module.asyncio, 'sleep', fast_sleep)
    monkeypatch.setattr(component, 'offload_file', transfer)
    await component.component_init()
    try:
        for _ in range(30):
            if 'first.gcode' in attempted:
                break
            await original_sleep(0.01)
        assert attempted == ['first.gcode']
        component.cache.queue_offload('later.gcode', 'complete')
        for _ in range(30):
            if 'later.gcode' in attempted:
                break
            await original_sleep(0.01)
        assert attempted == ['first.gcode', 'later.gcode']
    finally:
        await component.close()
    assert not component._background_tasks


@pytest.mark.asyncio
async def test_close_cancels_scheduled_print_offload(component, monkeypatch):
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def transfer(filename, end_state):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(component, 'offload_file', transfer)
    await component._status_update({'print_stats': {'state': 'printing', 'filename': 'part.gcode'}}, 0)
    await component._status_update({'print_stats': {'state': 'complete'}}, 1)
    await entered.wait()
    await component.close()
    assert cancelled.is_set()
    assert not component._background_tasks


@pytest.mark.asyncio
async def test_closed_component_does_not_schedule_new_print_tasks(component, monkeypatch):
    attempted = []

    async def transfer(filename, end_state):
        attempted.append(filename)

    monkeypatch.setattr(component, 'offload_file', transfer)
    await component.close()
    await component._status_update({'print_stats': {'state': 'printing', 'filename': 'late.gcode'}}, 0)
    await component._status_update({'print_stats': {'state': 'complete'}}, 1)
    await asyncio.sleep(0)
    assert attempted == []


@pytest.mark.asyncio
async def test_close_during_initial_health_check_prevents_late_workers(component, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def health():
        entered.set()
        await release.wait()

    monkeypatch.setattr(component, '_refresh_health', health)
    initializing = asyncio.create_task(component.component_init())
    await entered.wait()
    await component.close()
    release.set()
    await initializing
    try:
        assert not component._background_tasks
    finally:
        await component.close()


@pytest.mark.asyncio
async def test_remote_start_rechecks_active_print_after_fetch(component, monkeypatch):
    async def fetch(filename):
        await component._status_update({'print_stats': {'state': 'printing', 'filename': 'external.gcode'}}, 0)
        return True

    monkeypatch.setattr(component, 'fetch_file', fetch)
    assert (await component.remote_fetch('part.gcode', start_print=True))['success'] is False
    assert component.server.klippy.scripts == []


@pytest.mark.asyncio
async def test_failed_start_command_does_not_mark_printing_or_keep_gate(component, monkeypatch):
    async def fetch(filename):
        return True

    async def run(script):
        raise RuntimeError('Klipper disconnected')

    monkeypatch.setattr(component, 'fetch_file', fetch)
    monkeypatch.setattr(component.server.klippy, 'run_gcode', run)
    assert (await component.remote_fetch('part.gcode', start_print=True))['success'] is False
    assert component.last_print_state != 'printing'
    monkeypatch.undo()
    target = component.nas_gcodes_dir / 'part.gcode'
    target.write_text('G28\nG1 X20\n')
    (component.local_gcodes_dir / 'part.gcode').symlink_to(target)
    result = await component.remote_fetch('part.gcode', start_print=True)
    assert result['success'] is True
    local = component.local_gcodes_dir / 'part.gcode'
    assert not local.is_symlink() and local.read_text() == 'G28\nG1 X20\n'
    assert component.server.klippy.scripts == ['_SDCARD_PRINT_FILE_ORIGINAL FILENAME="part.gcode"']


@pytest.mark.asyncio
@pytest.mark.parametrize('end_state,enabled', [('complete', True), ('cancelled', False),
                                            ('cancelled', True), ('error', False), ('error', True)])
async def test_terminal_triggers_respect_config_and_schedule_once(component, monkeypatch, end_state, enabled):
    setattr(component, 'offload_on_' + end_state, enabled)
    attempted = []

    async def transfer(filename, state):
        attempted.append((filename, state))

    monkeypatch.setattr(component, 'offload_file', transfer)
    await component._status_update({'print_stats': {'state': 'printing', 'filename': 'part.gcode'}}, 0)
    await component._status_update({'print_stats': {'state': end_state}}, 1)
    await component._status_update({'print_stats': {'state': 'error'}}, 2)
    await asyncio.sleep(0)
    assert attempted == ([('part.gcode', end_state)] if enabled else [])


@pytest.mark.asyncio
async def test_terminal_initial_snapshot_does_not_reoffload_old_print(component, monkeypatch):
    attempted = []

    async def transfer(filename, state):
        attempted.append(filename)

    monkeypatch.setattr(component, 'offload_file', transfer)
    component.server.klippy.snapshot = {'print_stats': {'state': 'complete', 'filename': 'old.gcode'}}
    await component._handle_klippy_ready()
    await component.server.klippy.callback({'print_stats': {'total_duration': 30}}, 0)
    await asyncio.sleep(0)
    assert attempted == []


@pytest.mark.asyncio
async def test_retry_does_not_transfer_until_nas_is_healthy(component, monkeypatch):
    component.cache.queue_offload('part.gcode', 'complete')

    async def forbidden(*args, **kwargs):
        pytest.fail('unhealthy NAS must not process queued files')

    monkeypatch.setattr(component, 'offload_file', forbidden)
    await component._process_pending_offloads()
    assert component.cache.get_statistics()['queue_size'] == 1


@pytest.mark.asyncio
async def test_print_status_arriving_before_gcode_ack_is_not_overwritten(component, monkeypatch):
    attempted = []

    async def fetch(filename):
        return True

    async def transfer(filename, state):
        attempted.append(filename)

    async def start(script):
        await component._status_update({'print_stats': {'state': 'printing', 'filename': 'short.gcode'}}, 1)
        await component._status_update({'print_stats': {'state': 'complete'}}, 2)
        return 'ok'

    monkeypatch.setattr(component, 'fetch_file', fetch)
    monkeypatch.setattr(component, 'offload_file', transfer)
    monkeypatch.setattr(component.server.klippy, 'run_gcode', start)
    assert (await component.remote_fetch('short.gcode', start_print=True))['success'] is True
    assert component.last_print_state == 'complete'
    await component._status_update({'print_stats': {'state': 'complete'}}, 3)
    await asyncio.sleep(0)
    assert attempted == ['short.gcode']


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['remote', 'fetch', 'offload'])
async def test_python37_cancellation_is_not_converted_to_transfer_failure(component, module, monkeypatch, operation):
    # In Python 3.7 CancelledError still derives from Exception.
    class LegacyCancelledError(Exception):
        pass

    monkeypatch.setattr(module.asyncio, 'CancelledError', LegacyCancelledError)

    def cancel(*args):
        raise LegacyCancelledError()

    async def cancel_fetch(filename):
        raise LegacyCancelledError()

    if operation == 'remote':
        monkeypatch.setattr(component, 'fetch_file', cancel_fetch)
        call = component.remote_fetch('part.gcode', start_print=True)
    elif operation == 'fetch':
        monkeypatch.setattr(module, 'fetch', cancel)
        call = component.fetch_file('part.gcode')
    else:
        monkeypatch.setattr(module, 'offload', cancel)
        call = component.offload_file('part.gcode', 'complete')
    with pytest.raises(LegacyCancelledError):
        await call
    assert component._print_start_pending is False


@pytest.mark.asyncio
async def test_real_offload_list_and_remote_fetch_roundtrip(component, module, monkeypatch):
    monkeypatch.setattr(module.os.path, 'ismount', lambda path: Path(path) == component.nas_mount_point)
    local = component.local_gcodes_dir / 'roundtrip.gcode'
    content = b'G28\nG1 X23 Y45\n'
    local.write_bytes(content)
    assert await component.offload_file('roundtrip.gcode', 'complete') is True
    assert not local.exists()
    assert local.with_name(local.name + '.offloaded').is_file()
    response = await component._handle_list_request(Request())
    assert response['files'][0]['size'] == len(content)
    assert response['files'][0]['nas_available'] is True
    assert (await component.remote_fetch('roundtrip.gcode', start_print=True))['success'] is True
    assert not local.is_symlink()
    assert local.read_bytes() == content
    assert component.server.klippy.scripts == ['_SDCARD_PRINT_FILE_ORIGINAL FILENAME="roundtrip.gcode"']


@pytest.mark.asyncio
async def test_real_failed_offload_preserves_queue_identity_and_backoff(component, module, monkeypatch):
    monkeypatch.setattr(module.os.path, 'ismount', lambda path: False)
    local = component.local_gcodes_dir / 'offline.gcode'
    local.write_text('G28\n')
    component.cache.queue_offload('offline.gcode', 'complete')
    original_id = component.cache.get_pending_offloads()[0]['id']
    # Health can become stale during a pass; the transfer must still fail safely.
    component.nas_healthy = True
    await component._process_pending_offloads()
    remaining = component.cache.get_pending_offloads()
    assert len(remaining) == 1
    assert remaining[0]['id'] == original_id and remaining[0]['retry_count'] == 1
    await component._process_pending_offloads()
    assert component.cache.get_pending_offloads() == remaining
    assert local.read_text() == 'G28\n' and not local.is_symlink()
