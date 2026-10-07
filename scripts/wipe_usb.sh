#!/usr/bin/env bash
# scripts/wipe_usb.sh — wipe a USB drive and format it for model storage
#
# Usage:
#   bash scripts/wipe_usb.sh            # interactive: lists drives, you pick one
#   bash scripts/wipe_usb.sh /dev/sdX   # specify device directly (still confirms)
#
# Output format: exFAT (reads/writes on Mac, Windows, Linux — no driver needed)
# Labels:  BERYL-01 / BERYL-02 / BERYL-03  (you pick at runtime)
#
# Requires on the machine you run this from:
#   Linux : wipefs, parted, mkfs.exfat (exfatprogs or exfat-utils)
#   macOS : diskutil (built-in) — script detects OS and switches paths
#
# SAFE: nothing is written until you type the device path as confirmation.

set -uo pipefail

RED='\033[0;31m'; YLW='\033[0;33m'; GRN='\033[0;32m'; CYN='\033[0;36m'; RST='\033[0m'
BOLD='\033[1m'

die()  { echo -e "${RED}ERROR: $*${RST}" >&2; exit 1; }
info() { echo -e "${CYN}▸ $*${RST}"; }
ok()   { echo -e "${GRN}✓ $*${RST}"; }
warn() { echo -e "${YLW}⚠ $*${RST}"; }

# ── detect OS ────────────────────────────────────────────────────────────────
OS="$(uname -s)"
[[ "$OS" == "Linux" || "$OS" == "Darwin" ]] || die "Unsupported OS: $OS"

echo
echo -e "${BOLD}╔══════════════════════════════════════════╗${RST}"
echo -e "${BOLD}║       Miranda USB Prep — Berylize Labs   ║${RST}"
echo -e "${BOLD}╚══════════════════════════════════════════╝${RST}"
echo

# ── show connected drives ────────────────────────────────────────────────────
info "Connected drives:"
echo
if [[ "$OS" == "Linux" ]]; then
    lsblk -d -o NAME,SIZE,MODEL,TRAN,LABEL 2>/dev/null | grep -v "loop" || \
        lsblk -d -o NAME,SIZE,LABEL
elif [[ "$OS" == "Darwin" ]]; then
    diskutil list external physical 2>/dev/null || diskutil list
fi
echo

# ── get device from arg or prompt ────────────────────────────────────────────
if [[ "${1:-}" != "" ]]; then
    DEVICE="$1"
else
    read -rp "  Enter the USB device path (e.g. /dev/sdb or /dev/disk2): " DEVICE
fi

DEVICE="${DEVICE%/}"   # strip trailing slash
[[ "$DEVICE" =~ ^/dev/ ]] || die "Device must start with /dev/ — got: $DEVICE"
[[ -e "$DEVICE" ]]       || die "Device not found: $DEVICE"

# ── refuse to wipe the boot/root drive ───────────────────────────────────────
if [[ "$OS" == "Linux" ]]; then
    ROOT_DEV=$(lsblk -no PKNAME "$(findmnt -n -o SOURCE /)" 2>/dev/null | head -1)
    ROOT_DEV="/dev/${ROOT_DEV:-sda}"
    [[ "$DEVICE" == "$ROOT_DEV"* ]] && die "That looks like your boot drive ($ROOT_DEV). Aborting."
fi

# ── show what we found ────────────────────────────────────────────────────────
echo
warn "You selected: ${BOLD}$DEVICE${RST}"
echo
if [[ "$OS" == "Linux" ]]; then
    lsblk "$DEVICE" 2>/dev/null || true
elif [[ "$OS" == "Darwin" ]]; then
    diskutil info "$DEVICE" 2>/dev/null | grep -E "Device|Size|Content|Media Name" || true
fi
echo

# ── pick a label ─────────────────────────────────────────────────────────────
echo "  Label options:  1) BERYL-01   2) BERYL-02   3) BERYL-03   4) custom"
read -rp "  Pick label [1]: " LABEL_CHOICE
case "${LABEL_CHOICE:-1}" in
    1) LABEL="BERYL-01" ;;
    2) LABEL="BERYL-02" ;;
    3) LABEL="BERYL-03" ;;
    4) read -rp "  Custom label (max 11 chars, no spaces): " LABEL
       LABEL="${LABEL:0:11}" ;;
    *) LABEL="BERYL-01" ;;
esac

# ── final confirmation — must type device path ────────────────────────────────
echo
echo -e "${RED}${BOLD}══════════════════════════════════════════════════════${RST}"
echo -e "${RED}${BOLD}  THIS WILL PERMANENTLY DESTROY ALL DATA ON $DEVICE${RST}"
echo -e "${RED}${BOLD}  Label: $LABEL${RST}"
echo -e "${RED}${BOLD}══════════════════════════════════════════════════════${RST}"
echo
read -rp "  Type the full device path to confirm (or Ctrl-C to cancel): " CONFIRM

[[ "$CONFIRM" == "$DEVICE" ]] || die "Device path didn't match. Nothing written."

# ── unmount all partitions ────────────────────────────────────────────────────
info "Unmounting all partitions on $DEVICE..."
if [[ "$OS" == "Linux" ]]; then
    for part in "${DEVICE}"?*; do
        umount "$part" 2>/dev/null && echo "  unmounted $part" || true
    done
elif [[ "$OS" == "Darwin" ]]; then
    diskutil unmountDisk "$DEVICE" 2>/dev/null || true
fi

# ── wipe existing signatures ──────────────────────────────────────────────────
info "Clearing partition table and filesystem signatures..."
if [[ "$OS" == "Linux" ]]; then
    wipefs -a -f "$DEVICE" || die "wipefs failed — is it still mounted?"
elif [[ "$OS" == "Darwin" ]]; then
    diskutil zeroDisk "$DEVICE" 1m 2>/dev/null || true   # wipe first 1 MB
fi
sleep 1

# ── write new partition table + single exFAT partition ───────────────────────
info "Writing new GPT partition table..."
if [[ "$OS" == "Linux" ]]; then
    parted -s "$DEVICE" mklabel gpt          || die "parted mklabel failed"
    parted -s "$DEVICE" mkpart primary 1MiB 100% || die "parted mkpart failed"
    sleep 1

    PART="${DEVICE}1"
    # handle nvme naming (nvme0n1p1 vs sdb1)
    [[ -e "$PART" ]] || PART="${DEVICE}p1"
    [[ -e "$PART" ]] || die "Partition not found after parted. Try replugging."

    info "Formatting $PART as exFAT (label: $LABEL)..."
    if command -v mkfs.exfat >/dev/null 2>&1; then
        mkfs.exfat -n "$LABEL" "$PART" || die "mkfs.exfat failed"
    elif command -v mkexfatfs >/dev/null 2>&1; then
        mkexfatfs -n "$LABEL" "$PART" || die "mkexfatfs failed"
    else
        warn "exFAT tools not found. Installing exfatprogs..."
        sudo apt-get install -y -q exfatprogs 2>/dev/null || \
            sudo apt-get install -y -q exfat-utils 2>/dev/null || \
            die "Could not install exfat tools. Run: sudo apt install exfatprogs"
        mkfs.exfat -n "$LABEL" "$PART" || die "mkfs.exfat failed"
    fi

elif [[ "$OS" == "Darwin" ]]; then
    info "Formatting $DEVICE as exFAT (label: $LABEL) via diskutil..."
    diskutil eraseDisk ExFAT "$LABEL" GPT "$DEVICE" || die "diskutil eraseDisk failed"
fi

# ── verify ────────────────────────────────────────────────────────────────────
echo
ok "Done. $DEVICE is wiped and formatted."
echo
if [[ "$OS" == "Linux" ]]; then
    lsblk "$DEVICE" -o NAME,SIZE,FSTYPE,LABEL
elif [[ "$OS" == "Darwin" ]]; then
    diskutil info "${DEVICE}s1" 2>/dev/null | grep -E "Volume Name|File System|Disk Size" || \
        diskutil list "$DEVICE"
fi
echo
echo -e "  ${BOLD}Label  :${RST} $LABEL"
echo -e "  ${BOLD}Format :${RST} exFAT  (Mac / Windows / Linux — no driver needed)"
echo -e "  ${BOLD}Ready  :${RST} plug it into any machine and drop models straight on."
echo
info "Run this script again for the next USB (pick BERYL-02, BERYL-03)."
echo
