# Troubleshooting Guide

## Common Issues and Solutions

### Installation Issues

#### "Moonraker not found"
**Problem:** Installer can't find Moonraker directory

**Solution:**
```bash
# Find Moonraker manually
find ~ -name "moonraker.py" -type f 2>/dev/null

# Common locations:
# ~/moonraker/moonraker/
# ~/klipper_config/moonraker/
```

Enter the correct path when installer asks.

---

#### "Permission denied" when copying files
**Problem:** No write permission to Moonraker directory

**Solution:**
```bash
# Check ownership
ls -la ~/moonraker/moonraker/components/

# Fix ownership if needed
sudo chown -R $USER:$USER ~/moonraker/
```

---

### NAS Connection Issues

#### "NAS is NOT mounted"
**Problem:** Mount point exists but NAS not mounted

**Diagnosis:**
```bash
# Check if mounted
mountpoint /mnt/nas

# Check fstab
cat /etc/fstab | grep nas

# Try manual mount
sudo mount -a
```

**Solution:**
```bash
# Test NFS connection
showmount -e 192.168.1.50  # Replace with your NAS IP

# Check NFS service on NAS
# For Synology: Control Panel → File Services → NFS

# Verify fstab entry:
192.168.1.50:/volume1/klipper_gcodes  /mnt/nas  nfs  nofail,x-systemd.automount  0  0
```

---

#### "Write test failed"
**Problem:** Can read NAS but can't write

**Diagnosis:**
```bash
# Check permissions
ls -la /mnt/nas/

# Try to create file
touch /mnt/nas/test.txt
```

**Solution:**
- Check NFS export permissions on NAS
- Verify "Read/Write" privilege in NAS settings
- Check "Squash" setting (Map to admin or specific user)
- On Synology: Shared Folder → NFS Permissions → Check privilege

---

#### "Stale file handle"
**Problem:** `OSError [Errno 116] Stale file handle`

**Solution:**
```bash
# Unmount and remount
sudo umount /mnt/nas
sudo mount /mnt/nas

# If stuck, force unmount
sudo umount -f -l /mnt/nas
sudo mount /mnt/nas

# Restart NFS client
sudo systemctl restart nfs-common
```

---

### Runtime Issues

#### Print doesn't start - "FETCH failed"
**Problem:** File can't be fetched from NAS

**Diagnosis:**
```bash
# Check Moonraker logs
tail -f ~/printer_data/logs/moonraker.log | grep -i nas

# Check if file exists
ls -la ~/printer_data/gcodes/yourfile.gcode

# If symlink, check target
readlink ~/printer_data/gcodes/yourfile.gcode
ls -la /mnt/nas/klipper_gcodes/yourfile.gcode
```

**Solutions:**
1. Check NAS is mounted: `mountpoint /mnt/nas`
2. Check file permissions on NAS
3. Try manual fetch via API:
   ```bash
   curl -X POST http://localhost:7125/server/nas_offload/fetch \
     -H "Content-Type: application/json" \
     -d '{"filename": "yourfile.gcode"}'
   ```

---

#### Files not offloading after print
**Problem:** Files stay local, not archived to NAS

**Diagnosis:**
```bash
# Check configuration
curl http://localhost:7125/server/nas_offload/status

# Check Moonraker logs for errors
tail -f ~/printer_data/logs/moonraker.log | grep offload

# Check print_stats subscription
# Should see "Subscribed to print_stats" in logs
```

**Solutions:**
1. Verify `offload_on_complete: True` in moonraker.conf
2. Check NAS is writable
3. Check offload queue:
   ```bash
   # Queue should be empty if offload working
   sqlite3 ~/printer_data/database/nas_offload.db \
     "SELECT * FROM offload_queue;"
   ```

---

#### "File locked by another process"
**Problem:** FETCH/OFFLOAD fails with lock error

**Diagnosis:**
```bash
# Check for stuck locks
ls -la /var/run/klipper-nas-offload/

# Check running processes
ps aux | grep python | grep nas
```

**Solution:**
```bash
# Remove stale locks
sudo rm -f /var/run/klipper-nas-offload/*.lock

# Restart Moonraker
sudo systemctl restart moonraker
```

---

### Performance Issues

#### Slow FETCH (print start delayed)
**Problem:** Takes too long to fetch file from NAS

**Diagnosis:**
```bash
# Test network speed to NAS
iperf3 -c 192.168.1.50  # If NAS supports iperf3

# Test NFS read speed
time dd if=/mnt/nas/testfile of=/dev/null bs=1M count=100
```

**Solutions:**
1. Use wired Ethernet instead of WiFi
2. Increase NFS rsize/wsize in fstab:
   ```
   ...,rsize=32768,wsize=32768,...
   ```
3. Enable rsync for large files (auto-enabled for >100MB)
4. Check NAS isn't sleeping (spin up drives)

---

#### Slow OFFLOAD (delays next print)
**Problem:** Offload takes too long, blocks next operation

**Note:** Offload happens in background, shouldn't block. If it does:

**Solution:**
```bash
# Check async offload is working
tail -f ~/printer_data/logs/moonraker.log | grep "OFFLOAD"

# Should see offload starting, then print can continue

# If blocking, check Python asyncio isn't broken
python3 -c "import asyncio; print(asyncio.run(asyncio.sleep(0)))"
```

---

### API / Macro Issues

#### Macro doesn't call FETCH
**Problem:** SDCARD_PRINT_FILE override not working

**Diagnosis:**
```bash
# Check if macro is loaded
grep -A 10 "SDCARD_PRINT_FILE" ~/printer_data/config/nas_offload.cfg

# Check Klipper loaded the macro
# In Mainsail console: HELP
# Should see SDCARD_PRINT_FILE in list
```

**Solution:**
1. Ensure `[include nas_offload.cfg]` in printer.cfg
2. Restart Klipper: `sudo systemctl restart klipper`
3. Check for macro conflicts (other SDCARD_PRINT_FILE definitions)

---

#### "Remote method not found"
**Problem:** `action_call_remote_method("nas_offload_fetch")` fails

**Diagnosis:**
```bash
# Check Moonraker component loaded
curl http://localhost:7125/server/nas_offload/status

# Check Moonraker logs
grep "Remote method" ~/printer_data/logs/moonraker.log
```

**Solution:**
1. Restart Moonraker: `sudo systemctl restart moonraker`
2. Check component installed: `ls ~/moonraker/moonraker/components/nas_offload.py`
3. Check Python syntax errors in component

---

## Database Issues

#### Corrupt database
**Problem:** `database is locked` or corruption errors

**Solution:**
```bash
# Backup and reset database
mv ~/printer_data/database/nas_offload.db ~/printer_data/database/nas_offload.db.backup
sudo systemctl restart moonraker
# Database will be recreated automatically
```

---

## Getting Help

If none of these solutions work:

1. **Collect logs:**
   ```bash
   # Moonraker log
   tail -100 ~/printer_data/logs/moonraker.log > nas_offload_logs.txt
   
   # System log
   journalctl -u moonraker -n 100 >> nas_offload_logs.txt
   ```

2. **Check status:**
   ```bash
   curl http://localhost:7125/server/nas_offload/status > nas_status.json
   ```

3. **Open GitHub issue** with logs attached

4. **Klipper Discord** - #help channel

---

## Debug Mode

Enable verbose logging:

Edit `~/moonraker/moonraker/components/nas_offload.py`:
```python
# Change at top of file:
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Add this line
```

Restart Moonraker, then check logs for detailed debug output.
