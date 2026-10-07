#!/usr/bin/env bash
# scripts/usb_nuke.sh — one command, no prompts: wipe + exFAT-format USB sticks.
#
#   sudo bash scripts/usb_nuke.sh            # plug stick in, it does everything;
#                                            # then swap in the next stick (3 total)
#   sudo bash scripts/usb_nuke.sh 1          # just one stick
#   sudo bash scripts/usb_nuke.sh 3 5        # 3 sticks, 5 s abort countdown
#
# Only ever touches drives the kernel reports as TRAN=usb, never the disk
# holding / or /boot. Labels: BERYL-01, BERYL-02, BERYL-03.
set -uo pipefail

[[ $EUID -eq 0 ]] || exec sudo bash "$0" "$@"

COUNT="${1:-3}"
COUNTDOWN="${2:-4}"

R=$'\033[0;31m'; G=$'\033[0;32m'; C=$'\033[0;36m'; B=$'\033[1m'; N=$'\033[0m'
say()  { echo "${C}▸ $*${N}"; }
ok()   { echo "${G}✓ $*${N}"; }
fail() { echo "${R}✗ $*${N}" >&2; }

# ── tools ────────────────────────────────────────────────────────────────────
need_pkgs=()
command -v mkfs.exfat >/dev/null || need_pkgs+=(exfatprogs)
command -v parted      >/dev/null || need_pkgs+=(parted)
command -v wipefs      >/dev/null || need_pkgs+=(util-linux)
if ((${#need_pkgs[@]})); then
  say "installing ${need_pkgs[*]}…"
  apt-get install -y -q "${need_pkgs[@]}" >/dev/null 2>&1 || {
    fail "could not install ${need_pkgs[*]} (need internet once)"; exit 1; }
fi

# ── helpers ──────────────────────────────────────────────────────────────────
boot_disks() {  # disks backing / and /boot — never touch these
  for m in / /boot /boot/efi; do
    src=$(findmnt -n -o SOURCE "$m" 2>/dev/null) || continue
    [[ -b "$src" ]] || continue
    lsblk -no PKNAME "$src" 2>/dev/null | head -1
  done | sort -u
}

usb_disks() {   # whole-disk USB devices (not partitions), real size only
  lsblk -dnb -o NAME,TRAN,SIZE,TYPE 2>/dev/null |
    awk '$2=="usb" && $4=="disk" && $3>0 {print $1}'
}

wait_for_stick() {
  local skip="$BOOT"
  echo
  say "Plug in USB stick #$1 now…  (waiting)"
  while true; do
    for d in $(usb_disks); do
      grep -qxF "$d" <<<"$skip" && continue
      echo "$d"; return 0
    done
    sleep 1
  done
}

wait_for_removal() {
  say "Unplug it when you see the next prompt…"
  while lsblk -dn -o NAME 2>/dev/null | grep -qx "$1"; do sleep 1; done
}

nuke() {
  local dev="/dev/$1" label="$2" part
  local size model
  size=$(lsblk -dn -o SIZE "$dev" | xargs)
  model=$(lsblk -dn -o MODEL "$dev" | xargs)

  echo
  echo "${B}  Target : $dev  ($size  $model)${N}"
  lsblk "$dev" -o NAME,SIZE,FSTYPE,LABEL | sed 's/^/    /'
  echo "${R}${B}  ALL DATA ON $dev DESTROYED in $COUNTDOWN s — Ctrl-C to abort${N}"
  for ((i=COUNTDOWN; i>0; i--)); do printf '    %s…\r' "$i"; sleep 1; done
  echo

  say "unmounting…"
  for p in $(lsblk -lnp -o NAME "$dev" | tail -n +2) "$dev"; do
    umount -l "$p" 2>/dev/null || true
  done
  udisksctl power-on -b "$dev" >/dev/null 2>&1 || true

  say "wiping signatures and partition tables…"
  for p in $(lsblk -lnp -o NAME "$dev" | tail -n +2); do wipefs -a -f "$p" >/dev/null 2>&1 || true; done
  wipefs -a -f "$dev" >/dev/null || { fail "wipefs failed on $dev"; return 1; }
  command -v sgdisk >/dev/null && sgdisk --zap-all "$dev" >/dev/null 2>&1 || true

  say "zeroing first and last 16 MiB (kills ISO boot sectors + GPT backup)…"
  dd if=/dev/zero of="$dev" bs=1M count=16 conv=fsync status=none
  local sectors; sectors=$(blockdev --getsz "$dev")
  local tail_start=$(( sectors / 2048 - 16 ))
  ((tail_start > 16)) && dd if=/dev/zero of="$dev" bs=1M seek="$tail_start" count=16 conv=fsync status=none 2>/dev/null
  sync

  say "creating GPT + one exFAT partition…"
  partprobe "$dev" 2>/dev/null; udevadm settle
  parted -s "$dev" mklabel gpt mkpart "$label" 1MiB 100% || { fail "parted failed"; return 1; }
  partprobe "$dev" 2>/dev/null; udevadm settle; sleep 2

  part="${dev}1"; [[ -b "$part" ]] || part="${dev}p1"
  [[ -b "$part" ]] || { fail "partition node missing for $dev"; return 1; }
  umount -l "$part" 2>/dev/null || true

  mkfs.exfat -n "$label" "$part" >/dev/null || { fail "mkfs.exfat failed"; return 1; }
  sync; udevadm settle

  echo
  lsblk "$dev" -o NAME,SIZE,FSTYPE,LABEL | sed 's/^/    /'
  local fs; fs=$(lsblk -dn -o FSTYPE "$part" | xargs)
  [[ "$fs" == "exfat" ]] || { fail "verify failed: fstype='$fs'"; return 1; }
  ok "$label ready  ($dev, exFAT) — safe to unplug"
}

# ── main ─────────────────────────────────────────────────────────────────────
BOOT="$(boot_disks)"
echo "${B}Miranda USB nuke — $COUNT stick(s), exFAT, labels BERYL-01…${N}"
echo "  boot disk(s) protected: ${BOOT:-none found}"

done_n=0
for ((n=1; n<=COUNT; n++)); do
  label=$(printf 'BERYL-%02d' "$n")
  name=$(wait_for_stick "$n")
  if nuke "$name" "$label"; then done_n=$((done_n+1)); else fail "stick #$n failed — continuing"; fi
  ((n<COUNT)) && wait_for_removal "$name"
done

echo
ok "finished: $done_n/$COUNT sticks wiped and labelled"
