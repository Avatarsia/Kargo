# File Versioning Feature

## Problem

When uploading files with the same name (e.g., Fusion360's default "körper1.gcode"), KARGO would overwrite previous versions on the NAS, causing **permanent data loss**.

## Solution: Timestamp-Based Versioning

KARGO now automatically appends timestamps to filenames when offloading to NAS, preventing overwrites.

### Example

**Local filename:** `körper1.gcode`

**NAS versions:**
```
körper1_20260103_220155.gcode  (Version 1)
körper1_20260103_235430.gcode  (Version 2)
körper1_20260104_102210.gcode  (Version 3)
```

**Symlink on Klipper host points to latest:**
```
~/printer_data/gcodes/körper1.gcode -> /mnt/nas/.../körper1_20260104_102210.gcode
```

## Configuration

Add to `moonraker.conf`:

```ini
[nas_offload]
# Enable versioning (recommended)
enable_versioning: True

# Keep last N versions, older ones auto-deleted (0 = unlimited)
keep_versions: 5
```

### Options

| Setting | Default | Description |
|---------|---------|-------------|
| `enable_versioning` | `True` | Append timestamp to prevent overwrites |
| `keep_versions` | `5` | Number of versions to keep (0 = unlimited) |

## Behavior

### With Versioning Enabled (Default)

1. **First print of "test.gcode":**
   - Offload → `/mnt/nas/test_20260103_220155.gcode`
   - Symlink: `test.gcode` → NAS version

2. **Upload new "test.gcode" and print:**
   - Offload → `/mnt/nas/test_20260103_235430.gcode`
   - Symlink: `test.gcode` → new NAS version
   - **Old version kept!**

3. **After 6th print (keep_versions=5):**
   - Oldest version auto-deleted
   - Keeps last 5 versions only

### Without Versioning

Set `enable_versioning: False`:
- Overwrites previous version (⚠️ data loss risk)
- Use only if you manage versions externally

## File Naming

Timestamp format: `YYYYMMDD_HHMMSS`

**Examples:**
```
test.gcode          -> test_20260103_220155.gcode
benchy.gcode.gz     -> benchy_20260103_220155.gcode.gz
part_v2.ufp         -> part_v2_20260103_220155.ufp
```

## Benefits

✅ **No Data Loss** - All versions preserved  
✅ **Automatic Cleanup** - Old versions removed  
✅ **Transparent** - Symlink always points to latest  
✅ **Works with all file types** - .gcode, .gcode.gz, .ufp, etc.

## Notes

- Versions sorted by modification time (newest first)
- Cleanup runs after each successful offload
- Failed offloads don't create versions
- Disable if using external version control
