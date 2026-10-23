"""Regression cases at the consolidated review's actual race boundaries."""

import asyncio
import errno
import inspect
import logging
import os
import stat
import threading
from io import StringIO
from pathlib import Path

import pytest

from scripts.utils import file_ops
from scripts.utils.metadata_cache import MetadataCache
from test_file_ops import storage, nas_mount_policy, archive, restore
from test_nas_offload import component, module, Request


def recoverable(root, content):
    return any(path.read_bytes() == content for path in root.rglob('*')
               if path.is_file() and not path.is_symlink())


@pytest.mark.parametrize('present', [False, True])
def test_upload_during_fetch_survives(storage, monkeypatch, present):
    local, nas, _, _ = storage
    (nas / 'part.gcode').write_bytes(b'OLD-ARCHIVE')
    if present:
        (local / 'part.gcode').symlink_to(nas / 'part.gcode')
    copy = file_ops.shutil.copyfileobj
    injected = []

    def upload(source, destination, **kwargs):
        if not injected:
            incoming = local / 'upload'
            incoming.write_bytes(b'NEW-UPLOAD')
            os.replace(incoming, local / 'part.gcode')
            injected.append(True)
        return copy(source, destination, **kwargs)

    monkeypatch.setattr(file_ops.shutil, 'copyfileobj', upload)
    restore(storage)
    assert injected
    assert recoverable(local, b'NEW-UPLOAD')


@pytest.mark.parametrize('mode', ['hybrid', 'symlink', 'stub'])
@pytest.mark.parametrize('in_place', [False, True])
@pytest.mark.parametrize('protected_mount', [False, True])
def test_upload_at_last_local_mutation_survives(storage, nas_mount_policy, monkeypatch, mode, in_place, protected_mount):
    local, _, _, _ = storage
    target = local / 'part.gcode'
    target.write_bytes(b'OLD-PRINT')
    nas_mount_policy('rw,nosymfollow' if protected_mount else 'rw')
    injected = []
    replace, rename, unlink = os.replace, os.rename, os.unlink

    def inject(name, fd):
        if str(name) == 'part.gcode' and fd is not None and not injected:
            if Path('/proc/self/fd/%s' % fd).resolve() == local:
                if in_place:
                    target.write_bytes(b'NEW-UPLOAD')
                else:
                    (local / 'incoming').write_bytes(b'NEW-UPLOAD')
                    replace(local / 'incoming', target)
                injected.append(True)

    def replacing(src, dst, **kw):
        inject(dst, kw.get('dst_dir_fd'))
        return replace(src, dst, **kw)

    def renaming(src, dst, **kw):
        inject(src, kw.get('src_dir_fd'))
        return rename(src, dst, **kw)

    def removing(name, **kw):
        inject(name, kw.get('dir_fd'))
        return unlink(name, **kw)

    monkeypatch.setattr(os, 'replace', replacing)
    monkeypatch.setattr(os, 'rename', renaming)
    monkeypatch.setattr(os, 'unlink', removing)
    archive(storage, mode=mode)
    assert injected, 'test must reach the actual local mutation'
    assert recoverable(local, b'NEW-UPLOAD')


@pytest.mark.parametrize('mode', ['hybrid', 'symlink', 'stub'])
def test_unverified_mount_uses_safe_stub_for_direct_consumers(storage, tmp_path, mode):
    local, _, cache, _ = storage
    (local / 'part.gcode').write_text('PRINT')
    assert archive(storage, mode=mode)
    target = Path(cache.get_file_metadata('part.gcode')['nas_path'])
    target.unlink()
    secret = tmp_path / 'host-secret'
    secret.write_text('HOST-SECRET')
    target.symlink_to(secret)
    try:
        observed = (local / 'part.gcode').read_text()
    except FileNotFoundError:
        observed = None
    assert observed != 'HOST-SECRET'
    stub = file_ops.read_stub_file(local / 'part.gcode.offloaded')
    assert stub['effective_mode'] == 'stub'
    assert stub['requested_mode'] == mode
    assert restore(storage) is False


@pytest.mark.parametrize('exclusive', [False, True])
def test_temp_swap_inside_publication_syscall_cannot_publish_symlink(tmp_path, monkeypatch, exclusive):
    source, target, secret = [tmp_path / x for x in ('source', 'target', 'secret')]
    source.write_text('COPY')
    secret.write_text('HOST-SECRET')
    replace, link = os.replace, os.link
    attacked = []

    def swap(src, kw):
        # The old implementation publishes .tmp by pathname. An implementation
        # with direct exclusive FD reservation has no such source boundary.
        temporary = str(src) if str(src).endswith('.tmp') else next(
            (name for name in os.listdir(kw['src_dir_fd']) if name.endswith('.tmp')), None)
        if temporary:
            os.unlink(temporary, dir_fd=kw['src_dir_fd'])
            os.symlink(str(secret), temporary, dir_fd=kw['src_dir_fd'])
            attacked.append(True)

    def replacing(src, dst, **kw):
        swap(src, kw)
        return replace(src, dst, **kw)

    def linking(src, dst, **kw):
        swap(src, kw)
        return link(src, dst, **kw)

    monkeypatch.setattr(os, 'replace', replacing)
    monkeypatch.setattr(os, 'link', linking)
    with file_ops.open_directory(tmp_path) as parent:
        descriptor = file_ops._open_regular(parent, 'source')
        try:
            try:
                file_ops._copy_descriptor(descriptor, parent, 'target', exclusive=exclusive)
            except (OSError, file_ops.FileOperationError):
                return  # A failed safe publication is permitted.
        finally:
            os.close(descriptor)
    assert not target.is_symlink()
    assert target.read_text() == 'COPY'
    assert attacked


def test_stub_offload_is_idempotent(storage):
    local, _, cache, _ = storage
    (local / 'part.gcode').write_text('PRINT')
    assert archive(storage, mode='stub')
    assert archive(storage, mode='stub')
    assert cache.get_pending_offloads() == []


@pytest.mark.parametrize('missing_root', [False, True])
def test_all_created_nas_parent_entries_synced_before_local_commit(storage, monkeypatch, missing_root):
    local, mount, cache, locks = storage
    nas = mount / 'gcodes' if missing_root else mount
    relative = 'new/nested/part.gcode'
    (local / 'new/nested').mkdir(parents=True)
    (local / relative).write_text('PRINT')
    synced = set()
    fsync = os.fsync
    expected = {nas, nas / 'new'}
    if missing_root:
        expected.add(mount)

    def sync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            synced.add(Path('/proc/self/fd/%s' % fd).resolve())
        return fsync(fd)

    monkeypatch.setattr(os, 'fsync', sync)
    for operation in ('rename', 'replace', 'unlink'):
        original = getattr(os, operation)

        def guarded(*args, _original=original, **kw):
            if any(Path('/proc/self/fd/%s' % fd).resolve() == local / 'new/nested'
                   for key, fd in kw.items() if key.endswith('dir_fd')):
                assert expected <= synced
            return _original(*args, **kw)

        monkeypatch.setattr(os, operation, guarded)
    assert file_ops.offload(relative, local, nas, cache, locks, 'complete',
                           mode='stub', nas_mount_point=mount)
    assert expected <= synced


def test_versioning_works_without_hardlinks(storage, monkeypatch):
    local, _, cache, _ = storage
    versions = []

    def unsupported(*args, **kw):
        raise OSError(errno.EOPNOTSUPP, 'hardlinks unsupported')

    monkeypatch.setattr(os, 'link', unsupported)
    for content in ('FIRST', 'SECOND'):
        (local / 'part.gcode').write_text(content)
        assert archive(storage, mode='stub')
        version = Path(cache.get_file_metadata('part.gcode')['nas_path'])
        assert version.read_text() == content
        versions.append(version)
    assert len(set(versions)) == 2
    assert versions[0].read_text() == 'FIRST'


@pytest.mark.asyncio
@pytest.mark.parametrize('event', ['server:klippy_disconnect', 'server:klippy_shutdown',
                                  'server:klippy_ready', 'klippy_apis:cancel_requested',
                                  'cancelled'])
async def test_pending_start_invalidated_by_real_event(component, monkeypatch, event):
    entered, release = asyncio.Event(), asyncio.Event()

    async def fetch(filename):
        entered.set()
        await release.wait()
        return True

    monkeypatch.setattr(component, 'fetch_file', fetch)
    task = asyncio.create_task(component.remote_fetch('old.gcode', start_print=True))
    await entered.wait()
    try:
        if event == 'cancelled':
            await component._status_update({'print_stats': {'state': 'cancelled'}}, 0)
        else:
            assert event in component.server.events
            result = component.server.events[event]()
            if inspect.isawaitable(result):
                await result
        # New ready/standby must not revive an old request.
        await component._handle_klippy_ready()
    finally:
        release.set()
        result = await task
    assert not result['success']
    assert component.server.klippy.scripts == []


async def strict_dispatch(server, **kwargs):
    # Same boundary as pinned Moonraker KlippyConnection._execute_method:
    # callback result is awaited, only Exception is caught.
    try:
        result = server.remote_methods['nas_offload_fetch'](**kwargs)
        if result is not None:
            await result
    except Exception:
        logging.exception('Error running remote method')


@pytest.mark.asyncio
@pytest.mark.parametrize('external', [False, True])
async def test_remote_dispatch_distinguishes_lifecycle_and_caller_cancel(component, monkeypatch, external):
    entered = asyncio.Event()

    async def fetch(filename):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(component, 'fetch_file', fetch)
    task = asyncio.create_task(strict_dispatch(component.server, filename='part.gcode', start_print=True))
    await entered.wait()
    if external:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await component.close()
    else:
        await component.close()
        await task
        assert not task.cancelled()
    assert component.server.klippy.scripts == []


@pytest.mark.asyncio
async def test_stub_retry_after_direct_success_and_database_reopen(component, monkeypatch):
    component.offload_mode = 'stub'
    (component.local_gcodes_dir / 'part.gcode').write_text('PRINT')
    monkeypatch.setattr(file_ops, 'is_nas_available', lambda path: False)
    assert not await component.offload_file('part.gcode', 'complete')
    row = component.cache.get_pending_offloads()[0]
    monkeypatch.setattr(file_ops, 'is_nas_available', lambda path: True)
    assert await component.offload_file('part.gcode', 'complete')
    component.cache = MetadataCache(component.cache.db_path)
    assert component.cache.get_pending_offloads()[0]['id'] == row['id']
    component.nas_healthy = True
    await component._process_pending_offloads()
    assert component.cache.get_pending_offloads() == []


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['hybrid', 'symlink', 'stub'])
async def test_safe_stub_is_visible_in_list_and_response(component, monkeypatch, mode):
    component.offload_mode = mode
    monkeypatch.setattr(file_ops, 'is_nas_available', lambda path: True)
    (component.local_gcodes_dir / 'part.gcode').write_text('PRINT')
    result = await component._handle_offload_request(Request(filename='part.gcode'))
    assert result['success']
    assert result['effective_mode'] == 'stub'
    assert result['requested_mode'] == mode
    assert result['fallback'] is (mode != 'stub')
    if mode != 'stub':
        assert 'realpath' in result['fallback_reason']
    else:
        assert 'fallback_reason' not in result
    response = await component._handle_list_request(Request())
    assert response['total'] == 1
    item = response['files'][0]
    assert item['filename'] == 'part.gcode'
    assert item['effective_mode'] == 'stub'
    assert item['requested_mode'] == mode
    assert item['fallback'] is (mode != 'stub')
    if mode != 'stub':
        assert item['fallback_reason'] == result['fallback_reason']
    else:
        assert 'fallback_reason' not in item
    assert item['nas_available'] is True


@pytest.mark.parametrize('failure', ['offline', 'parent_fsync'])
def test_fresh_root_failures_keep_original_and_do_not_write_unmounted_tree(storage, monkeypatch, failure):
    local, mount, cache, locks = storage
    nas = mount / 'new-root'
    (local / 'part.gcode').write_text('PRINT')
    if failure == 'offline':
        monkeypatch.setattr(file_ops, 'is_nas_available', lambda path: False)
    else:
        fsync = os.fsync

        def fail_parent(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode) and Path('/proc/self/fd/%s' % fd).resolve() == mount:
                raise OSError(errno.EIO, 'parent durability failed')
            return fsync(fd)

        monkeypatch.setattr(os, 'fsync', fail_parent)
    assert not file_ops.offload('part.gcode', local, nas, cache, locks, 'complete',
                               nas_mount_point=mount)
    assert (local / 'part.gcode').read_text() == 'PRINT'
    assert not list(nas.glob('*.gcode'))
    if failure == 'offline':
        assert not nas.exists()


@pytest.mark.parametrize('nested_policy', [None, 'rw', 'rw,nosymfollow'])
def test_mount_policy_cannot_enable_nas_symlinks(storage, monkeypatch, nested_policy):
    local, nas, _, _ = storage
    import builtins
    real_open = builtins.open
    mountinfo = '1 0 0:1 / / rw - ext4 none rw\n'
    mountinfo += '2 1 0:2 / %s rw,nosymfollow - nfs server:/share rw\n' % nas
    if nested_policy:
        mountinfo += '3 2 0:3 / %s/nested %s - nfs server:/other rw\n' % (nas, nested_policy)

    def read_policy(path, *args, **kwargs):
        if str(path) == '/proc/self/mountinfo':
            return StringIO(mountinfo)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, 'open', read_policy)
    (local / 'part.gcode').write_text('PRINT')
    assert archive(storage, mode='hybrid')
    assert not (local / 'part.gcode').exists() and not (local / 'part.gcode').is_symlink()
    state = file_ops.get_offload_state('part.gcode', local, nas)
    assert state['requested_mode'] == 'hybrid' and state['effective_mode'] == 'stub'
    assert state['fallback'] and 'realpath' in state['fallback_reason']


@pytest.mark.parametrize('mode', ['hybrid', 'symlink', 'stub'])
def test_legacy_reference_is_converted_to_safe_stub_on_offload(storage, nas_mount_policy, mode):
    nas_mount_policy()
    local, nas, _, _ = storage
    (nas / 'legacy.gcode').write_text('PRINT')
    (local / 'part.gcode').symlink_to(nas / 'legacy.gcode')
    assert archive(storage, mode=mode)
    assert not (local / 'part.gcode').is_symlink()
    assert file_ops.read_stub_file(local / 'part.gcode.offloaded')['effective_mode'] == 'stub'
    assert restore(storage)
    assert (local / 'part.gcode').read_text() == 'PRINT'


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['fetch', 'offload'])
async def test_upload_lock_stays_held_until_cancelled_executor_finishes(component, module, monkeypatch, operation):
    entered, release = threading.Event(), threading.Event()
    upload_entered = asyncio.Event()

    def transfer(*args):
        entered.set()
        assert release.wait(3)
        return True

    async def upload():
        # Exact lock used by FileManager.finalize_upload and move/copy/delete.
        async with component.server.sync_lock:
            upload_entered.set()

    monkeypatch.setattr(module, operation, transfer)
    call = component.fetch_file('part.gcode') if operation == 'fetch' else component.offload_file('part.gcode', 'complete')
    task = asyncio.create_task(call)
    competing = None
    try:
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(.005)
        assert entered.is_set()
        competing = asyncio.create_task(upload())
        task.cancel()
        await asyncio.sleep(.02)
        assert not upload_entered.is_set()
        assert component.server.sync_lock.locked()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        if competing:
            await competing
    assert task.cancelled()
    assert upload_entered.is_set()


@pytest.mark.asyncio
async def test_retry_does_not_delete_newer_job_with_same_id(component, monkeypatch):
    component.nas_healthy = True
    component.cache.queue_offload('part.gcode', 'complete')

    async def transfer(*args, **kwargs):
        component.cache.queue_offload('part.gcode', 'complete')
        return True

    monkeypatch.setattr(component, 'offload_file', transfer)
    await component._process_pending_offloads()
    assert len(component.cache.get_pending_offloads()) == 1


def test_same_inode_content_update_with_restored_mtime_is_not_discarded(storage, monkeypatch):
    local, _, _, _ = storage
    target = local / 'part.gcode'
    target.write_bytes(b'OLD-PRINT')
    before = target.stat()
    rename = os.rename
    injected = []

    def update(src, dst, **kw):
        if src == 'part.gcode' and not injected:
            target.write_bytes(b'NEW-PRINT')
            os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
            injected.append(True)
        return rename(src, dst, **kw)

    monkeypatch.setattr(os, 'rename', update)
    assert not archive(storage, mode='stub')
    assert injected and recoverable(local, b'NEW-PRINT')


@pytest.mark.parametrize('mode', ['hybrid', 'symlink', 'stub'])
def test_successful_offload_releases_local_original_space(storage, nas_mount_policy, mode):
    local, _, _, _ = storage
    nas_mount_policy()
    (local / 'part.gcode').write_bytes(b'PRINT' * 2000)
    assert archive(storage, mode=mode)
    assert not list(local.glob('.kargo-recovery-*'))
    assert not recoverable(local, b'PRINT' * 2000)


@pytest.mark.parametrize('hardlinks', [True, False])
def test_missing_target_publication_never_overwrites_late_upload(storage, monkeypatch, hardlinks):
    local, nas, _, _ = storage
    (nas / 'part.gcode').write_text('ARCHIVE')
    link, opening = os.link, os.open
    injected = []

    def late_link(src, dst, **kwargs):
        if dst == 'part.gcode':
            if not hardlinks:
                raise OSError(errno.EOPNOTSUPP, 'unsupported')
            (local / 'part.gcode').write_text('NEW-UPLOAD')
            injected.append(True)
        return link(src, dst, **kwargs)

    def late_open(name, flags, *args, **kwargs):
        if name == 'part.gcode' and flags & os.O_EXCL:
            (local / 'part.gcode').write_text('NEW-UPLOAD')
            injected.append(True)
        return opening(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, 'link', late_link)
    monkeypatch.setattr(os, 'open', late_open)
    if hardlinks:
        assert not restore(storage)
    else:
        # Direct O_EXCL fallback belongs only to unpublished NAS archives.
        with file_ops.open_directory(local) as parent:
            source = os.open(nas / 'part.gcode', os.O_RDONLY)
            try:
                with pytest.raises(FileExistsError):
                    file_ops._copy_descriptor(source, parent, 'part.gcode', exclusive=True)
            finally:
                os.close(source)
    assert injected
    assert (local / 'part.gcode').read_text() == 'NEW-UPLOAD'


def test_hardlinkless_failed_fetch_does_not_leave_partial_ready_file(storage, monkeypatch):
    local, nas, _, _ = storage
    (nas / 'part.gcode').write_text('ARCHIVE')
    link, copy = os.link, file_ops.shutil.copyfileobj

    def unsupported(*args, **kwargs):
        raise OSError(errno.EOPNOTSUPP, 'unsupported')

    calls = []

    def fail_final(source, destination, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            destination.write(b'PARTIAL')
            destination.flush()
            raise OSError(errno.EIO, 'interrupted final reservation copy')
        return copy(source, destination, **kwargs)

    monkeypatch.setattr(os, 'link', unsupported)
    monkeypatch.setattr(file_ops.shutil, 'copyfileobj', fail_final)
    assert not restore(storage)
    assert not (local / 'part.gcode').is_file()


@pytest.mark.asyncio
async def test_cancel_between_dispatch_and_workflow_entry_invalidates_start(component, monkeypatch):
    start = component._start_task

    def cancel_before_entry(coroutine):
        task = start(coroutine)
        component._invalidate_print_start()
        return task

    async def fetch(filename):
        return True

    monkeypatch.setattr(component, '_start_task', cancel_before_entry)
    monkeypatch.setattr(component, 'fetch_file', fetch)
    result = await component.remote_fetch('old.gcode', start_print=True)
    assert not result['success']
    assert component.server.klippy.scripts == []


@pytest.mark.asyncio
async def test_external_cancellation_wins_even_when_close_cancels_workflow(component, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def fetch(filename):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
            return True

    monkeypatch.setattr(component, 'fetch_file', fetch)
    task = asyncio.create_task(component.remote_fetch('part.gcode', start_print=True))
    await entered.wait()
    closing = asyncio.create_task(component.close())
    await asyncio.sleep(0)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await closing
    assert component.server.klippy.scripts == []
