# KARGO - Klipper Archive & Remote G-code Operations

Automatic G-code archiving solution for Klipper 3D printers that offloads files to NAS after print completion and seamlessly fetches them for reprints.

## Features

- ✅ **Automatic Offloading**: G-codes archived to NAS after successful prints
- ✅ **Seamless Reprints**: Auto-fetch from NAS when reprinting from history
- ✅ **File Versioning**: Timestamp-based versioning prevents accidental overwrites
- ✅ **Safe References**: All requested modes use safe stubs without persistent NAS symlinks
- ✅ **Multi-NAS Support**: Configure multiple NAS targets for redundancy
- ✅ **Smart Features**: Background pre-fetch, progress indicators, notifications
- ✅ **Easy Setup**: KIAUH-style interactive installer
- ✅ **Safe Operations**: Coordinated uploads, no-clobber publication and root-bounded file access

## Quick Start

### Prerequisites

- Klipper + Moonraker installed with the [required API contract](docs/installation.md#moonraker-compatibility)
- NAS with NFS or SMB configured
- Python 3.7+

### Installation

```bash
cd ~
git clone https://github.com/Avatarsia/Kargo.git
cd Kargo
./kargo.sh        # Language selector (recommended)
```

**Or choose language directly:**
```bash
./install.sh      # English
./install_de.sh   # Deutsch
```

Follow the interactive installer prompts to configure your NAS.
Run as the Moonraker service user, without `sudo`; the installer uses `sudo` only for system setup.

## How It Works

1. **Slicer uploads G-code** → File stored on local eMMC/SD
2. **Print completes** → File automatically copied to NAS
3. **Local space released** → Safe stub references the NAS archive
4. **Reprint triggered** → Auto-fetch from NAS to local storage
5. **Print starts** → Moonraker invokes the original print command only after a successful fetch; a failed fetch never starts the print

## Configuration

Configuration is stored in `~/printer_data/config/moonraker.conf`:

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

The installer uses the detected printer data root for local files, cache and locks.
The default lock directory is `local_gcodes_dir.parent/systemd/nas-offload-locks`.
See the [configuration example](klipper_config/moonraker.conf.example) and [installation guide](docs/installation.md).

Requested `hybrid` and `symlink` values remain accepted for compatibility, but always fall
back to `stub`, including on `nosymfollow` mounts. Moonraker's realpath upload consumer can
bypass that mount protection. The API/list expose `requested_mode`, `effective_mode=stub`,
`fallback` and its `fallback_reason`. NAS bytes remain offloaded; fetch before direct
`M23`/`M24` use. Existing legacy symlinks require conversion or quarantine before normal
uploads resume; upgrading alone does not secure them.
See the [storage safety contract](docs/installation.md#storage-safety-contract).

## NAS Setup Guides

- [Synology DSM](docs/nas_setup/synology.md)
- [Generic Linux NFS](docs/nas_setup/generic_linux.md)

## Troubleshooting

See [Troubleshooting Guide](docs/troubleshooting.md)

## API Reference

See [API and macro behavior](docs/installation.md#api-and-macro-behavior).
`GET /server/nas_offload/list?offset=0&limit=100` returns a page of `files`
with live `nas_available` checks and pagination metadata. The default limit is 100; the maximum is 1000.
`NAS_FETCH FILE=...` requests an asynchronous fetch without starting a print.

## Tests

See [test setup and commands](tests/README.md). Run on Linux or WSL; installer tests use temporary files and extracted functions only.

## License

MIT License - see [LICENSE](LICENSE)

## Contributing

Contributions welcome! Please open an issue or PR.

## Support

- [GitHub Issues](https://github.com/Avatarsia/Kargo/issues)
- [Klipper Discord](https://discord.klipper3d.org)
