"""Descriptor-relative file operations for FETCH and OFFLOAD on Linux/POSIX."""

import os
import ctypes
import errno
import shutil
import fcntl
import json
import logging
import hashlib
import re
import secrets
import stat
from pathlib import Path
from typing import Optional, Dict, Any
from contextlib import contextmanager
from datetime import datetime

from .path_utils import (sanitize_path, open_directory, rooted_parent,
                         relative_to_root, ALLOWED_EXTENSIONS)
from .metadata_cache import MetadataCache


logger = logging.getLogger(__name__)
STUB_FALLBACK_REASON = 'NAS symlinks disabled: realpath consumers can write outside host roots'


class FileOperationError(Exception):
    """Raised when file operation fails."""
    pass


def _open_regular(parent_fd, name):
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=parent_fd)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise FileOperationError('Source is not a regular file')
    return descriptor


def _temporary(parent_fd):
    for _ in range(10):
        name = '.kargo-' + secrets.token_hex(16) + '.tmp'
        try:
            descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL |
                                 os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
            return name, descriptor
        except FileExistsError:
            continue
    raise FileOperationError('Could not allocate an exclusive temporary file')


def _unlink(parent_fd, name):
    if name is None:
        return
    try:
        os.unlink(name, dir_fd=parent_fd)
    except FileNotFoundError:
        pass


def _same_file(first, second):
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _check_source(parent_fd, name, expected):
    current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if (not _same_file(expected, current) or
            (expected.st_size, expected.st_mtime_ns, expected.st_ctime_ns) !=
            (current.st_size, current.st_mtime_ns, current.st_ctime_ns)):
        raise FileOperationError('Local source changed during offload')


def _check_copy(parent_fd, name, expected):
    current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if (not stat.S_ISREG(current.st_mode) or not _same_file(current, expected) or
            (current.st_size, current.st_mtime_ns) !=
            (expected.st_size, expected.st_mtime_ns)):
        raise FileOperationError('Copied file entry changed during transfer')


def _check_parent(root, relative, parent_fd):
    """Fail closed if a pinned parent has been replaced since traversal."""
    with rooted_parent(root, relative) as (current_fd, _):
        if not _same_file(os.fstat(current_fd), os.fstat(parent_fd)):
            raise FileOperationError('Parent changed during transfer')


@contextmanager
def file_lock(lock_path: Path, timeout: int = 30):
    """Acquire a non-blocking advisory lock without following lock symlinks."""
    with open_directory(Path(lock_path).parent, create=True) as parent_fd:
        descriptor = os.open(Path(lock_path).name, os.O_WRONLY | os.O_CREAT |
                             os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent_fd)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise FileOperationError('Lock is not a regular file')
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise FileOperationError('Operation already in progress') from exc
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _rename_noreplace(source_fd, source, target_fd, target):
    """Linux renameat2; never overwrite a concurrently created destination."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise FileOperationError('renameat2 unavailable; recovery entry retained')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                       ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(source_fd, os.fsencode(source), target_fd, os.fsencode(target), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def _capture(parent_fd, name):
    """Atomically preserve the entry actually displaced, including late uploads.

    Recovery lives on the trusted local filesystem for local commits. A NAS
    target can also use this operation: no captured pathname is opened to copy.
    """
    directory = '.kargo-recovery-' + secrets.token_hex(16)
    os.mkdir(directory, mode=0o700, dir_fd=parent_fd)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=parent_fd)
    try:
        os.rename(name, 'original', src_dir_fd=parent_fd, dst_dir_fd=descriptor)
        os.fsync(descriptor)
        os.fsync(parent_fd)
        return directory, descriptor
    except BaseException:
        # A failed fsync may follow a successful move. Preserve that inode.
        if _entry(descriptor, 'original') is not None:
            try:
                _rename_noreplace(descriptor, 'original', parent_fd, name)
            except OSError:
                logger.error('Recovery retained at %s/%s/original',
                             os.readlink('/proc/self/fd/%s' % parent_fd), directory)
        os.close(descriptor)
        try:
            os.rmdir(directory, dir_fd=parent_fd)
        except OSError:
            pass
        raise


def _release_capture(parent_fd, name, captured, discard=False):
    directory, descriptor = captured
    try:
        if discard:
            os.unlink('original', dir_fd=descriptor)
        else:
            try:
                _rename_noreplace(descriptor, 'original', parent_fd, name)
            except OSError:
                logger.error('Concurrent entry preserved at %s/%s/original',
                             os.readlink('/proc/self/fd/%s' % parent_fd), directory)
        os.fsync(descriptor)
        os.fsync(parent_fd)
    finally:
        os.close(descriptor)
        try:
            os.rmdir(directory, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except OSError:
            pass


def _unchanged_after_move(expected, actual):
    # rename changes ctime. Content changes are still checked by mtime/size;
    # offload also compares the captured bytes with the completed archive.
    return (_same_file(expected, actual) and expected.st_mode == actual.st_mode and
            (expected.st_size, expected.st_mtime_ns) ==
            (actual.st_size, actual.st_mtime_ns))


def _copy_descriptor(source_fd, parent_fd, name, verify=True, exclusive=False,
                     before_commit=None, allow_direct=False):
    """Publish an open regular inode, never a re-opened temporary pathname.

    Replacements first preserve the displaced entry, then publish no-clobber.
    A short missing-name interval replaces unsafe overwrite semantics. On
    hardlinkless NAS filesystems the final name is reserved O_EXCL and streamed via
    its FD; callers must not reference it until this function has completed.
    """
    expected = None if exclusive else _entry(parent_fd, name)
    temporary, descriptor = _temporary(parent_fd)
    captured = None
    committed = False
    try:
        before = os.fstat(source_fd)
        with os.fdopen(os.dup(descriptor), 'wb') as destination:
            with os.fdopen(os.dup(source_fd), 'rb') as source:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
            destination.flush()
        after = os.fstat(source_fd)
        if verify and (os.fstat(descriptor).st_size != before.st_size or
                       (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                       (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise FileOperationError('Source changed or copied size mismatched')
        os.fchmod(descriptor, stat.S_IMODE(before.st_mode) & 0o777)
        os.utime(descriptor, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.fsync(descriptor)
        copied_info = os.fstat(descriptor)
        if before_commit is not None:
            before_commit()
        _check_copy(parent_fd, temporary, copied_info)
        if expected is not None:
            captured = _capture(parent_fd, name)
            actual = _entry(captured[1], 'original')
            if not _unchanged_after_move(expected, actual):
                raise FileOperationError('Destination changed; displaced data preserved')
        try:
            # linkat(AT_SYMLINK_FOLLOW) through procfs binds the source to the
            # still-open FD. No CAP_DAC_READ_SEARCH / AT_EMPTY_PATH required.
            os.link('/proc/self/fd/%s' % descriptor, name,
                    src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=True)
        except OSError as exc:
            if exc.errno not in (errno.EOPNOTSUPP, errno.ENOSYS, errno.EPERM,
                                 errno.EXDEV, errno.ENOENT):
                raise
            if not (exclusive or allow_direct):
                raise FileOperationError('Local filesystem cannot safely publish the complete FD') from exc
            final_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                               os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                with os.fdopen(os.dup(descriptor), 'rb') as source:
                    with os.fdopen(os.dup(final_fd), 'wb') as destination:
                        shutil.copyfileobj(source, destination, length=1024 * 1024)
                        destination.flush()
                os.fchmod(final_fd, stat.S_IMODE(before.st_mode) & 0o777)
                os.utime(final_fd, ns=(before.st_atime_ns, before.st_mtime_ns))
                os.fsync(final_fd)
                copied_info = os.fstat(final_fd)
                if copied_info.st_size != before.st_size:
                    raise FileOperationError('Reserved archive size mismatch')
                _check_copy(parent_fd, name, copied_info)
            finally:
                os.close(final_fd)
        _check_copy(parent_fd, name, copied_info)
        os.fsync(parent_fd)
        committed = True
        return copied_info
    finally:
        os.close(descriptor)
        _unlink(parent_fd, temporary)
        if captured is not None:
            _release_capture(parent_fd, name, captured, discard=committed)


def safe_copy(src: Path, dst: Path, verify: bool = True, use_rsync: bool = None) -> None:
    """Copy regular files with pinned, no-follow traversal and no-clobber publication.

    use_rsync remains accepted for API compatibility. A bounded-memory stream
    replaces rsync, which reopens pathname arguments and can follow swapped
    parents. Large files are streamed rather than loaded into memory.
    """
    src, dst = Path(src).absolute(), Path(dst).absolute()
    try:
        with open_directory(src.parent) as source_parent:
            source_fd = _open_regular(source_parent, src.name)
            try:
                with open_directory(dst.parent, create=True) as parent_fd:
                    _copy_descriptor(source_fd, parent_fd, dst.name, verify,
                                     before_commit=lambda: _check_parent(
                                         dst.parent, dst.name, parent_fd))
            finally:
                os.close(source_fd)
    except Exception as exc:
        raise FileOperationError('Copy failed: {}'.format(exc)) from exc


def _write_stub(parent_fd, name, metadata):
    data = {'type': 'klipper_nas_offload_placeholder',
            'created_at': datetime.now().isoformat(), **metadata}
    temporary, descriptor = _temporary(parent_fd)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as destination:
            descriptor = None
            json.dump(data, destination, indent=2)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        temporary = None
        os.fsync(parent_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        _unlink(parent_fd, temporary)


def create_stub_file(stub_path: Path, metadata: Dict[str, Any]) -> None:
    with open_directory(Path(stub_path).parent) as parent_fd:
        _write_stub(parent_fd, Path(stub_path).name, metadata)


def _read_stub(parent_fd, name):
    descriptor = _open_regular(parent_fd, name)
    with os.fdopen(descriptor, 'r', encoding='utf-8') as source:
        data = json.load(source)
    if isinstance(data, dict) and data.get('type') == 'klipper_nas_offload_placeholder':
        return data
    raise FileOperationError('Invalid offload stub')


def read_stub_file(stub_path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open_directory(Path(stub_path).parent) as parent_fd:
            return _read_stub(parent_fd, Path(stub_path).name)
    except Exception as exc:
        logger.warning('Failed to read stub file %s: %s', stub_path, exc)
        return None


def _split_filename(name):
    for suffix in sorted(ALLOWED_EXTENSIONS, key=len, reverse=True):
        if name.lower().endswith(suffix):
            return name[:-len(suffix)], name[-len(suffix):]
    path = Path(name)
    return path.stem, path.suffix


def _cleanup_versions(parent_fd, base_name, keep_versions, protected=None):
    if keep_versions <= 0:
        return
    stem, suffix = _split_filename(base_name)
    pattern = re.compile(re.escape(stem) + r'_(\d{8}_\d{6})(?:_(\d{6})_([0-9a-f]{16}))?' +
                         re.escape(suffix))
    versions = []
    for name in os.listdir(parent_fd):
        match = pattern.fullmatch(name)
        if match is None:
            continue
        try:
            archived_at = datetime.strptime(match.group(1), '%Y%m%d_%H%M%S')
        except ValueError:
            continue
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISREG(info.st_mode):
            versions.append((name == protected, archived_at, match.group(2) or '',
                             match.group(3) or '', name))
    versions.sort(reverse=True)
    for version in versions[keep_versions:]:
        os.unlink(version[-1], dir_fd=parent_fd)


def _cleanup_old_versions(base_path: Path, keep_versions: int) -> None:
    """Retain exact-base archives by encoded archive time, never file mtime."""
    try:
        with open_directory(Path(base_path).parent) as parent_fd:
            _cleanup_versions(parent_fd, Path(base_path).name, keep_versions)
    except Exception as exc:
        logger.warning('Failed to cleanup old versions: %s', exc)


def is_nas_available(mount_point: Path) -> bool:
    """Probe the specified mount using an exclusive, unpredictable test file."""
    if not os.path.ismount(str(mount_point)):
        return False
    try:
        with open_directory(mount_point) as parent_fd:
            name, descriptor = _temporary(parent_fd)
            try:
                os.write(descriptor, b'kargo-mount-probe')
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
                _unlink(parent_fd, name)
        return True
    except Exception as exc:
        logger.warning('NAS write test failed: %s', exc)
        return False


def _entry(parent_fd, name):
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def fetch(rel_path: str, local_dir: Path, nas_dir: Path,
          cache: MetadataCache, lock_dir: Path) -> bool:
    """Materialize a local file from a root-bounded NAS reference or cache."""
    rel_path = sanitize_path(rel_path)
    local_dir, nas_dir = Path(local_dir).absolute(), Path(nas_dir).absolute()
    lock_path = Path(lock_dir) / (hashlib.md5(rel_path.encode()).hexdigest() + '.lock')
    try:
        with file_lock(lock_path), rooted_parent(local_dir, rel_path, create=True) as (parent_fd, name):
            info = _entry(parent_fd, name)
            if info is not None and stat.S_ISREG(info.st_mode):
                descriptor = _open_regular(parent_fd, name)
                os.close(descriptor)
                cache.record_access(rel_path)
                return True
            stub = name + '.offloaded'
            if info is not None and stat.S_ISLNK(info.st_mode):
                reference = Path(os.readlink(name, dir_fd=parent_fd))
                if not reference.is_absolute():
                    reference = Path(os.path.abspath(str(local_dir / Path(rel_path).parent / reference)))
            elif info is not None:
                raise FileOperationError('Local reference is not a file or symlink')
            elif _entry(parent_fd, stub) is not None:
                reference = Path(_read_stub(parent_fd, stub).get('nas_path', nas_dir / rel_path))
            else:
                metadata = cache.get_file_metadata(rel_path)
                reference = Path(metadata['nas_path']) if metadata else nas_dir / rel_path
            nas_relative = relative_to_root(reference, nas_dir)
            with rooted_parent(nas_dir, nas_relative) as (nas_parent, nas_name):
                descriptor = _open_regular(nas_parent, nas_name)
                try:
                    _copy_descriptor(descriptor, parent_fd, name,
                                     before_commit=lambda: _check_parent(local_dir, rel_path, parent_fd))
                finally:
                    os.close(descriptor)
            _unlink(parent_fd, stub)
            cache.record_access(rel_path)
            return True
    except Exception as exc:
        logger.error('FETCH failed for %s: %s', rel_path, exc)
        return False


def get_offload_state(rel_path, local_dir, nas_dir):
    """Describe links and stubs without following an untrusted NAS reference."""
    result = {'nas_available': False}
    with rooted_parent(local_dir, rel_path) as (parent_fd, name):
        info = _entry(parent_fd, name)
        if info is not None and stat.S_ISLNK(info.st_mode):
            reference = Path(os.readlink(name, dir_fd=parent_fd))
            if not reference.is_absolute():
                reference = Path(os.path.abspath(str(Path(local_dir) / Path(rel_path).parent / reference)))
            mode = 'hybrid' if _entry(parent_fd, name + '.offloaded') is not None else 'symlink'
            result.update(requested_mode=mode, effective_mode=mode)
        elif info is None and _entry(parent_fd, name + '.offloaded') is not None:
            data = _read_stub(parent_fd, name + '.offloaded')
            reference = Path(data['nas_path'])
            result.update(requested_mode=data.get('requested_mode', 'stub'),
                          effective_mode='stub')
        else:
            return None
    result['nas_path'] = str(reference)
    result['fallback'] = result['requested_mode'] != result['effective_mode']
    if result['fallback']:
        result['fallback_reason'] = STUB_FALLBACK_REASON
    try:
        relative = relative_to_root(reference, nas_dir)
        with rooted_parent(nas_dir, relative) as (parent_fd, name):
            descriptor = _open_regular(parent_fd, name)
            os.close(descriptor)
        result['nas_available'] = True
    except (OSError, ValueError, FileOperationError):
        pass
    return result


def _digest(descriptor):
    digest = hashlib.sha256()
    offset = 0
    while True:
        data = os.pread(descriptor, 1024 * 1024, offset)
        if not data:
            return digest.digest()
        digest.update(data)
        offset += len(data)


def _mount_for(nas_dir, configured):
    if configured is not None:
        mount = Path(configured).absolute()
        if mount == Path('/'):
            raise FileOperationError('System root is not a NAS mount')
        nas_dir.relative_to(mount)
        return mount
    mount = nas_dir
    while mount != mount.parent:
        if os.path.ismount(str(mount)):
            return mount
        mount = mount.parent
    raise FileOperationError('No NAS mount found; refusing system root fallback')


def offload(rel_path: str, local_dir: Path, nas_dir: Path,
            cache: MetadataCache, lock_dir: Path, end_state: str,
            mode: str = 'hybrid', enable_versioning: bool = True,
            keep_versions: int = 5, nas_mount_point: Optional[Path] = None) -> bool:
    """Commit an archive before publishing a safe stub for every requested mode."""
    rel_path = sanitize_path(rel_path)
    local_dir, nas_dir = Path(local_dir).absolute(), Path(nas_dir).absolute()
    lock_path = Path(lock_dir) / (hashlib.md5(rel_path.encode()).hexdigest() + '.lock')
    try:
        if mode not in ('hybrid', 'symlink', 'stub'):
            raise FileOperationError('Invalid offload mode')
        with file_lock(lock_path), rooted_parent(local_dir, rel_path) as (parent_fd, name):
            info = _entry(parent_fd, name)
            if info is not None and stat.S_ISLNK(info.st_mode):
                reference = Path(os.readlink(name, dir_fd=parent_fd))
                if not reference.is_absolute():
                    reference = Path(os.path.abspath(str(local_dir / Path(rel_path).parent / reference)))
                nas_relative = relative_to_root(reference, nas_dir)
                with rooted_parent(nas_dir, nas_relative) as (nas_parent, nas_name):
                    descriptor = _open_regular(nas_parent, nas_name)
                    archived_size = os.fstat(descriptor).st_size
                    os.close(descriptor)
                _write_stub(parent_fd, name + '.offloaded', {
                    'nas_path': str(reference), 'size': archived_size,
                    'requested_mode': mode, 'effective_mode': 'stub',
                    'end_state': end_state})
                captured = _capture(parent_fd, name)
                unchanged = _unchanged_after_move(info, _entry(captured[1], 'original'))
                _release_capture(parent_fd, name, captured, discard=unchanged)
                if not unchanged:
                    raise FileOperationError('Legacy reference changed; data preserved')
                return True
            if info is None and _entry(parent_fd, name + '.offloaded') is not None:
                reference = Path(_read_stub(parent_fd, name + '.offloaded')['nas_path'])
                nas_relative = relative_to_root(reference, nas_dir)
                with rooted_parent(nas_dir, nas_relative) as (nas_parent, nas_name):
                    archived_fd = _open_regular(nas_parent, nas_name)
                    os.close(archived_fd)
                return True
            descriptor = _open_regular(parent_fd, name)
            try:
                source_info = os.fstat(descriptor)
                mount = _mount_for(nas_dir, nas_mount_point)
                if not is_nas_available(mount):
                    raise FileOperationError('NAS unavailable')
                with rooted_parent(nas_dir, rel_path, create=True) as (nas_parent, base_name):
                    if enable_versioning:
                        stem, suffix = _split_filename(base_name)
                        version = '{}_{}_{}{}'.format(stem, datetime.now().strftime('%Y%m%d_%H%M%S_%f'),
                                                     secrets.token_hex(8), suffix)
                    else:
                        version = base_name
                    archived_info = _copy_descriptor(
                        descriptor, nas_parent, version, exclusive=enable_versioning, allow_direct=True,
                        before_commit=lambda: _check_parent(nas_dir, rel_path, nas_parent))
                    archived_path = nas_dir / Path(rel_path).parent / version
                    size = os.fstat(descriptor).st_size
                    cache.update_file(rel_path, size, str(archived_path))
                    _check_parent(local_dir, rel_path, parent_fd)
                    _check_parent(nas_dir, rel_path, nas_parent)
                    _check_source(parent_fd, name, source_info)
                    _check_copy(nas_parent, version, archived_info)
                    if mode != 'stub':
                        logger.warning('Offload %s: %s -> stub; %s',
                                       rel_path, mode, STUB_FALLBACK_REASON)
                    # Publish the reference before moving any original: a
                    # failed stub publication must retain the local file.
                    _write_stub(parent_fd, name + '.offloaded', {
                        'nas_path': str(archived_path), 'size': size,
                        'offloaded_at': datetime.now().isoformat(), 'end_state': end_state,
                        'requested_mode': mode, 'effective_mode': 'stub'})
                    captured = None
                    committed = False
                    try:
                        _check_parent(local_dir, rel_path, parent_fd)
                        _check_copy(nas_parent, version, archived_info)
                        captured = _capture(parent_fd, name)
                        actual = _entry(captured[1], 'original')
                        if not _unchanged_after_move(source_info, actual):
                            raise FileOperationError('Local upload displaced; data preserved')
                        archived_fd = _open_regular(nas_parent, version)
                        try:
                            if _digest(descriptor) != _digest(archived_fd):
                                raise FileOperationError('Local content changed; data preserved')
                        finally:
                            os.close(archived_fd)
                        os.fsync(parent_fd)
                        committed = True
                    finally:
                        if captured is not None:
                            _release_capture(parent_fd, name, captured, discard=committed)
                    if enable_versioning:
                        try:
                            _cleanup_versions(nas_parent, base_name, keep_versions, protected=version)
                        except OSError as exc:
                            logger.warning('Archive committed; retention failed: %s', exc)
                    return True
            finally:
                os.close(descriptor)
    except Exception as exc:
        logger.error('OFFLOAD failed for %s: %s', rel_path, exc)
        cache.queue_offload(rel_path, end_state)
        return False
