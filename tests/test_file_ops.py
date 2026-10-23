"""Linux filesystem regression tests for safe transfers and archive retention."""

import builtins
import os
from io import StringIO
from datetime import datetime
from pathlib import Path

import pytest

from scripts.utils import file_ops
from scripts.utils.metadata_cache import MetadataCache


@pytest.fixture
def storage(tmp_path, monkeypatch):
    local = tmp_path / 'local'
    nas = tmp_path / 'nas'
    local.mkdir()
    nas.mkdir()
    monkeypatch.setattr(file_ops, 'is_nas_available', lambda path: True)
    return local, nas, MetadataCache(tmp_path / 'cache.db'), tmp_path / 'locks'


@pytest.fixture
def nas_mount_policy(storage, monkeypatch):
    _, nas, _, _ = storage
    real_open = builtins.open

    def configure(flags='rw,nosymfollow'):
        mountinfo = ('1 0 0:1 / / rw - ext4 none rw\n'
                     '2 1 0:2 / %s %s - nfs server:/share rw\n') % (nas, flags)

        def read_policy(path, *args, **kwargs):
            if str(path) == '/proc/self/mountinfo':
                return StringIO(mountinfo)
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, 'open', read_policy)

    return configure


def archive(storage, name='part.gcode', **kwargs):
    local, nas, cache, locks = storage
    kwargs.setdefault('nas_mount_point', nas)
    return file_ops.offload(name, local, nas, cache, locks, 'complete', **kwargs)


def restore(storage, name='part.gcode'):
    return file_ops.fetch(name, *storage)


@pytest.mark.parametrize('use_rsync', [False, True, None])
def test_copy_does_not_follow_predictable_temp_symlink(tmp_path, use_rsync):
    source, target, victim = [tmp_path / name for name in ['src', 'dst', 'victim']]
    source.write_bytes(b'gcode')
    victim.write_bytes(b'ORIGINAL')
    (tmp_path / 'dst.tmp').symlink_to(victim)
    file_ops.safe_copy(source, target, use_rsync=use_rsync)
    assert victim.read_bytes() == b'ORIGINAL'
    assert target.read_bytes() == b'gcode'


def test_copy_rejects_source_symlink(tmp_path):
    victim = tmp_path / 'victim'
    victim.write_text('SECRET')
    source = tmp_path / 'source'
    source.symlink_to(victim)
    with pytest.raises(file_ops.FileOperationError):
        file_ops.safe_copy(source, tmp_path / 'destination')
    assert not (tmp_path / 'destination').exists()


@pytest.mark.parametrize('reference', ['nas', 'symlink', 'stub', 'cache'])
def test_fetch_rejects_outside_source(storage, tmp_path, reference):
    local, nas, cache, _ = storage
    secret = tmp_path / 'secret.gcode'
    secret.write_text('SECRET')
    if reference == 'nas':
        (nas / 'part.gcode').symlink_to(secret)
    elif reference == 'symlink':
        (local / 'part.gcode').symlink_to(secret)
    elif reference == 'stub':
        file_ops.create_stub_file(local / 'part.gcode.offloaded', {'nas_path': str(secret)})
    else:
        cache.update_file('part.gcode', 6, str(secret))
    assert restore(storage) is False
    assert not (local / 'part.gcode').is_file() or (local / 'part.gcode').is_symlink()
    assert secret.read_text() == 'SECRET'


@pytest.mark.parametrize('operation', ['fetch', 'offload'])
def test_rejects_local_parent_symlink(storage, tmp_path, operation):
    local, nas, _, _ = storage
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'part.gcode').write_text('ORIGINAL')
    (local / 'linked').symlink_to(outside, target_is_directory=True)
    (nas / 'linked').mkdir()
    (nas / 'linked/part.gcode').write_text('NAS')
    result = restore(storage, 'linked/part.gcode') if operation == 'fetch' else archive(storage, 'linked/part.gcode')
    assert result is False
    assert (outside / 'part.gcode').read_text() == 'ORIGINAL'
    assert not (outside / 'part.gcode').is_symlink()


@pytest.mark.parametrize('operation', ['fetch', 'offload'])
def test_rejects_nas_parent_symlink(storage, tmp_path, operation):
    local, nas, _, _ = storage
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'part.gcode').write_text('SECRET')
    (nas / 'linked').symlink_to(outside, target_is_directory=True)
    (local / 'linked').mkdir()
    if operation == 'offload':
        (local / 'linked/part.gcode').write_text('ORIGINAL')
    result = restore(storage, 'linked/part.gcode') if operation == 'fetch' else archive(storage, 'linked/part.gcode')
    assert result is False
    assert sorted(p.name for p in outside.iterdir()) == ['part.gcode']
    assert (outside / 'part.gcode').read_text() == 'SECRET'


def test_cache_only_recovery(storage):
    local, nas, cache, _ = storage
    source = nas / 'part_20250101_000000.gcode'
    source.write_text('ARCHIVE')
    cache.update_file('part.gcode', 7, str(source))
    assert restore(storage) is True
    assert (local / 'part.gcode').read_text() == 'ARCHIVE'
    assert not (local / 'part.gcode').is_symlink()


@pytest.mark.parametrize('mode', ['hybrid', 'symlink', 'stub'])
def test_modes_round_trip(storage, mode, nas_mount_policy):
    nas_mount_policy()
    local, _, cache, _ = storage
    original = local / 'part.gcode'
    original.write_text('PRINT')
    assert archive(storage, mode=mode) is True
    assert not original.exists() and not original.is_symlink()
    stub = file_ops.read_stub_file(original.with_name(original.name + '.offloaded'))
    assert stub['requested_mode'] == mode and stub['effective_mode'] == 'stub'
    assert Path(cache.get_file_metadata('part.gcode')['nas_path']).read_text() == 'PRINT'
    assert restore(storage) is True
    assert original.read_text() == 'PRINT'
    assert not original.is_symlink()
    assert not original.with_name(original.name + '.offloaded').exists()


@pytest.mark.parametrize('mode', ['hybrid', 'symlink', 'stub'])
def test_reference_publication_failure_retains_original(storage, monkeypatch, mode):
    local, _, cache, _ = storage
    original = local / 'part.gcode'
    original.write_text('ORIGINAL')
    replace = os.replace

    def fail(source, target, **kwargs):
        if target == 'part.gcode.offloaded':
            raise OSError('injected stub publication failure')
        return replace(source, target, **kwargs)

    monkeypatch.setattr(os, 'replace', fail)
    assert archive(storage, mode=mode) is False
    assert original.read_text() == 'ORIGINAL'
    assert not original.is_symlink()
    assert Path(cache.get_file_metadata('part.gcode')['nas_path']).read_text() == 'ORIGINAL'
    assert not list(local.glob('.kargo-*'))
    assert not (local / 'part.gcode.offloaded').exists()


def test_retention_protects_new_archive_with_old_mtime(storage, monkeypatch):
    local, nas, cache, _ = storage
    old = nas / 'part_20250101_000000.gcode'
    old.write_text('OLD')
    os.utime(old, (2000000000, 2000000000))
    (local / 'part.gcode').write_text('NEW')
    os.utime(local / 'part.gcode', (1, 1))
    assert archive(storage, keep_versions=1) is True
    current = Path(cache.get_file_metadata('part.gcode')['nas_path'])
    assert current.read_text() == 'NEW'
    assert not (local / 'part.gcode').exists()
    assert file_ops.read_stub_file(local / 'part.gcode.offloaded')['nas_path'] == str(current)
    assert not old.exists()
    assert restore(storage)
    assert (local / 'part.gcode').read_text() == 'NEW'


@pytest.mark.parametrize('name', ['part.gcode', 'part.extra.gcode', 'part.extra.gcode.gz'])
def test_cleanup_matches_exact_base_and_archive_time(tmp_path, name):
    suffix = '.gcode.gz' if name.endswith('.gz') else '.gcode'
    stem = name[:-len(suffix)]
    old = tmp_path / (stem + '_20240101_000000' + suffix)
    new = tmp_path / (stem + '_20250101_000000' + suffix)
    unrelated = [stem + '_other_20230101_000000' + suffix, stem + '_support' + suffix,
                 stem + '_20251399_999999' + suffix]
    for item in [old, new] + [tmp_path / entry for entry in unrelated]:
        item.write_text('ARCHIVE')
    os.utime(old, (2000000000, 2000000000))
    os.utime(new, (1, 1))
    file_ops._cleanup_old_versions(tmp_path / name, 1)
    assert not old.exists()
    assert new.exists()
    assert all((tmp_path / entry).exists() for entry in unrelated)


def test_same_second_offloads_never_overwrite(storage, monkeypatch):
    class FrozenTime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 1, 1, 12, 0, 0)
    monkeypatch.setattr(file_ops, 'datetime', FrozenTime)
    local, nas, _, _ = storage
    (local / 'part.gcode').write_text('FIRST')
    assert archive(storage) is True
    assert restore(storage) is True
    (local / 'part.gcode').write_text('SECOND')
    assert archive(storage) is True
    assert sorted(p.read_text() for p in nas.iterdir()) == ['FIRST', 'SECOND']


def test_explicit_mount_never_falls_back_to_root(storage, monkeypatch):
    local, nas, _, _ = storage
    (local / 'part.gcode').write_text('ORIGINAL')
    checked = []
    def unavailable(path):
        checked.append(path)
        return False
    monkeypatch.setattr(file_ops, 'is_nas_available', unavailable)
    assert archive(storage, nas_mount_point=nas) is False
    assert checked == [nas]
    assert (local / 'part.gcode').read_text() == 'ORIGINAL'


def test_implicit_mount_does_not_accept_system_root(storage, monkeypatch):
    local, nas, cache, locks = storage
    (local / 'part.gcode').write_text('ORIGINAL')
    monkeypatch.setattr(os.path, 'ismount', lambda path: str(path) == '/')
    assert file_ops.offload('part.gcode', local, nas, cache, locks, 'complete') is False
    assert (local / 'part.gcode').read_text() == 'ORIGINAL'


def test_mount_probe_leaves_predictable_symlink_untouched(tmp_path, monkeypatch):
    victim = tmp_path / 'victim'
    victim.write_text('ORIGINAL')
    probe = tmp_path / '.nas_offload_test'
    probe.symlink_to(victim)
    monkeypatch.setattr(os.path, 'ismount', lambda path: True)
    assert file_ops.is_nas_available(tmp_path) is True
    assert victim.read_text() == 'ORIGINAL'
    assert probe.is_symlink()


def test_copy_destination_parent_swap_does_not_touch_outside(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.write_text('COPY')
    target_dir = tmp_path / 'target'
    target_dir.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'destination').write_text('ORIGINAL')
    original_open = os.open
    original_file_open = builtins.open
    swapped = []
    def swap_on_temp(path, flags, *args, **kwargs):
        if flags & os.O_CREAT and not swapped:
            target_dir.rename(tmp_path / 'moved')
            target_dir.symlink_to(outside, target_is_directory=True)
            swapped.append(True)
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', swap_on_temp)
    def swap_on_file_open(path, mode='r', *args, **kwargs):
        if str(path).endswith('destination.tmp') and 'w' in mode and not swapped:
            target_dir.rename(tmp_path / 'moved')
            target_dir.symlink_to(outside, target_is_directory=True)
            swapped.append(True)
        return original_file_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(builtins, 'open', swap_on_file_open)
    try:
        file_ops.safe_copy(source, target_dir / 'destination')
    except file_ops.FileOperationError:
        pass
    assert swapped
    assert (outside / 'destination').read_text() == 'ORIGINAL'
    assert sorted(p.name for p in outside.iterdir()) == ['destination']


def test_source_modified_after_archive_keeps_new_local_content(storage, monkeypatch):
    local, _, cache, _ = storage
    original = local / 'part.gcode'
    original.write_text('ORIGINAL')
    update = cache.update_file
    def change_after_archive(*args, **kwargs):
        update(*args, **kwargs)
        original.write_text('MODIFIED')
    monkeypatch.setattr(cache, 'update_file', change_after_archive)
    assert archive(storage) is False
    assert not original.is_symlink()
    assert original.read_text() == 'MODIFIED'


def test_reference_temp_collision_does_not_remove_existing_entry(storage, monkeypatch):
    local, _, _, _ = storage
    (local / 'part.gcode').write_text('ORIGINAL')
    opening = os.open
    collided = []
    def collision(name, flags, *args, **kwargs):
        parent = kwargs.get('dir_fd')
        if (parent is not None and flags & os.O_EXCL and not collided and
                Path('/proc/self/fd/%s' % parent).resolve() == local):
            (local / name).write_text('UNRELATED')
            collided.append(local / name)
        return opening(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, 'open', collision)
    assert archive(storage)
    assert not (local / 'part.gcode').exists()
    assert file_ops.read_stub_file(local / 'part.gcode.offloaded')['effective_mode'] == 'stub'
    assert collided[0].read_text() == 'UNRELATED'


@pytest.mark.parametrize('mode', ['hybrid', 'symlink', 'stub'])
def test_stub_write_failure_retains_original(storage, monkeypatch, mode):
    local, _, _, _ = storage
    original = local / 'part.gcode'
    original.write_text('ORIGINAL')
    def fail(*args, **kwargs):
        raise OSError('injected JSON write failure')
    monkeypatch.setattr(file_ops.json, 'dump', fail)
    assert archive(storage, mode=mode) is False
    assert original.read_text() == 'ORIGINAL'
    assert not original.is_symlink()
    assert not list(local.glob('.kargo-*'))


@pytest.mark.parametrize('operation', ['fetch', 'offload'])
def test_nas_source_swap_to_symlink_never_reads_outside(storage, tmp_path, monkeypatch, operation):
    local, nas, _, _ = storage
    (nas / 'part.gcode').write_text('NAS')
    secret = tmp_path / 'secret.gcode'
    secret.write_text('SECRET')
    # Exercise both materialization and the already-offloaded shortcut.
    (local / 'part.gcode').symlink_to(nas / 'part.gcode')
    original_open = os.open
    swapped = []
    def swap_before_open(name, flags, *args, **kwargs):
        if name == 'part.gcode' and not flags & os.O_CREAT and not swapped:
            (nas / 'part.gcode').unlink()
            (nas / 'part.gcode').symlink_to(secret)
            swapped.append(True)
        return original_open(name, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', swap_before_open)
    result = restore(storage) if operation == 'fetch' else archive(storage)
    assert result is False
    assert swapped
    assert secret.read_text() == 'SECRET'
    assert (local / 'part.gcode').is_symlink()


@pytest.mark.parametrize('mode', ['hybrid', 'symlink', 'stub'])
def test_compressed_multidot_nested_round_trip(storage, mode):
    local, _, cache, _ = storage
    name = 'nested/part.extra.gcode.gz'
    (local / 'nested').mkdir()
    (local / name).write_bytes(b'compressed-data')
    assert archive(storage, name, mode=mode) is True
    actual = Path(cache.get_file_metadata(name)['nas_path'])
    assert actual.name.startswith('part.extra_')
    assert actual.name.endswith('.gcode.gz')
    assert '.gcode_' not in actual.name
    assert restore(storage, name) is True
    assert (local / name).read_bytes() == b'compressed-data'


def test_large_copy_streams_without_path_reopen(tmp_path):
    source = tmp_path / 'large.gcode'
    # More than the old rsync threshold; sparse input bounds test setup cost.
    with source.open('wb') as output:
        output.write(b'BEGIN')
        output.seek(101 * 1024 * 1024)
        output.write(b'END')
    target = tmp_path / 'copy.gcode'
    file_ops.safe_copy(source, target, use_rsync=True)
    assert target.stat().st_size == source.stat().st_size
    with target.open('rb') as result:
        assert result.read(5) == b'BEGIN'
        result.seek(-3, os.SEEK_END)
        assert result.read() == b'END'


@pytest.mark.parametrize('operation', ['fetch', 'offload'])
def test_nas_parent_swap_during_transfer_never_follows_replacement(storage, tmp_path, monkeypatch, operation):
    local, nas, _, _ = storage
    (local / 'nested').mkdir()
    (nas / 'nested').mkdir()
    (nas / 'nested/part.gcode').write_text('NAS')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'part.gcode').write_text('SECRET')
    if operation == 'offload':
        (local / 'nested/part.gcode').write_text('ORIGINAL')
    copy = file_ops.shutil.copyfileobj
    swapped = []
    def swap_during_copy(source, destination, **kwargs):
        (nas / 'nested').rename(nas / 'moved')
        (nas / 'nested').symlink_to(outside, target_is_directory=True)
        swapped.append(True)
        return copy(source, destination, **kwargs)
    monkeypatch.setattr(file_ops.shutil, 'copyfileobj', swap_during_copy)
    result = restore(storage, 'nested/part.gcode') if operation == 'fetch' else archive(storage, 'nested/part.gcode')
    assert swapped
    assert (outside / 'part.gcode').read_text() == 'SECRET'
    assert sorted(p.name for p in outside.iterdir()) == ['part.gcode']
    if operation == 'fetch':
        assert result is True
        assert (local / 'nested/part.gcode').read_text() == 'NAS'
    else:
        assert result is False
        assert (local / 'nested/part.gcode').read_text() == 'ORIGINAL'
        assert not (local / 'nested/part.gcode').is_symlink()


@pytest.mark.parametrize('reference', ['stub', 'cache'])
def test_root_bounded_reference_still_rejects_nas_symlink(storage, tmp_path, reference):
    local, nas, cache, _ = storage
    secret = tmp_path / 'secret.gcode'
    secret.write_text('SECRET')
    target = nas / 'archive.gcode'
    target.symlink_to(secret)
    if reference == 'stub':
        file_ops.create_stub_file(local / 'part.gcode.offloaded', {'nas_path': str(target)})
    else:
        cache.update_file('part.gcode', 6, str(target))
    assert restore(storage) is False
    assert not (local / 'part.gcode').exists()
    assert secret.read_text() == 'SECRET'


def test_relative_local_symlink_into_nas_is_supported(storage):
    local, nas, _, _ = storage
    (nas / 'part.gcode').write_text('NAS')
    (local / 'part.gcode').symlink_to('../nas/part.gcode')
    assert restore(storage) is True
    assert (local / 'part.gcode').read_text() == 'NAS'
    assert not (local / 'part.gcode').is_symlink()


def test_version_publication_failure_keeps_local_original(storage, monkeypatch):
    local, nas, _, _ = storage
    (local / 'part.gcode').write_text('ORIGINAL')
    def fail(*args, **kwargs):
        raise OSError('filesystem does not support hard links')
    monkeypatch.setattr(os, 'link', fail)
    assert archive(storage) is False
    assert (local / 'part.gcode').read_text() == 'ORIGINAL'
    assert not (local / 'part.gcode').is_symlink()
    assert not list(nas.iterdir())


def test_nonversioned_offload_atomically_replaces_nas_symlink_entry(storage, tmp_path):
    local, nas, _, _ = storage
    victim = tmp_path / 'victim'
    victim.write_text('ORIGINAL')
    (nas / 'part.gcode').symlink_to(victim)
    (local / 'part.gcode').write_text('NEW')
    assert archive(storage, enable_versioning=False) is True
    assert victim.read_text() == 'ORIGINAL'
    assert not (nas / 'part.gcode').is_symlink()
    assert (nas / 'part.gcode').read_text() == 'NEW'


def test_zero_retention_keeps_all_archives(tmp_path):
    versions = [tmp_path / ('part_' + stamp + '.gcode') for stamp in
                ['20240101_000000', '20250101_000000']]
    for version in versions:
        version.write_text('DATA')
    file_ops._cleanup_old_versions(tmp_path / 'part.gcode', 0)
    assert all(version.exists() for version in versions)


def test_archive_replaced_by_symlink_before_local_commit_keeps_original(storage, tmp_path, monkeypatch):
    local, _, cache, _ = storage
    (local / 'part.gcode').write_text('ORIGINAL')
    victim = tmp_path / 'victim'
    victim.write_text('SECRET')
    update = cache.update_file
    def replace_archive(*args, **kwargs):
        update(*args, **kwargs)
        archived = Path(args[2])
        archived.unlink()
        archived.symlink_to(victim)
    monkeypatch.setattr(cache, 'update_file', replace_archive)
    assert archive(storage) is False
    assert not (local / 'part.gcode').is_symlink()
    assert (local / 'part.gcode').read_text() == 'ORIGINAL'
    assert victim.read_text() == 'SECRET'


def test_copy_detects_temp_entry_replaced_by_symlink(tmp_path, monkeypatch):
    source, target, victim = [tmp_path / name for name in ['source', 'target', 'victim']]
    source.write_text('COPY')
    target.write_text('ORIGINAL')
    victim.write_text('SECRET')
    copy = file_ops.shutil.copyfileobj
    def replace_temp(source_file, destination, **kwargs):
        copy(source_file, destination, **kwargs)
        for temporary in tmp_path.glob('.kargo-*.tmp'):
            temporary.unlink()
            temporary.symlink_to(victim)
    monkeypatch.setattr(file_ops.shutil, 'copyfileobj', replace_temp)
    with pytest.raises(file_ops.FileOperationError):
        file_ops.safe_copy(source, target)
    assert not target.is_symlink()
    assert target.read_text() == 'ORIGINAL'
    assert victim.read_text() == 'SECRET'
