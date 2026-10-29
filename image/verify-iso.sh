#!/usr/bin/env bash
# Inspect the completed artifact, not just its build inputs.
set -euo pipefail
ISO="${1:?Usage: verify-iso.sh ISO COMMIT}"
COMMIT="${2:?expected full source commit required}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
xorriso -indev "$ISO" -report_el_torito plain > "$WORK/boot.txt" 2>&1
grep -Eq 'El Torito boot img.*BIOS' "$WORK/boot.txt"
grep -Eq 'El Torito boot img.*UEFI' "$WORK/boot.txt"
for path in homelab autoinstall.yaml boot/grub/grub.cfg boot/grub/loopback.cfg md5sum.txt; do
  mkdir -p "$WORK/$(dirname "$path")"
  xorriso -osirrox on -indev "$ISO" -extract "/$path" "$WORK/$path"
done
test "$(cat "$WORK/homelab/ref")" = "$COMMIT"
git init -q "$WORK/repo"
git -C "$WORK/repo" bundle verify "$WORK/homelab/homelab-monitor.bundle"
git -C "$WORK/repo" fetch -q "$WORK/homelab/homelab-monitor.bundle" 'refs/heads/*:refs/remotes/image/*'
git -C "$WORK/repo" cat-file -e "$COMMIT^{commit}"
git -C "$WORK/repo" show "$COMMIT:image/autoinstall.yaml" > "$WORK/expected.yaml"
cmp "$WORK/expected.yaml" "$WORK/autoinstall.yaml"
for config in grub.cfg loopback.cfg; do
  grep -Eq 'linux[[:space:]]+/casper/vmlinuz.*autoinstall.*console=ttyS0' "$WORK/boot/grub/$config"
done
# The original Ubuntu manifest contains many large files; verify every modified entry here.
for path in boot/grub/grub.cfg boot/grub/loopback.cfg autoinstall.yaml homelab/ref homelab/homelab-monitor.bundle; do
  expected="$(awk -v f="./$path" '$2 == f {print $1}' "$WORK/md5sum.txt")"
  actual="$(if command -v md5sum >/dev/null; then md5sum "$WORK/$path" | cut -d' ' -f1; else md5 -q "$WORK/$path"; fi)"
  [[ "$expected" == "$actual" ]]
done
printf 'PASS: BIOS/UEFI catalog, pinned bundle, autoinstall and media checksums (%s)\n' "$COMMIT"
