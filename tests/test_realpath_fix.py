"""CW-R1: NAS writers cannot redirect a same-name realpath upload to the host."""

import builtins
from io import StringIO
import os
from pathlib import Path
import shutil

import pytest

from scripts.utils import file_ops
from test_file_ops import storage, archive


@pytest.fixture
def positive_policy(storage, monkeypatch):
    _, nas, _, _ = storage
    mountinfo = ('1 0 0:1 / / rw - ext4 none rw\n'
                 '2 1 0:2 / %s rw,nosymfollow - nfs server:/share rw\n') % nas
    real_open = builtins.open

    def read_policy(path, *args, **kwargs):
        if str(path) == '/proc/self/mountinfo':
            return StringIO(mountinfo)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, 'open', read_policy)


@pytest.mark.parametrize('mode', ['hybrid', 'symlink', 'stub'])
def test_realpath_upload_cannot_follow_hostile_nas_leaf(storage, positive_policy, tmp_path, mode):
    local, _, cache, _ = storage
    target, host = local / 'part.gcode', tmp_path / 'host-original'
    target.write_bytes(b'OLD-PRINT')
    host.write_bytes(b'HOST-ORIGINAL')
    assert archive(storage, mode=mode)
    archived = Path(cache.get_file_metadata('part.gcode')['nas_path'])
    archived.unlink()
    archived.symlink_to(host)
    upload = tmp_path / 'upload.tmp'
    upload.write_bytes(b'NEW-UPLOAD')
    # Offline characterization of the consumer. The separate pinned contract
    # executes all four upstream methods, without adding network I/O to pytest.
    destination = os.path.realpath(str(target)) if target.is_symlink() else str(target)
    shutil.move(str(upload), destination)
    assert host.read_bytes() == b'HOST-ORIGINAL'
    assert destination == str(target)
    assert target.read_bytes() == b'NEW-UPLOAD' and not target.is_symlink()
    stub = file_ops.read_stub_file(local / 'part.gcode.offloaded')
    assert stub['requested_mode'] == mode and stub['effective_mode'] == 'stub'


def test_offload_preserves_unrelated_local_user_symlink(storage, tmp_path):
    local, _, _, _ = storage
    outside = tmp_path / 'user-file'
    outside.write_bytes(b'USER-ORIGINAL')
    target = local / 'part.gcode'
    target.symlink_to(outside)
    assert not archive(storage)
    assert target.is_symlink() and os.readlink(str(target)) == str(outside)
    assert outside.read_bytes() == b'USER-ORIGINAL'
    assert not (local / 'part.gcode.offloaded').exists()
