# Installation Guide

## Quick Start

```bash
cd ~
git clone https://github.com/Avatarsia/Kargo.git
cd Kargo
./install.sh
```

Follow the interactive prompts.
Run the installer as the unprivileged Moonraker service user, without `sudo`.
NAS write probes use a random exclusive file with no symlink following and clean up that file.
The NAS must permit writes for the service user's UID/GID; root write access is insufficient.

## Moonraker Compatibility

KARGO targets this API contract, checked against upstream commit
`b559b9a81161f958a43c2f527b6ad96f73310d5d`:

- `await klippy_apis.subscribe_objects(objects, callback=callback)` returns the initial status; callbacks receive `(status, eventtime)`.
- `await klippy_apis.run_gcode(script)` dispatches the original print command after fetching.
- `server.register_remote_method(name, callback)` accepts an async callback.
- `file_manager.sync_lock` is the async lock used by upload finalization and move/copy/delete operations. KARGO holds it until its executor transfer actually ends, including cancellation. A different Moonraker implementation without this contract fails the transfer safely.
- Events `klippy_apis:cancel_requested`, `server:klippy_disconnect`, `server:klippy_shutdown` and a new `server:klippy_ready` invalidate pending print starts; cancelled/error status and component close do too.

Sources: [FileManager upload lock](https://github.com/Arksine/moonraker/blob/b559b9a81161f958a43c2f527b6ad96f73310d5d/moonraker/components/file_manager/file_manager.py#L781-L803), [KlippyAPI](https://github.com/Arksine/moonraker/blob/b559b9a81161f958a43c2f527b6ad96f73310d5d/moonraker/components/klippy_apis.py),
[Server](https://github.com/Arksine/moonraker/blob/b559b9a81161f958a43c2f527b6ad96f73310d5d/moonraker/server.py).
This pins the required interface, not a minimum Moonraker release. Component tests use this contract;
a live Moonraker/Klipper/NAS integration test is still required on the target printer.

## Manual Installation

If you prefer manual installation:

### 1. Copy Component to Moonraker

```bash
# Adjust these paths to your installation and printer data root.
MOONRAKER_DIR="$HOME/moonraker/moonraker/components"
PRINTER_DATA_DIR="$HOME/printer_data"
mkdir -p "$PRINTER_DATA_DIR/systemd/nas-offload-locks" "$PRINTER_DATA_DIR/database"

# Copy component
cp scripts/nas_offload.py "$MOONRAKER_DIR/"
cp -r scripts/utils "$MOONRAKER_DIR/"
```

### 2. Copy Macros to Klipper

```bash
cp klipper_config/nas_offload.cfg "$PRINTER_DATA_DIR/config/"
```

### 3. Update Configurations

Add to `~/printer_data/config/printer.cfg`:
```ini
[include nas_offload.cfg]
```

Add to `~/printer_data/config/moonraker.conf`:
```ini
[nas_offload]
nas_mount_point: /mnt/nas
nas_gcodes_dir: /mnt/nas/klipper_gcodes
local_gcodes_dir: ~/printer_data/gcodes
cache_db: ~/printer_data/database/nas_offload.db
lock_dir: ~/printer_data/systemd/nas-offload-locks
offload_mode: hybrid
offload_on_complete: True
```

Use your actual data root in all three local paths, including installations using `klipper_data`
or a custom path. The interactive installer writes the detected root explicitly and creates
the lock and database directories as the invoking user. If `[nas_offload]` already exists,
update those paths manually; the installer preserves the existing section.
Without an explicit `lock_dir`, locks default to `local_gcodes_dir.parent/systemd/nas-offload-locks`.
See the [full example](../klipper_config/moonraker.conf.example) for optional settings.

### 4. Restart Services

```bash
sudo systemctl restart moonraker
sudo systemctl restart klipper
```

## Verification

Check Moonraker logs:
```bash
tail -f ~/printer_data/logs/moonraker.log | grep -i "nas"
```

Test API:
```bash
curl http://localhost:7125/server/nas_offload/status
```

## API and Macro Behavior

`SDCARD_PRINT_FILE` requests `nas_offload_fetch(filename=..., start_print=True)`.
Moonraker awaits the fetch before calling `_SDCARD_PRINT_FILE_ORIGINAL` through `klippy_apis.run_gcode`.
A failed fetch never starts printing. Install the matching component and macros together;
there is no fixed wait in the macro and no direct print command before transfer completion.
`NAS_FETCH FILE=...` requests a fetch without auto-starting; its console message confirms only the request.

`GET /server/nas_offload/list?offset=0&limit=100` retains the `files` response and adds pagination metadata.
`offset` defaults to 0; `limit` defaults to 100 and is capped at 1000.
Each page includes live `nas_available` values and loads cached metadata in one batch.
Filesystem scans, transfers, health checks and database work run outside the async event loop.

## Storage Safety Contract

KARGO checks the exact configured NAS mount before creating a missing `nas_gcodes_dir`.
Each traversed parent directory is synchronized before local data is released. A failed
mount/write/fsync check retains local data. This does not prevent an administrator from
subsequently unmounting/remounting the NAS or guarantee a server's crash durability.

`offload_mode` is the requested mode. `hybrid`, `symlink` and `stub` remain accepted,
but every new offload uses `effective_mode=stub`. This security compatibility change
also applies when the NAS mount and every nested mount have effective `nosymfollow`.
Moonraker's upload consumer resolves a local symlink with userspace `realpath`, then
moves the upload to the resolved path. A hostile NAS archive symlink can make that an
external host path; the final write no longer traverses the protected NAS mount.
Mount policy therefore cannot enable persistent NAS symlink publication. KARGO does
not patch consumers or issue mount/remount commands.

Stub mode leaves `<filename>.offloaded`, without a directly printable `<filename>`.
`SDCARD_PRINT_FILE` materializes it before printing; `NAS_FETCH` fetches without printing.
Direct `M23`/`M24` users must fetch first. The KARGO list includes stubs and legacy links,
with `requested_mode`, `effective_mode`, `fallback`, and a root-bounded live
`nas_available` check. A fallback includes `fallback_reason`. The offload response exposes
the same state. For requested `hybrid`/`symlink`, `fallback=true` and `fallback_reason`
explicitly identify the realpath consumer risk. Requested `stub` has `fallback=false`.
Archive bytes stay on the NAS until safe materialization; no full local copy is retained
after an ordinary successful offload.

When upgrading an installation with KARGO legacy NAS links:

1. Pause normal upload access and printing before exposing existing references to
   clients. Updating KARGO or listing files does not convert or secure untouched links.
2. Identify the KARGO NAS references. With a legitimate regular archive available,
   use KARGO fetch (`NAS_FETCH` or the fetch endpoint) to materialize a local regular file,
   or re-offload the validated legacy reference to convert it directly to a safe stub.
   Conversion applies to every requested mode, regardless of mount policy. Safe legacy
   fetch uses root-bounded no-follow reads and remains supported.
3. If the NAS is offline or the archive is malicious, a failed fetch/conversion leaves
   the legacy link in place. Keep normal upload access blocked for that path. Quarantine
   the known KARGO link itself, without following it, in a non-served directory outside
   all registered file roots; preserve its reference/cache information for later recovery.
   Resume normal uploads only after each affected link is converted, materialized or
   quarantined. Do not use a normal same-name upload as a migration workaround.

Unrelated local user symlinks are not automatically changed or migrated.

NAS writers may alter archive bytes, but a NAS symlink must not cause consumers to read
outside the host roots. Sources and parents are opened no-follow; configured symlink root
aliases and NAS-internal symlink source paths are rejected.

Copies keep their data FD open through publication. On NAS filesystems without hardlinks,
a unique version name is reserved with `O_EXCL|O_NOFOLLOW` and written through that FD.
Only after file/parent fsync and validation is it cached or locally referenced. Versioning
stays enabled. Reserved names are visible during writing; interruption can leave an
unreferenced incomplete NAS archive for manual inspection. Local complete-file publication
needs Linux procfs and hardlink support; it fails safely if these cannot publish the open FD.
Recovery restore uses `renameat2(RENAME_NOREPLACE)` and retains recovery data if unavailable.

Local replacements first move the actual displaced entry into a private
`.kargo-recovery-<id>/original` directory in the same parent. There may be a short missing-name
interval. A successful ordinary offload removes the verified archived original and frees
local space. A conflicting upload is restored without overwriting a newer name, or retained
in that recovery directory with its full path logged. Inspect conflicts before deleting them;
no automated cleanup removes conflict data. Non-cooperating writers holding an already-open
local write FD are outside the Moonraker upload-lock contract.

The global file-manager lock delays uploads/moves/deletes during NAS transfers. Cancellation
may wait for an already-running executor transfer; releasing the lock early would be unsafe.
A lifecycle-cancelled remote workflow returns failure without cancelling Moonraker's dispatcher;
external dispatcher cancellation still propagates. No new print start is sent after
invalidation; a command already submitted to Klipper cannot be recalled.

Retry rows carry a monotonically increasing `revision`; successful workers remove only the
revision they processed. Valid already-offloaded stubs are idempotent, including after database
reopen. Direct success leaves queue cleanup to the worker.
Pagination bounds the returned page; it does not eliminate the filesystem scan.

Run the [Linux/WSL regression tests](../tests/README.md) before deployment.

## NAS Setup

See guides in `docs/nas_setup/` for your specific NAS:
- [Synology DSM](nas_setup/synology.md)
- [Generic Linux](nas_setup/generic_linux.md)

## Troubleshooting

See [Troubleshooting Guide](troubleshooting.md)
