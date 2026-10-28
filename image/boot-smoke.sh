#!/usr/bin/env bash
# Boot the actual release ISO in an isolated VM, with no installation disk.
set -euo pipefail
ISO="${1:?Usage: boot-smoke.sh ISO bios|uefi LOG}"
MODE="${2:?firmware mode required}"
LOG="${3:?serial log required}"
firmware=()
case "$MODE" in
  bios) ;;
  uefi) firmware=(-bios /usr/share/OVMF/OVMF_CODE_4M.fd) ;;
  *) echo "ERROR: unknown firmware mode $MODE" >&2; exit 1 ;;
esac
mkdir -p "$(dirname "$LOG")"
accel=tcg
[[ -r /dev/kvm && -w /dev/kvm ]] && accel=kvm
qemu-system-x86_64 -accel "$accel" -m 4096 -smp 2 \
  "${firmware[@]}" -cdrom "$ISO" -boot d -display none \
  -serial "file:$LOG" -nic none -no-reboot &
pid=$!
trap 'kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true' EXIT
for ((second=0; second<600; second++)); do
  kill -0 "$pid" || { echo "ERROR: VM exited before live userspace" >&2; exit 1; }
  if grep -Eq 'systemd [0-9]+.*running in system mode' "$LOG"; then
    echo "PASS: $MODE boot reached live systemd userspace"
    exit 0
  fi
  sleep 1
done
echo "ERROR: $MODE boot did not reach live userspace within 600 seconds" >&2
tail -n 50 "$LOG"
exit 1
