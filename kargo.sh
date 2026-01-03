#!/bin/bash
# KARGO - Klipper Archive & Remote G-code Operations
# Language Selector & Installer Launcher

set -e

# Colors
BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Print header
clear
echo -e "${BLUE}╔════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║                                                    ║${NC}"
echo -e "${BLUE}║          KARGO Installer v1.0                      ║${NC}"
echo -e "${BLUE}║   Klipper Archive & Remote G-code Operations      ║${NC}"
echo -e "${BLUE}║                                                    ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════╝${NC}"
echo
echo -e "${GREEN}Welcome to KARGO!${NC}"
echo -e "This installer will help you set up automatic G-code archiving to your NAS."
echo
echo -e "${YELLOW}Please select your language / Bitte wähle deine Sprache:${NC}"
echo
echo "  1) English"
echo "  2) Deutsch"
echo
echo "  Q) Quit / Beenden"
echo

# Read user choice
while true; do
    read -p "Select / Wähle [1/2/Q]: " choice
    
    case "$choice" in
        1)
            echo
            echo -e "${GREEN}→ Starting English installer...${NC}"
            sleep 1
            exec "$SCRIPT_DIR/install.sh"
            ;;
        2)
            echo
            echo -e "${GREEN}→ Starte deutschen Installer...${NC}"
            sleep 1
            exec "$SCRIPT_DIR/install_de.sh"
            ;;
        [Qq])
            echo
            echo "Goodbye! / Auf Wiedersehen!"
            exit 0
            ;;
        *)
            echo -e "${YELLOW}Invalid choice. Please select 1, 2, or Q${NC}"
            echo -e "${YELLOW}Ungültige Auswahl. Bitte wähle 1, 2 oder Q${NC}"
            ;;
    esac
done
