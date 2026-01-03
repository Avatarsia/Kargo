# Generic Linux NFS Server Setup

This guide covers setting up an NFS server on any Linux system (Ubuntu, Debian, Raspberry Pi, etc.) to use with KARGO.

## On the NFS Server (NAS)

### 1. Install NFS Server

```bash
# Ubuntu/Debian/Raspberry Pi OS
sudo apt-get update
sudo apt-get install -y nfs-kernel-server
```

### 2. Create Share Directory

```bash
# Create directory for G-codes
sudo mkdir -p /srv/nfs/klipper_gcodes

# Set ownership (replace with your user)
sudo chown nobody:nogroup /srv/nfs/klipper_gcodes
sudo chmod 777 /srv/nfs/klipper_gcodes

# Or specific user:
# sudo chown pi:pi /srv/nfs/klipper_gcodes
# sudo chmod 755 /srv/nfs/klipper_gcodes
```

### 3. Configure NFS Exports

Edit `/etc/exports`:

```bash
sudo nano /etc/exports
```

Add export entry (replace `192.168.1.100` with your Klipper host IP):

```
/srv/nfs/klipper_gcodes 192.168.1.100(rw,sync,no_subtree_check,no_root_squash)
```

**Options explained:**
- `rw` - Read/write access
- `sync` - Write changes before responding (safer)
- `no_subtree_check` - Better performance
- `no_root_squash` - Don't map root to nobody (allows full access)

**For whole subnet:**
```
/srv/nfs/klipper_gcodes 192.168.1.0/24(rw,sync,no_subtree_check,no_root_squash)
```

**For single IP with squash:**
```
/srv/nfs/klipper_gcodes 192.168.1.100(rw,sync,no_subtree_check,all_squash,anonuid=1000,anongid=1000)
```

### 4. Apply Configuration

```bash
# Export the shares
sudo exportfs -arv

# Restart NFS server
sudo systemctl restart nfs-kernel-server

# Enable on boot
sudo systemctl enable nfs-kernel-server

# Check exports
sudo exportfs -v
showmount -e localhost
```

### 5. Configure Firewall (if enabled)

```bash
# UFW
sudo ufw allow from 192.168.1.100 to any port nfs

# Or for whole subnet
sudo ufw allow from 192.168.1.0/24 to any port nfs

# iptables
sudo iptables -A INPUT -p tcp -s 192.168.1.100 --dport 2049 -j ACCEPT
sudo iptables -A INPUT -p udp -s 192.168.1.100 --dport 2049 -j ACCEPT
```

---

## On the Klipper Host (Client)

### 1. Install NFS Client

```bash
sudo apt-get update
sudo apt-get install -y nfs-common
```

### 2. Create Mount Point

```bash
sudo mkdir -p /mnt/nas
```

### 3. Test Mount

```bash
# Replace 192.168.1.50 with your NFS server IP
sudo mount -t nfs 192.168.1.50:/srv/nfs/klipper_gcodes /mnt/nas

# Verify
df -h | grep /mnt/nas
ls -la /mnt/nas

# Test write
echo "test" | sudo tee /mnt/nas/test.txt
cat /mnt/nas/test.txt
sudo rm /mnt/nas/test.txt

# Unmount
sudo umount /mnt/nas
```

### 4. Configure Permanent Mount

Edit `/etc/fstab`:

```bash
sudo nano /etc/fstab
```

Add (replace IP):

```
192.168.1.50:/srv/nfs/klipper_gcodes  /mnt/nas  nfs  nofail,x-systemd.automount,x-systemd.device-timeout=10s,rsize=8192,wsize=8192  0  0
```

Test:

```bash
sudo systemctl daemon-reload
sudo mount -a
df -h | grep /mnt/nas
```

### 5. Install KARGO

```bash
cd ~
git clone https://github.com/yourusername/kargo.git
cd kargo
./install.sh
```

---

## Troubleshooting

### "Permission denied"

**Server side:**
```bash
# Check exports
sudo exportfs -v

# Check directory permissions
ls -la /srv/nfs/klipper_gcodes/

# Fix permissions
sudo chmod 777 /srv/nfs/klipper_gcodes
```

**Client side:**
```bash
# Try with different mount options
sudo mount -t nfs -o vers=4 192.168.1.50:/srv/nfs/klipper_gcodes /mnt/nas
```

### "Connection refused"

```bash
# Server: Check NFS is running
sudo systemctl status nfs-kernel-server

# Server: Check firewall
sudo ufw status
sudo iptables -L | grep nfs

# Client: Test connectivity
ping 192.168.1.50
showmount -e 192.168.1.50
```

### "Stale file handle"

```bash
# Server: Restart NFS
sudo systemctl restart nfs-kernel-server

# Client: Remount
sudo umount -f -l /mnt/nas
sudo mount /mnt/nas
```

---

## Performance Tuning

### Large rsize/wsize

For faster transfers:

```
# In /etc/fstab:
...,rsize=32768,wsize=32768,...

# Or even:
...,rsize=65536,wsize=65536,...
```

### NFSv4

Force NFSv4 for better performance:

```bash
# In fstab:
192.168.1.50:/srv/nfs/klipper_gcodes  /mnt/nas  nfs  vers=4,nofail,...
```

### Async vs Sync

**Server export:**
- `sync` - Safer, waits for writes to complete
- `async` - Faster, but risk of data loss on crash

For Klipper use case, `sync` is recommended.

---

## Security Considerations

### Use Specific User Mapping

On server:

```bash
# Create dedicated user
sudo useradd -M -s /bin/false klipper
id klipper  # Note UID and GID

# Set ownership
sudo chown klipper:klipper /srv/nfs/klipper_gcodes/
sudo chmod 755 /srv/nfs/klipper_gcodes/
```

In `/etc/exports`:
```
/srv/nfs/klipper_gcodes 192.168.1.100(rw,sync,no_subtree_check,all_squash,anonuid=1001,anongid=1001)
```

where `1001` is the UID/GID of the klipper user.

### Limit to Single IP

Always specify single IP instead of subnet when possible:

```
# Good (specific IP)
/srv/nfs/klipper_gcodes 192.168.1.100(...)

# Less secure (whole subnet)
/srv/nfs/klipper_gcodes 192.168.1.0/24(...)
```

---

## Advanced: NFS over VPN

For remote access via Wireguard/Tailscale:

```bash
# Server: Allow VPN subnet
# In /etc/exports (10.0.0.0/24 is VPN subnet):
/srv/nfs/klipper_gcodes 10.0.0.0/24(rw,sync,no_subtree_check)

# Client: Mount using VPN IP
10.0.0.1:/srv/nfs/klipper_gcodes /mnt/nas nfs ...
```
