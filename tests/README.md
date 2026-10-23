# Regression Tests

Run from the repository root on Linux or WSL as an unprivileged user:
```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r tests/requirements.txt

# Run all tests
.venv/bin/python -m pytest tests/ -v -p no:cacheprovider -W error

# Run installer and macro regressions
.venv/bin/python -m pytest tests/test_installation.py tests/test_macros.py -v -p no:cacheprovider

# Run with coverage
.venv/bin/python -m pytest tests/ --cov=scripts --cov-report=html

# Check the actual checked-out Linux scripts (LF line endings required)
bash -n install.sh
bash -n install_de.sh
bash -n kargo.sh
```

The centrally prepared review environment can instead run
`/home/lexan/.cache/kargo-fixes-venv/bin/python -m pytest tests/ -v -p no:cacheprovider`
from `/mnt/c/Users/3D Partner/.t3/worktrees/Kargo/feature-repo-review-fixes` in WSL Ubuntu.
Python 3.12 is used for these regression runs; they do not prove runtime compatibility with Python 3.7.

## Test Coverage

Current test files:
- `test_correction_wave.py` - Linux FD/publication interleavings, late uploads, mount-policy fallback, parent fsync order, fresh NAS roots, hardlinkless versioning, persisted retries and lifecycle/dispatcher cancellation
- `test_file_ops.py` / `test_path_safety.py` - No-follow filesystem operations and archive retention
- `test_nas_offload.py` - Strict Moonraker contract and executor/lifecycle integration
- `test_path_utils.py` - Path sanitization tests
- `test_metadata_cache.py` - SQLite cache tests
- `test_installation.py` - Extracted installer functions in a temporary Linux filesystem, symlink safety, service-user permissions, detected data root and component copy boundaries
- `test_macros.py` - Jinja2 compile/render with Klipper's exact delimiters, quoted filenames and fetch-before-print delegation

Installer tests never execute the top-level installer, mount a NAS, change `/etc/fstab`,
or restart services. They reject unexpected `sudo` calls; write-permission tests require
an unprivileged Linux user. Jinja2 is a test dependency only.

## TODO: Integration Tests

Integration tests require:
- Real NFS mount
- Moonraker instance
- Test G-code files

Local regression tests do not replace validation on the target Moonraker/Klipper installation.

Mount availability and nosymfollow snapshots are controlled in unit tests; no real
mount is created or changed. All requested modes must publish stubs, even with a positive
policy snapshot. Real Linux symlinks, ordinary reads and a realpath/move upload probe
check that hostile NAS leaves cannot redirect new references to external host files.
The saved `.superpowers/sdd/repo-review-fixes/verify-upstream-contract.py` additionally
executes the exact hash-checked pinned Moonraker upload methods. Its source cache/download
is separate from the normal offline pytest suite; neither probe proves a live NAS mount.
