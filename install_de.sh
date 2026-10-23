#!/bin/bash
# KARGO - Klipper Archive & Remote G-code Operations
# Installations-Skript - KIAUH-Stil interaktiver Installer (DEUTSCHE VERSION)

set -e

# Farben
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # Keine Farbe

# Pfade
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOONRAKER_DIR=""
KLIPPER_CONFIG_DIR=""
PRINTER_DATA_DIR=""

# Hilfsfunktionen
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

# Pfade erkennen
detect_paths() {
    print_info "Erkenne Installationspfade..."
    
    # Versuche gängige Orte
    if [ -d "$HOME/printer_data" ]; then
        PRINTER_DATA_DIR="$HOME/printer_data"
    elif [ -d "$HOME/klipper_data" ]; then
        PRINTER_DATA_DIR="$HOME/klipper_data"
    else
        print_warning "Konnte printer_data Verzeichnis nicht automatisch finden"
        read -p "Gib den printer_data Pfad ein [$HOME/printer_data]: " custom_path
        PRINTER_DATA_DIR="${custom_path:-$HOME/printer_data}"
    fi
    
    KLIPPER_CONFIG_DIR="$PRINTER_DATA_DIR/config"
    
    # Finde Moonraker
    if [ -d "$HOME/moonraker/moonraker" ]; then
        MOONRAKER_DIR="$HOME/moonraker/moonraker"
    else
        print_warning "Konnte Moonraker Verzeichnis nicht finden"
        read -p "Gib den Moonraker Pfad ein [$HOME/moonraker/moonraker]: " custom_mr
        MOONRAKER_DIR="${custom_mr:-$HOME/moonraker/moonraker}"
    fi
    
    print_success "Pfade erkannt:"
    echo "  printer_data: $PRINTER_DATA_DIR"
    echo "  Klipper Config: $KLIPPER_CONFIG_DIR"
    echo "  Moonraker: $MOONRAKER_DIR"
    echo
}

# Voraussetzungen prüfen
check_prerequisites() {
    print_info "Prüfe Voraussetzungen..."
    
    local all_ok=true
    
    # Python 3
    if command -v python3 &> /dev/null; then
        local py_version=$(python3 --version | cut -d' ' -f2)
        print_success "Python 3: $py_version"
    else
        print_error "Python 3 nicht gefunden"
        all_ok=false
    fi
    
    # Moonraker
    if [ -d "$MOONRAKER_DIR" ]; then
        print_success "Moonraker: $MOONRAKER_DIR"
    else
        print_error "Moonraker nicht gefunden unter $MOONRAKER_DIR"
        all_ok=false
    fi
    
    # Klipper config
    if [ -d "$KLIPPER_CONFIG_DIR" ]; then
        print_success "Klipper Config: $KLIPPER_CONFIG_DIR"
    else
        print_error "Klipper Config nicht gefunden unter $KLIPPER_CONFIG_DIR"
        all_ok=false
    fi
    
    # NFS client
    if command -v mount.nfs &> /dev/null; then
        print_success "NFS Client installiert"
    else
        print_warning "NFS Client (nfs-common) nicht installiert"
        read -p "Soll nfs-common jetzt installiert werden? [J/n]: " install_nfs
        if [[ "$install_nfs" != "n" ]]; then
            sudo apt-get update
            sudo apt-get install -y nfs-common
            print_success "NFS Client installiert"
        fi
    fi
    
    echo
    
    if [ "$all_ok" = false ]; then
        print_error "Voraussetzungs-Prüfung fehlgeschlagen"
        echo
        echo -e "${YELLOW}Bitte installiere die fehlenden Komponenten:${NC}"
        echo
        echo "Python 3:"
        echo "  sudo apt-get install python3"
        echo
        echo "NFS Client:"
        echo "  sudo apt-get install nfs-common"
        echo
        echo "Moonraker/Klipper:"
        echo "  Siehe: https://docs.mainsail.xyz/setup"
        echo
        exit 1
    fi
}

# Validierungs-Hilfsfunktionen
validate_ip() {
    local ip=$1
    if [[ $ip =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
        return 0
    fi
    return 1
}

validate_hostname() {
    local host=$1
    # Hostname oder IP erlauben
    if validate_ip "$host" || [[ $host =~ ^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$ ]]; then
        return 0
    fi
    return 1
}

detect_nas_type() {
    local host=$1
    
    # Versuche Synology zu erkennen
    if [[ $host =~ synology|diskstation ]]; then
        echo "synology"
        return
    fi
    
    # Versuche QNAP zu erkennen
    if [[ $host =~ qnap ]]; then
        echo "qnap"
        return
    fi
    
    # Versuche TrueNAS zu erkennen
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
    echo -e "${BLUE}═══ NAS Mount Einrichtung ═══${NC}"
    echo
    
    print_info "Dieser Wizard richtet den NFS Mount automatisch ein."
    echo
    
    # NAS Server details
    echo -e "${BLUE}Schritt 1: NAS Server${NC}"
    echo "Beispiele:"
    echo "  - IP-Adresse: 192.168.1.50"
    echo "  - Hostname: nas.local oder synology-nas"
    echo
    
    while true; do
        read -p "NAS IP-Adresse oder Hostname: " NAS_HOST
        
        if [ -z "$NAS_HOST" ]; then
            print_error "NAS Host ist erforderlich"
            continue
        fi
        
        # Validiere Eingabe
        if ! validate_hostname "$NAS_HOST"; then
            print_error "Ungültige IP-Adresse oder Hostname"
            echo "Beispiel: 192.168.1.50 oder nas.local"
            continue
        fi
        
        print_success "Host: $NAS_HOST"
        break
    done
    
    # Auto-detect NAS type
    NAS_TYPE=$(detect_nas_type "$NAS_HOST")
    DEFAULT_EXPORT=$(get_default_export_path "$NAS_TYPE")
    
    if [ "$NAS_TYPE" != "generic" ]; then
        print_info "NAS-Typ erkannt: $NAS_TYPE"
    fi
    
    echo
    echo -e "${BLUE}Schritt 2: NFS Export-Pfad${NC}"
    echo "Dies ist der Pfad AUF dem NAS (nicht lokal)."
    echo "Beispiele:"
    echo "  - Synology: /volume1/klipper_gcodes"
    echo "  - QNAP: /share/klipper_gcodes"
    echo "  - Generic Linux: /export/klipper_gcodes"
    echo
    read -p "NFS Export-Pfad [$DEFAULT_EXPORT]: " NAS_EXPORT
    NAS_EXPORT="${NAS_EXPORT:-$DEFAULT_EXPORT}"
    
    # Validiere Pfad Format
    if [[ ! $NAS_EXPORT =~ ^/ ]]; then
        print_warning "Pfad sollte mit / beginnen, korrigiere..."
        NAS_EXPORT="/$NAS_EXPORT"
    fi
    
    echo
    echo -e "${BLUE}Schritt 3: Lokaler Mount-Point${NC}"
    echo "Wo soll das NAS auf diesem System gemountet werden?"
    echo "Standard: /mnt/nas (empfohlen)"
    echo
    read -p "Lokaler Mount-Point [/mnt/nas]: " NAS_MOUNT_LOCAL
    NAS_MOUNT_LOCAL="${NAS_MOUNT_LOCAL:-/mnt/nas}"
    
    # Validiere Mount-Point
    if [[ ! $NAS_MOUNT_LOCAL =~ ^/ ]]; then
        print_error "Mount-Point muss absoluter Pfad sein (beginnt mit /)"
        return 1
    fi
    
    # Warne bei unüblichen Pfaden
    if [[ ! $NAS_MOUNT_LOCAL =~ ^/mnt/|^/media/ ]]; then
        print_warning "Unüblicher Mount-Point. Standard ist /mnt/nas"
        read -p "Trotzdem fortfahren? [j/N]: " confirm_path
        if [[ "$confirm_path" != "j" ]]; then
            return 1
        fi
    fi
    
    echo
    print_success "NFS Mount Konfiguration:"
    echo "  Server: $NAS_HOST"
    echo "  Export: $NAS_EXPORT"
    echo "  Mount-Point: $NAS_MOUNT_LOCAL"
    echo
    
    read -p "Fortfahren? [J/n]: " confirm
    if [[ "$confirm" == "n" ]]; then
        return 1
    fi
    
    # Test NFS connectivity first
    print_info "Teste NFS Server Erreichbarkeit..."
    if ! ping -c 1 -W 2 "$NAS_HOST" &>/dev/null; then
        print_warning "NAS Host nicht erreichbar via ping"
        print_info "Mögliche Ursachen:"
        echo "  - NAS ist offline"
        echo "  - Firewall blockiert ICMP"
        echo "  - Falsche IP/Hostname"
        read -p "Trotzdem fortfahren? [j/N]: " continue_anyway
        if [[ "$continue_anyway" != "j" ]]; then
            return 1
        fi
    else
        print_success "NAS Host erreichbar ($NAS_HOST)"
    fi
    
    # Check if showmount works
    if command -v showmount &>/dev/null; then
        print_info "Prüfe NFS Exports..."
        if showmount -e "$NAS_HOST" &>/dev/null; then
            print_success "NFS Server antwortet"
            echo
            showmount -e "$NAS_HOST"
            echo
        else
            print_warning "Konnte NFS Exports nicht abfragen"
        fi
    fi
    
    # Create mount point
    print_info "Erstelle Mount-Point $NAS_MOUNT_LOCAL..."
    if [ ! -d "$NAS_MOUNT_LOCAL" ]; then
        sudo mkdir -p "$NAS_MOUNT_LOCAL"
        print_success "Mount-Point erstellt"
    else
        print_info "Mount-Point existiert bereits"
    fi
    
    # Test manual mount first
    print_info "Teste manuelles Mounten..."
    if sudo mount -t nfs "${NAS_HOST}:${NAS_EXPORT}" "$NAS_MOUNT_LOCAL" 2>/dev/null; then
        print_success "Manueller Mount erfolgreich!"
        
        # Test write permission
        if test_nas_write "$NAS_MOUNT_LOCAL"; then
            print_success "Schreibrechte OK"
        else
            print_error "Keine Schreibrechte auf NAS!"
            echo
            echo -e "${YELLOW}Problem:${NC} Du kannst nicht auf das NAS schreiben."
            echo
            echo -e "${BLUE}Lösungen:${NC}"
            echo "1. Prüfe NFS Export Berechtigungen auf dem NAS:"
            echo "   - Synology: Systemsteuerung → Gemeinsame Ordner → NFS-Berechtigungen"
            echo "   - Stelle sicher: Lese-/Schreibrechte aktiviert"
            echo "   - Client IP erlaubt: ${NAS_HOST}"
            echo
            echo "2. Prüfe User-Mapping:"
            echo "   - UID/GID des Moonraker-Service-Users zulassen"
            echo "   - Root squashing aktiviert lassen"
            echo
            echo "3. Dokumentation:"
            echo "   - Siehe docs/nas_setup/synology.md für Details"
            echo
            sudo umount "$NAS_MOUNT_LOCAL"
            return 1
        fi
        
        # Unmount for fstab setup
        sudo umount "$NAS_MOUNT_LOCAL"
    else
        print_error "Manueller Mount fehlgeschlagen!"
        echo
        echo -e "${YELLOW}Problem:${NC} Konnte NAS nicht mounten."
        echo
        echo -e "${BLUE}Mögliche Ursachen und Lösungen:${NC}"
        echo
        echo "1. NFS Export nicht konfiguriert:"
        echo "   → Gehe auf dein NAS und richte NFS Export ein"
        echo "   → Pfad: $NAS_EXPORT"
        echo "   → Siehe: docs/nas_setup/synology.md"
        echo
        echo "2. Falscher Export-Pfad:"
        echo "   → Prüfe ob $NAS_EXPORT auf dem NAS existiert"
        echo "   → Synology: /volume1/..."
        echo "   → QNAP: /share/..."
        echo
        echo "3. Firewall blockiert NFS:"
        echo "   → NFS benötigt Ports: 111, 2049, 20048"
        echo "   → Prüfe Firewall auf NAS und Router"
        echo
        echo "4. NFS Service nicht aktiv:"
        echo "   → Aktiviere NFS Service auf dem NAS"
        echo "   → Synology: Systemsteuerung → Dateidienste → NFS"
        echo
        echo "Tipp: Teste mit 'showmount -e $NAS_HOST' welche Exports verfügbar sind"
        echo
        return 1
    fi
    
    # Backup fstab
    print_info "Erstelle Backup von /etc/fstab..."
    sudo cp /etc/fstab /etc/fstab.backup.kargo
    print_success "Backup erstellt: /etc/fstab.backup.kargo"
    
    # Check if entry already exists
    if grep -q "$NAS_MOUNT_LOCAL" /etc/fstab; then
        print_warning "Mount-Point bereits in /etc/fstab vorhanden"
        read -p "Überschreiben? [j/N]: " overwrite
        if [[ "$overwrite" != "j" ]]; then
            print_info "Überspringe fstab Eintrag"
        else
            # Remove old entry
            sudo sed -i "\|$NAS_MOUNT_LOCAL|d" /etc/fstab
        fi
    fi
    
    # Add to fstab if not exists or overwritten
    if ! grep -q "$NAS_MOUNT_LOCAL" /etc/fstab; then
        print_info "Füge Eintrag zu /etc/fstab hinzu..."
        echo "" | sudo tee -a /etc/fstab >/dev/null
        echo "# KARGO NAS Mount" | sudo tee -a /etc/fstab >/dev/null
        echo "${NAS_HOST}:${NAS_EXPORT}  $NAS_MOUNT_LOCAL  nfs  nofail,x-systemd.automount,x-systemd.device-timeout=10s,rsize=8192,wsize=8192  0  0" | sudo tee -a /etc/fstab >/dev/null
        print_success "fstab Eintrag hinzugefügt"
    fi
    
    # Mount via fstab
    print_info "Mounten via fstab..."
    sudo systemctl daemon-reload
    if sudo mount -a 2>/dev/null; then
        print_success "Mount erfolgreich!"
    else
        print_error "Mount via fstab fehlgeschlagen"
        echo
        echo -e "${YELLOW}Problem:${NC} Automatisches Mounten funktioniert nicht."
        echo
        echo -e "${BLUE}Prüfe folgendes:${NC}"
        echo "1. Syntax in /etc/fstab:"
        echo "   → Führe aus: cat /etc/fstab | grep kargo"
        echo "   → Format: HOST:EXPORT  MOUNT  nfs  OPTIONS  0  0"
        echo
        echo "2. Manuelle Tests:"
        echo "   → sudo mount -t nfs ${NAS_HOST}:${NAS_EXPORT} $NAS_MOUNT_LOCAL"
        echo "   → Falls erfolgreich: Problem liegt in fstab Syntax"
        echo
        echo "3. Logs prüfen:"
        echo "   → sudo journalctl -xe | grep mount"
        echo
        echo "Tipp: Backup liegt unter /etc/fstab.backup.kargo"
        echo
        return 1
    fi
    
    # Final verification
    if mountpoint -q "$NAS_MOUNT_LOCAL"; then
        print_success "✓ NAS ist gemountet und bereit!"
        
        # Store for later use
        NAS_MOUNT="$NAS_MOUNT_LOCAL"
        export NAS_MOUNT
        
        return 0
    else
        print_error "Verifikation fehlgeschlagen"
        return 1
    fi
}

# NAS Konfigurations-Wizard
configure_nas() {
    print_header
    echo -e "${BLUE}═══ NAS Konfigurationswizard ═══${NC}"
    echo
    
    # Mount point
    echo "Wo ist dein NAS gemountet?"
    echo "(Falls du gerade das NFS Mount Setup gemacht hast: bereits gesetzt)"
    echo
    read -p "NAS Mount-Point [/mnt/nas]: " NAS_MOUNT
    NAS_MOUNT="${NAS_MOUNT:-/mnt/nas}"
    
    # NAS directory
    echo
    echo "In welchem Unterordner sollen G-Codes gespeichert werden?"
    echo "Wird automatisch erstellt falls nicht vorhanden."
    echo
    read -p "NAS G-Code Verzeichnis [$NAS_MOUNT/klipper_gcodes]: " NAS_GCODES
    NAS_GCODES="${NAS_GCODES:-$NAS_MOUNT/klipper_gcodes}"
    
    # Validiere dass es Unterordner vom Mount-Point ist
    if [[ ! $NAS_GCODES =~ ^$NAS_MOUNT ]]; then
        print_warning "G-Code Verzeichnis sollte unter $NAS_MOUNT liegen"
        print_info "Korrigiere zu: $NAS_MOUNT/klipper_gcodes"
        NAS_GCODES="$NAS_MOUNT/klipper_gcodes"
    fi
    
    # Offload mode
    echo
    echo "Offload-Modi:"
    echo "  1) hybrid   - Symlink + Stub-Datei (empfohlen)"
    echo "  2) symlink  - Nur Symlink"
    echo "  3) stub     - Nur Stub-Datei"
    read -p "Modus wählen [1]: " mode_choice
    case "${mode_choice:-1}" in
        1) OFFLOAD_MODE="hybrid" ;;
        2) OFFLOAD_MODE="symlink" ;;
        3) OFFLOAD_MODE="stub" ;;
        *) OFFLOAD_MODE="hybrid" ;;
    esac
    
    # Offload triggers
    echo
    read -p "Offload bei erfolgreichem Druck? [J/n]: " offload_complete
    OFFLOAD_COMPLETE=$([[ "$offload_complete" != "n" ]] && echo "True" || echo "False")
    
    read -p "Offload bei abgebrochenem Druck? [j/N]: " offload_cancelled
    OFFLOAD_CANCELLED=$([[ "$offload_cancelled" == "j" ]] && echo "True" || echo "False")
    
    read -p "Offload bei Fehler? [j/N]: " offload_error
    OFFLOAD_ERROR=$([[ "$offload_error" == "j" ]] && echo "True" || echo "False")
    
    echo
    print_success "Konfiguration:"
    echo "  Mount-Point: $NAS_MOUNT"
    echo "  NAS Verzeichnis: $NAS_GCODES"
    echo "  Modus: $OFFLOAD_MODE"
    echo "  Bei erfolgreich: $OFFLOAD_COMPLETE"
    echo "  Bei abgebrochen: $OFFLOAD_CANCELLED"
    echo "  Bei Fehler: $OFFLOAD_ERROR"
    echo
    
    read -p "Mit dieser Konfiguration fortfahren? [J/n]: " confirm
    if [[ "$confirm" == "n" ]]; then
        echo "Konfiguration abgebrochen."
        return 1
    fi
    
    return 0
}

# Schreibprobe als Service-User; vorhandene Eintraege niemals ueberschreiben.
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

# NAS Verbindung testen
test_nas() {
    print_header
    echo -e "${BLUE}═══ NAS Verbindungstest ═══${NC}"
    echo
    
    local mount_point="${1:-/mnt/nas}"
    
    # Prüfe ob Mount-Point existiert
    if [ ! -d "$mount_point" ]; then
        print_warning "Mount-Point existiert nicht, erstelle..."
        sudo mkdir -p "$mount_point"
    fi
    
    # Prüfe ob gemountet
    if mountpoint -q "$mount_point"; then
        print_success "NAS ist gemountet unter $mount_point"
    else
        print_error "NAS ist NICHT gemountet unter $mount_point"
        echo
        print_info "Bitte stelle sicher, dass das NAS in /etc/fstab konfiguriert ist"
        print_info "Siehe docs/nas_setup/ für dein NAS-System"
        return 1
    fi
    
    # Schreibtest
    if test_nas_write "$mount_point"; then
        print_success "Schreibtest erfolgreich"
    else
        print_error "Schreibtest fehlgeschlagen - keine Schreibberechtigung"
        return 1
    fi
    
    # Verfügbarer Speicher
    local available=$(df -h "$mount_point" | tail -1 | awk '{print $4}')
    print_success "Verfügbarer Speicher: $available"
    
    echo
    return 0
}

# Installation durchführen
do_install() {
    print_header
    echo -e "${BLUE}═══ KARGO installieren ═══${NC}"
    echo
    
    # Pfade erkennen
    detect_paths
    
    # Voraussetzungen prüfen
    check_prerequisites
    
    # NAS Mount einrichten (wenn noch nicht gemountet)
    if ! mountpoint -q "${NAS_MOUNT:-/mnt/nas}" 2>/dev/null; then
        print_info "NAS ist noch nicht gemountet"
        if ! setup_nas_mount; then
            print_error "NAS Mount Setup fehlgeschlagen"
            return 1
        fi
    else
        print_success "NAS ist bereits gemountet"
        read -p "NAS Mount-Point [/mnt/nas]: " NAS_MOUNT
        NAS_MOUNT="${NAS_MOUNT:-/mnt/nas}"
    fi
    
    # NAS konfigurieren
    if ! configure_nas; then
        return 1
    fi
    
    # NAS Verbindung testen
    if ! test_nas "$NAS_MOUNT"; then
        print_warning "NAS Verbindungstest fehlgeschlagen"
        read -p "Trotzdem fortfahren? [j/N]: " continue_install
        if [[ "$continue_install" != "j" ]]; then
            echo "Installation abgebrochen."
            return 1
        fi
    fi
    
    echo
    print_info "Installiere Komponenten..."
    
    # Backup vorhandener Dateien
    if [ -f "$KLIPPER_CONFIG_DIR/nas_offload.cfg" ]; then
        cp "$KLIPPER_CONFIG_DIR/nas_offload.cfg" "$KLIPPER_CONFIG_DIR/nas_offload.cfg.backup"
        print_info "Backup der vorhandenen nas_offload.cfg erstellt"
    fi
    
    # Moonraker Komponente kopieren
    print_info "Installiere Moonraker Komponente..."
    cp "$SCRIPT_DIR/scripts/nas_offload.py" "$MOONRAKER_DIR/components/"
    cp -r "$SCRIPT_DIR/scripts/utils" "$MOONRAKER_DIR/components/"
    print_success "Moonraker Komponente installiert"
    
    # Klipper Makros kopieren
    print_info "Installiere Klipper Makros..."
    cp "$SCRIPT_DIR/klipper_config/nas_offload.cfg" "$KLIPPER_CONFIG_DIR/"
    print_success "Klipper Makros installiert"
    
    # moonraker.conf Snippet generieren
    local moonraker_conf="$KLIPPER_CONFIG_DIR/moonraker.conf"
    mkdir -p "$PRINTER_DATA_DIR/systemd/nas-offload-locks" "$PRINTER_DATA_DIR/database"
    
    if grep -q "\[nas_offload\]" "$moonraker_conf" 2>/dev/null; then
        print_warning "nas_offload Sektion existiert bereits in moonraker.conf"
        print_info "Bitte manuell aktualisieren falls nötig"
    else
        print_info "Füge nas_offload Sektion zu moonraker.conf hinzu..."
        cat >> "$moonraker_conf" << EOF

# KARGO Konfiguration
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
        print_success "nas_offload Konfiguration zu moonraker.conf hinzugefügt"
    fi
    
    # Prüfe ob printer.cfg nas_offload.cfg inkludiert
    local printer_cfg="$KLIPPER_CONFIG_DIR/printer.cfg"
    if grep -q "nas_offload.cfg" "$printer_cfg" 2>/dev/null; then
        print_info "printer.cfg inkludiert bereits nas_offload.cfg"
    else
        print_warning "Bitte füge zu printer.cfg hinzu:"
        echo "  [include nas_offload.cfg]"
    fi
    
    echo
    print_success "Installation abgeschlossen!"
    echo
    print_info "Nächste Schritte:"
    echo "  1. Moonraker neustarten: sudo systemctl restart moonraker"
    echo "  2. Klipper neustarten: sudo systemctl restart klipper"
    echo "  3. Status prüfen: http://localhost:7125/server/nas_offload/status"
    echo
}

# Deinstallation
do_uninstall() {
    print_header
    echo -e "${RED}═══ KARGO deinstallieren ═══${NC}"
    echo
    
    read -p "Bist du sicher? Dies entfernt alle Komponenten [j/N]: " confirm
    if [[ "$confirm" != "j" ]]; then
        echo "Deinstallation abgebrochen."
        return
    fi
    
    detect_paths
    
    print_info "Entferne Komponenten..."
    
    # Moonraker Komponente entfernen
    rm -rf "$MOONRAKER_DIR/components/utils"
    rm -f "$MOONRAKER_DIR/components/nas_offload.py"
    print_success "Moonraker Komponente entfernt"
    
    # Klipper Makros entfernen
    rm -f "$KLIPPER_CONFIG_DIR/nas_offload.cfg"
    print_success "Klipper Makros entfernt"
    
    print_warning "Bitte entferne manuell die [nas_offload] Sektion aus moonraker.conf"
    print_warning "Bitte entferne manuell [include nas_offload.cfg] aus printer.cfg"
    
    echo
    print_success "Deinstallation abgeschlossen!"
    print_info "Starte Moonraker und Klipper neu um die Entfernung abzuschließen"
}

# Status anzeigen
show_status() {
    print_header
    echo -e "${BLUE}═══ System-Status ═══${NC}"
    echo
    
    detect_paths
    
    # Prüfe ob Komponente installiert ist
    if [ -f "$MOONRAKER_DIR/components/nas_offload.py" ]; then
        print_success "Moonraker Komponente: Installiert"
    else
        print_error "Moonraker Komponente: Nicht installiert"
    fi
    
    if [ -f "$KLIPPER_CONFIG_DIR/nas_offload.cfg" ]; then
        print_success "Klipper Makros: Installiert"
    else
        print_error "Klipper Makros: Nicht installiert"
    fi
    
    # Prüfe moonraker.conf
    if grep -q "\[nas_offload\]" "$KLIPPER_CONFIG_DIR/moonraker.conf" 2>/dev/null; then
        print_success "Konfiguration: In moonraker.conf gefunden"
    else
        print_warning "Konfiguration: Nicht in moonraker.conf gefunden"
    fi
    
    # Versuche API abzufragen
    echo
    print_info "Frage API ab..."
    if command -v curl &> /dev/null; then
        if curl -s "http://localhost:7125/server/nas_offload/status" &> /dev/null; then
            print_success "API antwortet"
            echo
            curl -s "http://localhost:7125/server/nas_offload/status" | python3 -m json.tool
        else
            print_warning "API antwortet nicht"
            echo
            echo -e "${YELLOW}Mögliche Ursachen:${NC}"
            echo "1. Moonraker läuft nicht:"
            echo "   → sudo systemctl status moonraker"
            echo "   → sudo systemctl restart moonraker"
            echo
            echo "2. KARGO nicht geladen:"
            echo "   → Prüfe Logs: tail -f ~/printer_data/logs/moonraker.log | grep -i kargo"
            echo "   → Suche nach Fehlern beim Laden der Komponente"
            echo
            echo "3. Konfiguration fehlt:"
            echo "   → Prüfe ob [nas_offload] in moonraker.conf vorhanden"
            echo
        fi
    fi
    
    echo
}

# Hauptmenü
show_menu() {
    print_header
    echo " 1) KARGO System installieren (inkl. NFS Mount Setup)"
    echo " 2) Nur NFS Mount einrichten"
    echo " 3) NAS Einstellungen konfigurieren"
    echo " 4) NAS Verbindung testen"
    echo " 5) Status anzeigen"
    echo " 6) Deinstallieren"
    echo
    echo " Q) Beenden"
    echo
}

# Hauptschleife
main() {
    while true; do
        show_menu
        read -p "Option wählen: " choice
        
        case "$choice" in
            1)
                do_install
                read -p "Drücke Enter um fortzufahren..."
                ;;
            2)
                setup_nas_mount
                read -p "Drücke Enter um fortzufahren..."
                ;;
            3)
                configure_nas
                read -p "Drücke Enter um fortzufahren..."
                ;;
            4)
                read -p "NAS Mount-Point [/mnt/nas]: " mount_test
                test_nas "${mount_test:-/mnt/nas}"
                read -p "Drücke Enter um fortzufahren..."
                ;;
            5)
                show_status
                read -p "Drücke Enter um fortzufahren..."
                ;;
            6)
                do_uninstall
                read -p "Drücke Enter um fortzufahren..."
                ;;
            [Qq])
                echo "Auf Wiedersehen!"
                exit 0
                ;;
            *)
                print_error "Ungültige Option"
                sleep 1
                ;;
        esac
    done
}

# Ausführen
main
