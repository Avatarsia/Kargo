# Synology DSM - NAS Setup Guide

This guide walks you through setting up NFS exports on Synology DSM for KARGO.

## Prerequisites

- Synology NAS with DSM 7.x
- Network connection between your Klipper host and NAS
- Admin access to DSM

## Step 1: Create Shared Folder

1. Open **Control Panel** → **Shared Folder**
2. Click **Create** → **Create shared folder**
3. Name: `klipper_gcodes` (or your preferred name)
4. Description: "Klipper G-code archive"
5. Click **Next** → **OK**
6. Skip encryption (unless you need it)
7. Click **Next** → **Apply**

## Step 2: Enable NFS Service

1. Open **Control Panel** → **File Services**
2. Go to **NFS** tab
3. Check **Enable NFS service**
4. Check **Enable NFSv4 support** (recommended)
5. Click **Apply**

## Step 3: Configure NFS Permissions

1. Go back to **Control Panel** → **Shared Folder**
2. Select `klipper_gcodes` folder
3. Click **Edit** → **NFS Permissions** tab
4. Click **Create**

Configure the NFS rule:
- **Server or IP address**: `192.168.1.100` (your Klipper host IP)
- **Privilege**: Read/Write
- **Squash**: Map all users to admin (or create specific user)
- **Security**: sys
- **Enable asynchronous**: ✓ (better performance)
- **Allow connections from non-privileged ports**: ✓
- **Allow users to access mounted subfolders**: ✓

5. Click **Save** → **OK**

## Step 4: Test from Klipper Host

SSH to your Klipper host and test the mount:

```bash
# Create mount point
sudo mkdir -p /mnt/nas

# Install NFS client
sudo apt-get update
sudo apt-get install -y nfs-common

# Test mount (replace with your NAS IP)
sudo mount -t nfs 192.168.1.50:/volume1/klipper_gcodes /mnt/nas

# Verify
ls -la /mnt/nas

# Test write
echo "test" | sudo tee /mnt/nas/test.txt
cat /mnt/nas/test.txt
sudo rm /mnt/nas/test.txt

# Unmount
sudo umount /mnt/nas
```

## Step 5: Configure Permanent Mount

Add to `/etc/fstab`:

```bash
sudo nano /etc/fstab
```

Add this line (replace IP and path):
```
192.168.1.50:/volume1/klipper_gcodes  /mnt/nas  nfs  nofail,x-systemd.automount,x-systemd.device-timeout=10s,rsize=8192,wsize=8192  0  0
```

Test fstab:
```bash
sudo systemctl daemon-reload
sudo mount -a
df -h | grep /mnt/nas
```

## Step 6: Install KARGO

Now you're ready to install KARGO:

```bash
cd ~
git clone https://github.com/yourusername/kargo.git
cd kargo
./install.sh
```

## Troubleshooting

### "Permission denied" when mounting
- Check NFS rule IP address matches your Klipper host
- Verify "Squash" setting (try "Map all users to admin")
- Ensure NFS service is enabled

### "No such device" error
- Install `nfs-common`: `sudo apt-get install nfs-common`

### Slow performance
- Enable "asynchronous" in NFS rule
- Increase `rsize`/`wsize` in fstab (try 16384 or 32768)
- Use wired connection instead of WiFi

### Connection timeouts
- Check firewall on NAS (allow NFS port 2049)
- Verify network connectivity: `ping 192.168.1.50`
- Check Synology logs: **Log Center** → **Connection**

## Advanced: Dedicated User

For better security, create a dedicated user:

1. **Control Panel** → **User & Group**
2. Create user: `klipper` 
3. Set password
4. Give read/write access to `klipper_gcodes` folder
5. In NFS rule, use "Squash: Map root to" → select `klipper` user

Then on Klipper host, mount with uid/gid:
```
192.168.1.50:/volume1/klipper_gcodes  /mnt/nas  nfs  uid=1000,gid=1000,nofail,x-systemd.automount  0  0
```
