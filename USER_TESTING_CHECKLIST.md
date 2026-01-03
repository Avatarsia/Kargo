# User Testing Checklist

## Pre-Test Setup

### Environment Requirements
- [ ] Klipper + Moonraker installed and running
- [ ] NAS with NFS configured and accessible
- [ ] Network connection between Klipper host and NAS
- [ ] Test G-code files prepared (small, medium, large)

### NAS Setup
- [ ] NFS export created on NAS
- [ ] Permissions configured (read/write)
- [ ] Mount point created on Klipper host: `/mnt/nas`
- [ ] NFS mount tested manually
- [ ] Added to `/etc/fstab` for persistent mount

---

## Installation Test

### Phase 1: Run Installer
```bash
cd /home/sven/Schreibtisch/klipper_nas_script
./install.sh
```

**Expected:**
- [ ] Menu displays correctly
- [ ] Colors show properly
- [ ] Can navigate menu

### Phase 2: Installation Process
Select option `1) Install`:

- [ ] Paths auto-detected correctly
- [ ] Prerequisites check passes
- [ ] NFS client detected/installed
- [ ] Configuration wizard completes
- [ ] NAS connection test succeeds
- [ ] Component files copied
- [ ] moonraker.conf updated
- [ ] Success message shown

### Phase 3: Service Restart
```bash
sudo systemctl restart moonraker
sudo systemctl restart klipper
```

### Phase 4: Verification
- [ ] Check Moonraker logs for "NAS Offload initialized"
- [ ] API responds: `curl http://localhost:7125/server/nas_offload/status`
- [ ] Klipper macro loads: Check in Mainsail console

---

## Functional Testing

### Test 1: First Print (No Offload Yet)

**Steps:**
1. Upload fresh G-code file to Mainsail
2. Start print
3. Wait for completion

**Verify:**
- [ ] Print starts normally (no FETCH needed)
- [ ] Print completes successfully
- [ ] After completion, file becomes symlink
- [ ] Stub file created (`.offloaded`)
- [ ] File exists on NAS
- [ ] API shows statistics updated

**Commands:**
```bash
# Check local file
ls -la ~/printer_data/gcodes/test.gcode
readlink ~/printer_data/gcodes/test.gcode

# Check stub
cat ~/printer_data/gcodes/test.gcode.offloaded

# Check NAS
ls -lh /mnt/nas/klipper_gcodes/test.gcode

# Check API
curl http://localhost:7125/server/nas_offload/list
```

### Test 2: Reprint (FETCH Required)

**Steps:**
1. Click "Reprint" on previously offloaded file
2. Observe console messages
3. Wait for print start

**Verify:**
- [ ] Console shows "NAS Offload: Checking..."
- [ ] Small delay (file being fetched)
- [ ] Print starts successfully
- [ ] During print, file is regular file
- [ ] After print, file is symlink again

### Test 3: Large File (>100MB)

**Steps:**
1. Upload large G-code (>100MB)
2. Print and complete
3. Reprint

**Verify:**
- [ ] Offload uses rsync (check logs)
- [ ] FETCH completes (may take longer)
- [ ] File integrity maintained
- [ ] No corruption

### Test 4: NAS Offline Resilience

**Steps:**
1. Ensure file is offloaded (symlink)
2. Unmount NAS: `sudo umount /mnt/nas`
3. Try to print

**Verify:**
- [ ] Error message shown
- [ ] Print does NOT start
- [ ] Clear error about NAS unavailable
- [ ] Stub file still exists

**Recovery:**
```bash
sudo mount /mnt/nas
# Retry print - should work now
```

### Test 5: Multiple Files

**Steps:**
1. Upload 5-10 different files
2. Print each once
3. Check all offloaded

**Verify:**
- [ ] All files become symlinks
- [ ] All files on NAS
- [ ] Folder structure preserved
- [ ] Statistics accurate

### Test 6: Cancelled Print

**Config:** Set `offload_on_cancelled: True` in moonraker.conf

**Steps:**
1. Start print
2. Cancel mid-print
3. Check if offloaded

**Verify:**
- [ ] File offloaded (if configured)
- [ ] Or kept local (if not configured)

---

## Performance Testing

### Measure FETCH Time

```bash
# In Moonraker logs, find timing:
grep "FETCH" ~/printer_data/logs/moonraker.log

# Or use manual API call:
time curl -X POST http://localhost:7125/server/nas_offload/fetch \
  -H "Content-Type: application/json" \
  -d '{"filename": "large_file.gcode"}'
```

**Record:**
- 10MB file: _____ seconds
- 100MB file: _____ seconds
- 500MB file: _____ seconds

**Acceptable:**
- <5s for 10MB
- <15s for 100MB
- <60s for 500MB

### Network Load

During print:
```bash
# Monitor network
iftop -i eth0

# Should not see heavy traffic (only during FETCH)
```

---

## Error Testing

### Test: Permissions Error

```bash
# Remove write permission
sudo chmod 555 /mnt/nas/klipper_gcodes/
# Try to offload - should queue for retry
# Restore permission
sudo chmod 755 /mnt/nas/klipper_gcodes/
```

### Test: Disk Full

Mock with quota if possible, or fill NAS to near-full.

**Expected:** Offload fails gracefully, file kept local.

### Test: Concurrent Operations

- Start print
- While printing, manually offload another file
- Both should complete without conflict

---

## Edge Cases

### Unicode Filenames

- [ ] Upload file with German umlauts: `test_ä_ö_ü.gcode`
- [ ] Print and offload
- [ ] Reprint

### Long Paths

- [ ] Create nested folders: `project/version/parts/subfolder/test.gcode`
- [ ] Verify structure preserved on NAS

### Rapid Fire Prints

- [ ] Print same file 3x in quick succession
- [ ] Each should fetch, print, offload

---

## Final Verification

### System Health
```bash
# API health check
curl http://localhost:7125/server/nas_offload/health

# Database integrity
sqlite3 ~/printer_data/database/nas_offload.db ".integrity_check"

# Queue should be empty
curl http://localhost:7125/server/nas_offload/status | grep queue_size
```

### Documentation Accuracy

- [ ] README installation steps work
- [ ] Troubleshooting guide helps with any issues
- [ ] NAS setup guide was sufficient

---

## Issues Encountered

**Log any issues here:**

1. Issue: ________________________________
   - Severity: Critical / Major / Minor
   - Workaround: ___________________________
   - Fix needed: ___________________________

2. Issue: ________________________________
   ...

---

## Sign-Off

**Tester:** ________________  
**Date:** ________________  
**Result:** ☐ PASS  ☐ PASS with Issues  ☐ FAIL

**Notes:**
