# KARGO - Klipper Archive & Remote G-code Operations

Automatic G-code archiving solution for Klipper 3D printers that offloads files to NAS after print completion and seamlessly fetches them for reprints.

## Features

- ✅ **Automatic Offloading**: G-codes archived to NAS after successful prints
- ✅ **Seamless Reprints**: Auto-fetch from NAS when reprinting from history
- ✅ **File Versioning**: Timestamp-based versioning prevents accidental overwrites
- ✅ **Hybrid Resilience**: Symlink + stub system works even when NAS is offline
- ✅ **Multi-NAS Support**: Configure multiple NAS targets for redundancy
- ✅ **Smart Features**: Background pre-fetch, progress indicators, notifications
- ✅ **Easy Setup**: KIAUH-style interactive installer
- ✅ **Safe Operations**: File locking, atomic operations, path sanitization

## Quick Start

### Prerequisites

- Klipper + Moonraker installed
- NAS with NFS or SMB configured
- Python 3.7+

### Installation

```bash
cd ~
git clone https://github.com/yourusername/kargo.git
cd kargo
./kargo.sh        # Language selector (recommended)
```

**Or choose language directly:**
```bash
./install.sh      # English
./install_de.sh   # Deutsch
```

Follow the interactive installer prompts to configure your NAS.

## How It Works

1. **Slicer uploads G-code** → File stored on local eMMC/SD
2. **Print completes** → File automatically copied to NAS
3. **Local file replaced** → Symlink created pointing to NAS
4. **Reprint triggered** → Auto-fetch from NAS to local storage
5. **Print starts** → Works seamlessly, user sees no difference

## Configuration

Configuration is stored in `~/printer_data/config/moonraker.conf`:

```ini
[nas_offload]
nas_mount_point: /mnt/nas
nas_gcodes_dir: /mnt/nas/klipper_gcodes
offload_mode: hybrid
offload_on_complete: True
```

See [Configuration Reference](docs/configuration.md) for all options.

## NAS Setup Guides

- [Synology DSM](docs/nas_setup/synology.md)
- [QNAP QTS](docs/nas_setup/qnap.md)
- [TrueNAS](docs/nas_setup/truenas.md)
- [OpenMediaVault](docs/nas_setup/openmediavault.md)
- [Unraid](docs/nas_setup/unraid.md)
- [UGREEN NASync](docs/nas_setup/ugreen.md)
- [Generic Linux NFS](docs/nas_setup/generic_linux.md)

## Troubleshooting

See [Troubleshooting Guide](docs/troubleshooting.md)

## API Reference

See [API Documentation](docs/api_reference.md)

## License

MIT License - see [LICENSE](LICENSE)

## Contributing

Contributions welcome! Please open an issue or PR.

## Support

- [GitHub Issues](https://github.com/yourusername/kargo/issues)
- [Klipper Discord](https://discord.klipper3d.org)
