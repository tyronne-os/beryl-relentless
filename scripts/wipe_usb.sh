#!/usr/bin/env bash
# scripts/wipe_usb.sh — wipe a USB drive and format it for model storage
#
# Usage:
#   bash scripts/wipe_usb.sh            # auto-detects the USB drive
#   bash scripts/wipe_usb.sh /dev/sdX   # specify device directly
#   bash scripts/wipe_usb.sh --watch    # wait for next insertion, then auto-run
#
# Format: exFAT — reads/writes on Mac, Windows, Linux, no driver needed.
# Labels: BERYL-01 / BERYL-02 / BERYL-03 (you pick at runtime).
# SAFE: nothing written until you confirm the device path.

set -uo pipefail

RED='\033[0;31m'; YLW='\033[0;33m'; GRN='\033[0;32m'
CYN='\033[0;36m'; RST='\033[0m'; BOLD='\033[1m'

die()  { echo -e "${RED}ERROR: $*${RST}" >&2; exit 1; }
info() { echo -e "${CYN}  ▸ $*${RST}"; }
ok()   { echo -e "${GRN}  ✓ $*${RST}"; }
warn() { echo -e "${YLW}  ⚠ $*${RST}"; }

OS="$(uname -s)"
[[ "$OS" == "Linux" || "$OS" == "Darwin" ]] || die "Unsupported OS: $OS"

banner() {
  echo
  echo -e "${BOLD}╔══════════════════════════════════════════╗${RST}"
  echo -e "${BOLD}║      Miranda USB Prep — Berylize Labs    ║${RST}"
  echo -e "${BOLD}╚══════════════════════════════════════════╝${RST}"
  echo
}

# ── find all USB block devices ────────────────────────────────────────────────
find_usb_devices() {
  lsblk -d -o NAME,TRAN 2>/dev/null | awk '$2=="usb"{print "/dev/"$1}'
}

# ── --watch: block until a new USB appears, then re-exec with it ──────────────
if [[ "${1:-}" == "--watch" ]]; then
  banner
  info "Watching for USB insertion — plug in the drive now…"
  BEFORE=$(find_usb_devices | sort)
  while true; do
    sleep 1
    AFTER=$(find_usb_devices | sort)
    if [[ "$AFTER" != "$BEFORE" ]]; then
      NEW=$(comm -13 <(echo "$BEFORE") <(echo "$AFTER") | head -1)
      [[ -n "$NEW" ]] || continue
      echo
      info "Drive detected: ${BOLD}$NEW${RST}"
      sleep 1
      exec sudo "$0" "$NEW"
    fi
  done
fi

banner

# ── resolve device ────────────────────────────────────────────────────────────
if [[ "${1:-}" =~ ^/dev/ ]]; then
  DEVICE="$1"
else
  # auto-detect
  mapfile -t USB_DEVS < <(find_usb_devices)
  if [[ ${#USB_DEVS[@]} -eq 1 ]]; then
    DEVICE="${USB_DEVS[0]}"
    MODEL=$(lsblk -d -o MODEL "$DEVICE" 2>/dev/null | tail -1 | xargs)
    SIZE=$(lsblk  -d -o SIZE  "$DEVICE" 2>/dev/null | tail -1 | xargs)
    info "Auto-detected: ${BOLD}$DEVICE${RST}  $SIZE  $MODEL"
    read -rp "  Use $DEVICE? [Y/n]: " YN
    [[ "${YN:-Y}" =~ ^[Yy]$ ]] || read -rp "  Enter device path: " DEVICE
  elif [[ ${#USB_DEVS[@]} -gt 1 ]]; then
    info "Multiple USB drives found:"
    lsblk -d -o NAME,SIZE,MODEL,TRAN 2>/dev/null | grep usb
    echo
    read -rp "  Enter device path (e.g. /dev/sdb): " DEVICE
  else
    info "No USB drive detected. All connected drives:"
    lsblk -d -o NAME,SIZE,MODEL,TRAN 2>/dev/null | grep -v loop
    echo
    read -rp "  Enter device path (e.g. /dev/sdb): " DEVICE
  fi
fi

DEVICE="${DEVICE%/}"
[[ "$DEVICE" =~ ^/dev/ ]] || die "Device must start with /dev/ — got: $DEVICE"
[[ -b "$DEVICE" ]]         || die "Not a block device: $DEVICE"

# ── safety: refuse root/boot drive ───────────────────────────────────────────
if [[ "$OS" == "Linux" ]]; then
  ROOT_PART=$(findmnt -n -o SOURCE / 2>/dev/null || echo "")
  ROOT_DISK=$(lsblk -no PKNAME "$ROOT_PART" 2>/dev/null | head -1)
  [[ -n "$ROOT_DISK" && "$DEVICE" == "/dev/${ROOT_DISK}"* ]] && \
    die "That's your boot drive (/dev/$ROOT_DISK). Aborting."
fi

# ── show device details ───────────────────────────────────────────────────────
echo
warn "Selected: ${BOLD}$DEVICE${RST}"
echo
if [[ "$OS" == "Linux" ]]; then
  lsblk "$DEVICE" -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINT 2>/dev/null || true
elif [[ "$OS" == "Darwin" ]]; then
  diskutil info "$DEVICE" 2>/dev/null | grep -E "Device|Size|Content|Volume" || true
fi
echo

# ── pick label ────────────────────────────────────────────────────────────────
echo -e "  Label:  1) BERYL-01   2) BERYL-02   3) BERYL-03   4) custom"
read -rp "  Pick [1]: " LC
case "${LC:-1}" in
  1) LABEL="BERYL-01" ;;
  2) LABEL="BERYL-02" ;;
  3) LABEL="BERYL-03" ;;
  4) read -rp "  Label (max 11 chars, no spaces): " LABEL; LABEL="${LABEL:0:11}" ;;
  *) LABEL="BERYL-01" ;;
esac

# ── final confirmation ────────────────────────────────────────────────────────
echo
echo -e "${RED}${BOLD}  !! ALL DATA ON $DEVICE WILL BE PERMANENTLY DESTROYED !!${RST}"
echo -e "${RED}${BOLD}  Label will be: $LABEL${RST}"
echo
read -rp "  Type the full device path to confirm (Ctrl-C to cancel): " CONFIRM
[[ "$CONFIRM" == "$DEVICE" ]] || die "Path didn't match. Nothing written."

# ── unmount ───────────────────────────────────────────────────────────────────
info "Unmounting…"
if [[ "$OS" == "Linux" ]]; then
  for p in "${DEVICE}"?* "${DEVICE}"p?*; do
    [[ -b "$p" ]] && umount "$p" 2>/dev/null && echo "    unmounted $p" || true
  done
elif [[ "$OS" == "Darwin" ]]; then
  diskutil unmountDisk "$DEVICE" 2>/dev/null || true
fi

# ── wipe ──────────────────────────────────────────────────────────────────────
info "Wiping partition table and filesystem signatures…"
if [[ "$OS" == "Linux" ]]; then
  wipefs -a -f "$DEVICE" || die "wipefs failed — is it still mounted?"
elif [[ "$OS" == "Darwin" ]]; then
  dd if=/dev/zero of="$DEVICE" bs=1m count=2 2>/dev/null || true
fi
sleep 1

# ── partition + format ────────────────────────────────────────────────────────
info "Writing new GPT + exFAT partition (label: $LABEL)…"
if [[ "$OS" == "Linux" ]]; then
  parted -s "$DEVICE" mklabel gpt         || die "parted mklabel failed"
  parted -s "$DEVICE" mkpart primary 1MiB 100% || die "parted mkpart failed"
  sleep 2

  PART="${DEVICE}1"
  [[ -b "$PART" ]] || PART="${DEVICE}p1"
  [[ -b "$PART" ]] || die "Partition not found after parted — try replugging."

  if ! command -v mkfs.exfat &>/dev/null && ! command -v mkexfatfs &>/dev/null; then
    info "Installing exfatprogs…"
    sudo apt-get install -y -q exfatprogs 2>/dev/null || \
      sudo apt-get install -y -q exfat-utils 2>/dev/null || \
      die "Could not install exfat tools. Run: sudo apt install exfatprogs"
  fi

  if command -v mkfs.exfat &>/dev/null; then
    mkfs.exfat -n "$LABEL" "$PART" || die "mkfs.exfat failed"
  else
    mkexfatfs -n "$LABEL" "$PART"  || die "mkexfatfs failed"
  fi

elif [[ "$OS" == "Darwin" ]]; then
  diskutil eraseDisk ExFAT "$LABEL" GPT "$DEVICE" || die "diskutil eraseDisk failed"
fi

# ── verify ────────────────────────────────────────────────────────────────────
echo
ok "Done. $DEVICE wiped and formatted."
echo
if [[ "$OS" == "Linux" ]]; then
  lsblk "$DEVICE" -o NAME,SIZE,FSTYPE,LABEL
elif [[ "$OS" == "Darwin" ]]; then
  diskutil list "$DEVICE"
fi
echo
echo -e "  ${BOLD}Label  :${RST} $LABEL"
echo -e "  ${BOLD}Format :${RST} exFAT  (Mac / Windows / Linux, no driver needed)"
echo -e "  ${BOLD}Ready  :${RST} drop models straight onto it from any machine."
echo
info "For the next USB: sudo bash scripts/wipe_usb.sh --watch"
echo
