#!/bin/bash
# KARGO - Klipper Archive & Remote G-code Operations
# Installation Script - KIAUH-style interactive installer (ENGLISH VERSION)

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOONRAKER_DIR=""
KLIPPER_CONFIG_DIR=""
PRINTER_DATA_DIR=""

# Helper functions
print_header() {
    echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║      KARGO Setup v1.0                  ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
    echo
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

# Detect paths
detect_paths() {
    print_info "Detecting installation paths..."
    
    # Try common locations
    if [ -d "$HOME/printer_data" ]; then
        PRINTER_DATA_DIR="$HOME/printer_data"
    elif [ -d "$HOME/klipper_data" ]; then
        PRINTER_DATA_DIR="$HOME/klipper_data"
    else
        print_warning "Could not auto-detect printer_data directory"
        read -p "Enter printer_data path [$HOME/printer_data]: " custom_path
        PRINTER_DATA_DIR="${custom_path:-$HOME/printer_data}"
    fi
    
    KLIPPER_CONFIG_DIR="$PRINTER_DATA_DIR/config"
    
    # Find Moonraker
    if [ -d "$HOME/moonraker/moonraker" ]; then
        MOONRAKER_DIR="$HOME/moonraker/moonraker"
    else
        print_warning "Could not find Moonraker directory"
        read -p "Enter Moonraker path [$HOME/moonraker/moonraker]: " custom_mr
        MOONRAKER_DIR="${custom_mr:-$HOME/moonraker/moonraker}"
    fi
    
    print_success "Paths detected:"
    echo "  printer_data: $PRINTER_DATA_DIR"
    echo "  Klipper config: $KLIPPER_CONFIG_DIR"
    echo "  Moonraker: $MOONRAKER_DIR"
    echo
}

# Check prerequisites
check_prerequisites() {
    print_info "Checking prerequisites..."
    
    local all_ok=true
    
    # Python 3
    if command -v python3 &> /dev/null; then
        local py_version=$(python3 --version | cut -d' ' -f2)
        print_success "Python 3: $py_version"
    else
        print_error "Python 3 not found"
        all_ok=false
    fi
    
    # Moonraker
    if [ -d "$MOONRAKER_DIR" ]; then
        print_success "Moonraker: $MOONRAKER_DIR"
    else
        print_error "Moonraker not found at $MOONRAKER_DIR"
        all_ok=false
    fi
    
    # Klipper config
    if [ -d "$KLIPPER_CONFIG_DIR" ]; then
        print_success "Klipper config: $KLIPPER_CONFIG_DIR"
    else
        print_error "Klipper config not found at $KLIPPER_CONFIG_DIR"
        all_ok=false
    fi
    
    # NFS client
    if command -v mount.nfs &> /dev/null; then
        print_success "NFS client installed"
    else
        print_warning "NFS client (nfs-common) not installed"
        read -p "Install nfs-common now? [Y/n]: " install_nfs
        if [[ "$install_nfs" != "n" ]]; then
            sudo apt-get update
            sudo apt-get install -y nfs-common
            print_success "NFS client installed"
        fi
    fi
    
    echo
    
    if [ "$all_ok" = false ]; then
        print_error "Prerequisites check failed"
        echo
        echo -e "${YELLOW}Please install missing components:${NC}"
        echo
        echo "Python 3:"
        echo "  sudo apt-get install python3"
        echo
        echo "NFS Client:"
        echo "  sudo apt-get install nfs-common"
        echo
        echo "Moonraker/Klipper:"
        echo "  See: https://docs.mainsail.xyz/setup"
        echo
        exit 1
    fi
}

# Validation helper functions
validate_ip() {
    local ip=$1
    if [[ $ip =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
        return 0
    fi
    return 1
}

validate_hostname() {
    local host=$1
    # Allow hostname or IP
    if validate_ip "$host" || [[ $host =~ ^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$ ]]; then
        return 0
    fi
    return 1
}

detect_nas_type() {
    local host=$1
    
    # Try to detect Synology
    if [[ $host =~ synology|diskstation ]]; then
        echo "synology"
        return
    fi
    
    # Try to detect QNAP
    if [[ $host =~ qnap ]]; then
        echo "qnap"
        return
    fi
    
    # Try to detect TrueNAS
    if [[ $host =~ truenas|freenas ]]; then
        echo "truenas"
        return
    fi
    
    echo "generic"
}

get_default_export_path() {
    local nas_type=$1
    
    case "$nas_type" in
        synology)
            echo "/volume1/klipper_gcodes"
            ;;
        qnap)
            echo "/share/klipper_gcodes"
            ;;
        truenas)
            echo "/mnt/tank/klipper_gcodes"
            ;;
        *)
            echo "/export/klipper_gcodes"
            ;;
    esac
}

# NAS Mount Setup
setup_nas_mount() {
    print_header
    echo -e "${BLUE}═══ NAS Mount Setup ═══${NC}"
    echo
    
    print_info "This wizard will automatically configure NFS mounting."
    echo
    
    # NAS Server details 
    echo -e "${BLUE}Step 1: NAS Server${NC}"
    echo "Examples:"
    echo "  - IP address: 192.168.1.50"
    echo "  - Hostname: nas.local or synology-nas"
    echo
    
    while true; do
        read -p "NAS IP address or hostname: " NAS_HOST
        
        if [ -z "$NAS_HOST" ]; then
            print_error "NAS host is required"
            continue
        fi
        
        # Validate input
        if ! validate_hostname "$NAS_HOST"; then
            print_error "Invalid IP address or hostname"
            echo "Example: 192.168.1.50 or nas.local"
            continue
        fi
        
        print_success "Host: $NAS_HOST"
        break
    done
    
    # Auto-detect NAS type
    NAS_TYPE=$(detect_nas_type "$NAS_HOST")
    DEFAULT_EXPORT=$(get_default_export_path "$NAS_TYPE")
    
    if [ "$NAS_TYPE" != "generic" ]; then
        print_info "NAS type detected: $NAS_TYPE"
    fi
    
    echo
    echo -e "${BLUE}Step 2: NFS Export Path${NC}"
    echo "This is the path ON the NAS (not local)."
    echo "Examples:"
    echo "  - Synology: /volume1/klipper_gcodes"
    echo "  - QNAP: /share/klipper_gcodes"
    echo "  - Generic Linux: /export/klipper_gcodes"
    echo
    read -p "NFS Export path [$DEFAULT_EXPORT]: " NAS_EXPORT
    NAS_EXPORT="${NAS_EXPORT:-$DEFAULT_EXPORT}"
    
    # Validate path format
    if [[ ! $NAS_EXPORT =~ ^/ ]]; then
        print_warning "Path should start with /, correcting..."
        NAS_EXPORT="/$NAS_EXPORT"
    fi
    
    echo
    echo -e "${BLUE}Step 3: Local Mount Point${NC}"
    echo "Where should the NAS be mounted on this system?"
    echo "Default: /mnt/nas (recommended)"
    echo
    read -p "Local mount point [/mnt/nas]: " NAS_MOUNT_LOCAL
    NAS_MOUNT_LOCAL="${NAS_MOUNT_LOCAL:-/mnt/nas}"
    
    # Validate mount point
    if [[ ! $NAS_MOUNT_LOCAL =~ ^/ ]]; then
        print_error "Mount point must be absolute path (starts with /)"
        return 1
    fi
    
    # Warn about unusual paths
    if [[ ! $NAS_MOUNT_LOCAL =~ ^/mnt/|^/media/ ]]; then
        print_warning "Unusual mount point. Standard is /mnt/nas"
        read -p "Continue anyway? [y/N]: " confirm_path
        if [[ "$confirm_path" != "y" ]]; then
            return 1
        fi
    fi
    
    echo
    print_success "NFS Mount Configuration:"
    echo "  Server: $NAS_HOST"
    echo "  Export: $NAS_EXPORT"
    echo "  Mount point: $NAS_MOUNT_LOCAL"
    echo
    
    read -p "Continue with this configuration? [Y/n]: " confirm
    if [[ "$confirm" == "n" ]]; then
        return 1
    fi
    
    # Test NFS connectivity first
    print_info "Testing NFS server reachability..."
    if ! ping -c 1 -W 2 "$NAS_HOST" &>/dev/null; then
        print_warning "NAS host not reachable via ping"
        print_info "Possible causes:"
        echo "  - NAS is offline"
        echo "  - Firewall blocks ICMP"
        echo "  - Wrong IP/hostname"
        read -p "Continue anyway? [y/N]: " continue_anyway
        if [[ "$continue_anyway" != "y" ]]; then
            return 1
        fi
    else
        print_success "NAS host reachable ($NAS_HOST)"
    fi
    
    # Check if showmount works
    if command -v showmount &>/dev/null; then
        print_info "Checking NFS exports..."
        if showmount -e "$NAS_HOST" &>/dev/null; then
            print_success "NFS server responds"
            echo
            showmount -e "$NAS_HOST"
            echo
        else
            print_warning "Could not query NFS exports"
        fi
    fi
    
    # Create mount point
    print_info "Creating mount point $NAS_MOUNT_LOCAL..."
    if [ ! -d "$NAS_MOUNT_LOCAL" ]; then
        sudo mkdir -p "$NAS_MOUNT_LOCAL"
        print_success "Mount point created"
    else
        print_info "Mount point already exists"
    fi
    
    # Test manual mount first
    print_info "Testing manual mount..."
    if sudo mount -t nfs "${NAS_HOST}:${NAS_EXPORT}" "$NAS_MOUNT_LOCAL" 2>/dev/null; then
        print_success "Manual mount successful!"
        
        # Test write permission
        if test_nas_write "$NAS_MOUNT_LOCAL"; then
            print_success "Write permissions OK"
        else
            print_error "No write permissions on NAS!"
            echo
            echo -e "${YELLOW}Problem:${NC} Cannot write to NAS."
            echo
            echo -e "${BLUE}Solutions:${NC}"
            echo "1. Check NFS export permissions on NAS:"
            echo "   - Synology: Control Panel → Shared Folders → NFS Permissions"
            echo "   - Ensure: Read/Write enabled"
            echo "   - Client IP allowed: ${NAS_HOST}"
            echo
            echo "2. Check user mapping:"
            echo "   - Allow the Moonraker service user's UID/GID"
            echo "   - Keep root squashing enabled"
            echo
            echo "3. Documentation:"
            echo "   - See docs/nas_setup/synology.md for details"
            echo
            sudo umount "$NAS_MOUNT_LOCAL"
            return 1
        fi
        
        # Unmount for fstab setup
        sudo umount "$NAS_MOUNT_LOCAL"
    else
        print_error "Manual mount failed!"
        echo
        echo -e "${YELLOW}Problem:${NC} Could not mount NAS."
        echo
        echo -e "${BLUE}Possible causes and solutions:${NC}"
        echo
        echo "1. NFS export not configured:"
        echo "   → Go to your NAS and set up NFS export"
        echo "   → Path: $NAS_EXPORT"
        echo "   → See: docs/nas_setup/synology.md"
        echo
        echo "2. Wrong export path:"
        echo "   → Check if $NAS_EXPORT exists on NAS"
        echo "   → Synology: /volume1/..."
        echo "   → QNAP: /share/..."
        echo
        echo "3. Firewall blocks NFS:"
        echo "   → NFS requires ports: 111, 2049, 20048"
        echo "   → Check firewall on NAS and router"
        echo
        echo "4. NFS service not active:"
        echo "   → Enable NFS service on NAS"
        echo "   → Synology: Control Panel → File Services → NFS"
        echo
        echo "Tip: Test with 'showmount -e $NAS_HOST' to see available exports"
        echo
        return 1
    fi
    
    # Backup fstab
    print_info "Creating backup of /etc/fstab..."
    sudo cp /etc/fstab /etc/fstab.backup.kargo
    print_success "Backup created: /etc/fstab.backup.kargo"
    
    # Check if entry already exists
    if grep -q "$NAS_MOUNT_LOCAL" /etc/fstab; then
        print_warning "Mount point already in /etc/fstab"
        read -p "Overwrite? [y/N]: " overwrite
        if [[ "$overwrite" != "y" ]]; then
            print_info "Skipping fstab entry"
        else
            # Remove old entry
            sudo sed -i "\|$NAS_MOUNT_LOCAL|d" /etc/fstab
        fi
    fi
    
    # Add to fstab if not exists or overwritten
    if ! grep -q "$NAS_MOUNT_LOCAL" /etc/fstab; then
        print_info "Adding entry to /etc/fstab..."
        echo "" | sudo tee -a /etc/fstab >/dev/null
        echo "# KARGO NAS Mount" | sudo tee -a /etc/fstab >/dev/null
        echo "${NAS_HOST}:${NAS_EXPORT}  $NAS_MOUNT_LOCAL  nfs  nofail,x-systemd.automount,x-systemd.device-timeout=10s,rsize=8192,wsize=8192  0  0" | sudo tee -a /etc/fstab >/dev/null
        print_success "fstab entry added"
    fi
    
    # Mount via fstab
    print_info "Mounting via fstab..."
    sudo systemctl daemon-reload
    if sudo mount -a 2>/dev/null; then
        print_success "Mount successful!"
    else
        print_error "Mount via fstab failed"
        echo
        echo -e "${YELLOW}Problem:${NC} Automatic mounting doesn't work."
        echo
        echo -e "${BLUE}Check the following:${NC}"
        echo "1. Syntax in /etc/fstab:"
        echo "   → Run: cat /etc/fstab | grep kargo"
        echo "   → Format: HOST:EXPORT  MOUNT  nfs  OPTIONS  0  0"
        echo
        echo "2. Manual tests:"
        echo "   → sudo mount -t nfs ${NAS_HOST}:${NAS_EXPORT} $NAS_MOUNT_LOCAL"
        echo "   → If successful: problem is in fstab syntax"
        echo
        echo "3. Check logs:"
        echo "   → sudo journalctl -xe | grep mount"
        echo
        echo "Tip: Backup is at /etc/fstab.backup.kargo"
        echo
        return 1
    fi
    
    # Final verification
    if mountpoint -q "$NAS_MOUNT_LOCAL"; then
        print_success "✓ NAS is mounted and ready!"
        
        # Store for later use
        NAS_MOUNT="$NAS_MOUNT_LOCAL"
        export NAS_MOUNT
        
        return 0
    else
        print_error "Verification failed"
        return 1
    fi
}

# NAS configuration wizard
configure_nas() {
    print_header
    echo -e "${BLUE}═══ NAS Configuration Wizard ═══${NC}"
    echo
    
    # Mount point
    echo "Where is your NAS mounted?"
    echo "(If you just ran NFS mount setup: already set)"
    echo
    read -p "NAS mount point [/mnt/nas]: " NAS_MOUNT
    NAS_MOUNT="${NAS_MOUNT:-/mnt/nas}"
    
    # NAS directory
    echo
    echo "In which subfolder should G-codes be stored?"
    echo "Will be created automatically if it doesn't exist."
    echo
    read -p "NAS G-code directory [$NAS_MOUNT/klipper_gcodes]: " NAS_GCODES
    NAS_GCODES="${NAS_GCODES:-$NAS_MOUNT/klipper_gcodes}"
    
    # Validate it's under mount point
    if [[ ! $NAS_GCODES =~ ^$NAS_MOUNT ]]; then
        print_warning "G-code directory should be under $NAS_MOUNT"
        print_info "Correcting to: $NAS_MOUNT/klipper_gcodes"
        NAS_GCODES="$NAS_MOUNT/klipper_gcodes"
    fi
    
    # Offload mode
    echo
    echo "Offload modes:"
    echo "  1) hybrid   - Symlink + stub file (recommended)"
    echo "  2) symlink  - Symlink only"
    echo "  3) stub     - Stub file only"
    read -p "Select mode [1]: " mode_choice
    case "${mode_choice:-1}" in
        1) OFFLOAD_MODE="hybrid" ;;
        2) OFFLOAD_MODE="symlink" ;;
        3) OFFLOAD_MODE="stub" ;;
        *) OFFLOAD_MODE="hybrid" ;;
    esac
    
    # Offload triggers
    echo
    read -p "Offload on successful print? [Y/n]: " offload_complete
    OFFLOAD_COMPLETE=$([[ "$offload_complete" != "n" ]] && echo "True" || echo "False")
    
    read -p "Offload on cancelled print? [y/N]: " offload_cancelled
    OFFLOAD_CANCELLED=$([[ "$offload_cancelled" == "y" ]] && echo "True" || echo "False")
    
    read -p "Offload on failed print? [y/N]: " offload_error
    OFFLOAD_ERROR=$([[ "$offload_error" == "y" ]] && echo "True" || echo "False")
    
    echo
    print_success "Configuration:"
    echo "  Mount point: $NAS_MOUNT"
    echo "  NAS directory: $NAS_GCODES"
    echo "  Mode: $OFFLOAD_MODE"
    echo "  Trigger on complete: $OFFLOAD_COMPLETE"
    echo "  Trigger on cancelled: $OFFLOAD_CANCELLED"
    echo "  Trigger on error: $OFFLOAD_ERROR"
    echo
    
    read -p "Proceed with this configuration? [Y/n]: " confirm
    if [[ "$confirm" == "n" ]]; then
        echo "Configuration cancelled."
        return 1
    fi
    
    return 0
}

# Probe as the service user; never follow or overwrite an existing entry.
test_nas_write() {
    python3 - "$1" <<'PY'
import os
import secrets
import sys

if os.geteuid() == 0:
    sys.exit("Run the installer as the Moonraker service user, without sudo.")
directory_fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    for attempt in range(10):
        name = '.kargo-test-' + secrets.token_hex(16)
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory_fd)
            break
        except FileExistsError:
            continue
    else:
        sys.exit("Unable to create an exclusive NAS write probe.")
    try:
        with os.fdopen(fd, 'wb') as probe:
            probe.write(b'test\n')
    finally:
        os.unlink(name, dir_fd=directory_fd)
finally:
    os.close(directory_fd)
PY
}

# Test NAS connection
test_nas() {
    print_header
    echo -e "${BLUE}═══ NAS Connection Test ═══${NC}"
    echo
    
    local mount_point="${1:-/mnt/nas}"
    
    # Check if mount point exists
    if [ ! -d "$mount_point" ]; then
        print_warning "Mount point doesn't exist, creating..."
        sudo mkdir -p "$mount_point"
    fi
    
    # Check if mounted
    if mountpoint -q "$mount_point"; then
        print_success "NAS is mounted at $mount_point"
    else
        print_error "NAS is NOT mounted at $mount_point"
        echo
        print_info "Please ensure NAS is configured in /etc/fstab"
        print_info "See docs/nas_setup/ for your NAS system"
        return 1
    fi
    
    # Write test
    if test_nas_write "$mount_point"; then
        print_success "Write test successful"
    else
        print_error "Write test failed - no write permission"
        return 1
    fi
    
    # Check available space
    local available=$(df -h "$mount_point" | tail -1 | awk '{print $4}')
    print_success "Available space: $available"
    
    echo
    return 0
}

# Install component
do_install() {
    print_header
    echo -e "${BLUE}═══ Installing KARGO ═══${NC}"
    echo
    
    # Detect paths
    detect_paths
    
    # Check prerequisites
    check_prerequisites
    
    # NAS mount setup (if not already mounted)
    if ! mountpoint -q "${NAS_MOUNT:-/mnt/nas}" 2>/dev/null; then
        print_info "NAS not yet mounted"
        if ! setup_nas_mount; then
            print_error "NAS mount setup failed"
            return 1
        fi
    else
        print_success "NAS is already mounted"
        read -p "NAS mount point [/mnt/nas]: " NAS_MOUNT
        NAS_MOUNT="${NAS_MOUNT:-/mnt/nas}"
    fi
    
    # Configure NAS
    if ! configure_nas; then
        return 1
    fi
    
    # Test NAS connection
    if ! test_nas "$NAS_MOUNT"; then
        print_warning "NAS connection test failed"
        read -p "Continue anyway? [y/N]: " continue_install
        if [[ "$continue_install" != "y" ]]; then
            echo "Installation cancelled."
            return 1
        fi
    fi
    
    echo
    print_info "Installing components..."
    
    # Backup existing files
    if [ -f "$KLIPPER_CONFIG_DIR/nas_offload.cfg" ]; then
        cp "$KLIPPER_CONFIG_DIR/nas_offload.cfg" "$KLIPPER_CONFIG_DIR/nas_offload.cfg.backup"
        print_info "Backed up existing nas_offload.cfg"
    fi
    
    # Copy Moonraker component
    print_info "Installing Moonraker component..."
    cp "$SCRIPT_DIR/scripts/nas_offload.py" "$MOONRAKER_DIR/components/"
    cp -r "$SCRIPT_DIR/scripts/utils" "$MOONRAKER_DIR/components/"
    print_success "Moonraker component installed"
    
    # Copy Klipper macros
    print_info "Installing Klipper macros..."
    cp "$SCRIPT_DIR/klipper_config/nas_offload.cfg" "$KLIPPER_CONFIG_DIR/"
    print_success "Klipper macros installed"
    
    # Generate moonraker.conf snippet
    local moonraker_conf="$KLIPPER_CONFIG_DIR/moonraker.conf"
    mkdir -p "$PRINTER_DATA_DIR/systemd/nas-offload-locks" "$PRINTER_DATA_DIR/database"
    
    if grep -q "\[nas_offload\]" "$moonraker_conf" 2>/dev/null; then
        print_warning "nas_offload section already exists in moonraker.conf"
        print_info "Please update manually if needed"
    else
        print_info "Adding nas_offload section to moonraker.conf..."
        cat >> "$moonraker_conf" << EOF

# KARGO Configuration
[nas_offload]
nas_mount_point: $NAS_MOUNT
nas_gcodes_dir: $NAS_GCODES
local_gcodes_dir: $PRINTER_DATA_DIR/gcodes
cache_db: $PRINTER_DATA_DIR/database/nas_offload.db
lock_dir: $PRINTER_DATA_DIR/systemd/nas-offload-locks
offload_mode: $OFFLOAD_MODE
offload_on_complete: $OFFLOAD_COMPLETE
offload_on_cancelled: $OFFLOAD_CANCELLED
offload_on_error: $OFFLOAD_ERROR
EOF
        print_success "Added nas_offload configuration to moonraker.conf"
    fi
    
    # Check if printer.cfg includes nas_offload.cfg
    local printer_cfg="$KLIPPER_CONFIG_DIR/printer.cfg"
    if grep -q "nas_offload.cfg" "$printer_cfg" 2>/dev/null; then
        print_info "printer.cfg already includes nas_offload.cfg"
    else
        print_warning "Please add to printer.cfg:"
        echo "  [include nas_offload.cfg]"
    fi
    
    echo
    print_success "Installation complete!"
    echo
    print_info "Next steps:"
    echo "  1. Restart Moonraker: sudo systemctl restart moonraker"
    echo "  2. Restart Klipper: sudo systemctl restart klipper"
    echo "  3. Check status: http://localhost:7125/server/nas_offload/status"
    echo
}

# Uninstall
do_uninstall() {
    print_header
    echo -e "${RED}═══ Uninstalling KARGO ═══${NC}"
    echo
    
    read -p "Are you sure? This will remove all components [y/N]: " confirm
    if [[ "$confirm" != "y" ]]; then
        echo "Uninstall cancelled."
        return
    fi
    
    detect_paths
    
    print_info "Removing components..."
    
    # Remove Moonraker component
    rm -rf "$MOONRAKER_DIR/components/utils"
    rm -f "$MOONRAKER_DIR/components/nas_offload.py"
    print_success "Moonraker component removed"
    
    # Remove Klipper macros
    rm -f "$KLIPPER_CONFIG_DIR/nas_offload.cfg"
    print_success "Klipper macros removed"
    
    print_warning "Please manually remove [nas_offload] section from moonraker.conf"
    print_warning "Please manually remove [include nas_offload.cfg] from printer.cfg"
    
    echo
    print_success "Uninstall complete!"
    print_info "Restart Moonraker and Klipper to complete removal"
}

# Show status
show_status() {
    print_header
    echo -e "${BLUE}═══ System Status ═══${NC}"
    echo
    
    detect_paths
    
    # Check if component installed
    if [ -f "$MOONRAKER_DIR/components/nas_offload.py" ]; then
        print_success "Moonraker component: Installed"
    else
        print_error "Moonraker component: Not installed"
    fi
    
    if [ -f "$KLIPPER_CONFIG_DIR/nas_offload.cfg" ]; then
        print_success "Klipper macros: Installed"
    else
        print_error "Klipper macros: Not installed"
    fi
    
    # Check moonraker.conf
    if grep -q "\[nas_offload\]" "$KLIPPER_CONFIG_DIR/moonraker.conf" 2>/dev/null; then
        print_success "Configuration: Found in moonraker.conf"
    else
        print_warning "Configuration: Not found in moonraker.conf"
    fi
    
    # Try to query API
    echo
    print_info "Querying API..."
    if command -v curl &>/dev/null; then
        if curl -s "http://localhost:7125/server/nas_offload/status" &>/dev/null; then
            print_success "API is responding"
            echo
            curl -s "http://localhost:7125/server/nas_offload/status" | python3 -m json.tool
        else
            print_warning "API not responding"
            echo
            echo -e "${YELLOW}Possible causes:${NC}"
            echo "1. Moonraker not running:"
            echo "   → sudo systemctl status moonraker"
            echo "   → sudo systemctl restart moonraker"
            echo
            echo "2. KARGO not loaded:"
            echo "   → Check logs: tail -f ~/printer_data/logs/moonraker.log | grep -i kargo"
            echo "   → Look for loading errors"
            echo
            echo "3. Configuration missing:"
            echo "   → Check if [nas_offload] exists in moonraker.conf"
            echo
        fi
    fi
    
    echo
}

# Main menu
show_menu() {
    print_header
    echo " 1) Install KARGO System (incl. NFS Mount Setup)"
    echo " 2) Set up NFS Mount only"
    echo " 3) Configure NAS Settings"
    echo " 4) Test NAS Connection"
    echo " 5) Show Status"
    echo " 6) Uninstall"
    echo
    echo " Q) Quit"
    echo
}

# Main loop
main() {
    while true; do
        show_menu
        read -p "Select option: " choice
        
        case "$choice" in
            1)
                do_install
                read -p "Press Enter to continue..."
                ;;
            2)
                setup_nas_mount
                read -p "Press Enter to continue..."
                ;;
            3)
                configure_nas
                read -p "Press Enter to continue..."
                ;;
            4)
                read -p "NAS mount point [/mnt/nas]: " mount_test
                test_nas "${mount_test:-/mnt/nas}"
                read -p "Press Enter to continue..."
                ;;
            5)
                show_status
                read -p "Press Enter to continue..."
                ;;
            6)
                do_uninstall
                read -p "Press Enter to continue..."
                ;;
            [Qq])
                echo "Goodbye!"
                exit 0
                ;;
            *)
                print_error "Invalid option"
                sleep 1
                ;;
        esac
    done
}

# Run
main
