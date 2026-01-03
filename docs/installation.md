# Installation Guide

## Quick Start

```bash
cd ~
git clone https://github.com/yourusername/kargo.git
cd kargo
./install.sh
```

Follow the interactive prompts.

## Manual Installation

If you prefer manual installation:

### 1. Copy Component to Moonraker

```bash
# Find Moonraker components directory
MOONRAKER_DIR=$(python3 -c "import moonraker; print(moonraker.__path__[0])")/components

# Or typical location:
MOON RAKER_DIR=~/moonraker/moonraker/components

# Copy component
cp -r scripts/* $MOONRAKER_DIR/
```

### 2. Copy Macros to Klipper

```bash
cp klipper_config/nas_offload.cfg ~/printer_data/config/
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
offload_mode: hybrid
offload_on_complete: True
```

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

## NAS Setup

See guides in `docs/nas_setup/` for your specific NAS:
- [Synology DSM](nas_setup/synology.md)
- [QNAP](nas_setup/qnap.md)
- [TrueNAS](nas_setup/truenas.md)
- [Generic Linux](nas_setup/generic_linux.md)

## Troubleshooting

See [Troubleshooting Guide](troubleshooting.md)
