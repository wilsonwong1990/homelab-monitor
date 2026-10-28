#!/usr/bin/env bash
# Build a homelab monitoring PC installer from a stock Ubuntu 24.04 Desktop ISO.
# Adds autoinstall.yaml and a pinned git bundle of this repository; the boot
# records are replayed unchanged. Runs on Linux or macOS with xorriso and git.
set -euo pipefail

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REF=HEAD
ISO=""
OUT=""
KEYRING=""
KEY=843938DF228D22F7B3742BC0D94AA3F0EFE21092

usage() {
  cat <<'EOF'
Usage: ./build-iso.sh --iso ubuntu-24.04.x-desktop-amd64.iso [options]

  --iso FILE      Stock Ubuntu Desktop 24.04 LTS ISO (required)
  --ref REF       Commit/tag/branch to embed (default: HEAD; must be committed)
  --source DIR    Repository checkout (default: this repository)
  --out FILE      Output ISO (default: homelab-monitor-<commit>.iso)
  --keyring FILE  Ubuntu keyring (default: beside ISO, ubuntu-cdimage.gpg)
  -h, --help      Show this help

Signed SHA256SUMS beside the ISO is required; use image/download-ubuntu.sh.
Requires xorriso, git, Python 3, gpgv, and sha256sum (or shasum).
EOF
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --iso) ISO="${2:?--iso needs a value}"; shift 2 ;;
    --ref) REF="${2:?--ref needs a value}"; shift 2 ;;
    --source) SOURCE="${2:?--source needs a value}"; shift 2 ;;
    --out) OUT="${2:?--out needs a value}"; shift 2 ;;
    --keyring) KEYRING="${2:?--keyring needs a value}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
done

[[ -n "$ISO" ]] || { usage >&2; die "--iso is required"; }
[[ -f "$ISO" ]] || die "$ISO not found"
command -v xorriso >/dev/null || die "xorriso is required"
command -v git >/dev/null || die "git is required"
command -v gpgv >/dev/null || die "gpgv is required"
command -v python3 >/dev/null || die "python3 is required"

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }

sums="$(dirname "$ISO")/SHA256SUMS"
KEYRING="${KEYRING:-$(dirname "$ISO")/ubuntu-cdimage.gpg}"
KEYRING="$(cd "$(dirname "$KEYRING")" && pwd)/$(basename "$KEYRING")"
[[ -f "$sums" && -f "$sums.gpg" && -f "$KEYRING" ]] || die "signed SHA256SUMS and Ubuntu keyring are required"
signature="$(gpgv --keyring "$KEYRING" --status-fd 1 "$sums.gpg" "$sums")" || die "Ubuntu checksum signature verification failed"
grep -q "^\[GNUPG:\] VALIDSIG $KEY " <<< "$signature" || die "unexpected Ubuntu signing key"
[[ "$(basename "$ISO")" =~ ^ubuntu-24\.04\.[0-9]+(\.[0-9]+)?-desktop-amd64\.iso$ ]] || die "only Ubuntu 24.04 Desktop amd64 is supported"
if [[ -f "$sums" ]]; then
  expected="$(awk -v f="$(basename "$ISO")" '{n=$2; sub(/^\*/, "", n)} n == f {print $1}' "$sums")"
  [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || die "$(basename "$ISO") must occur exactly once in $sums"
  [[ "$(sha256 "$ISO")" == "$expected" ]] || die "checksum mismatch for $ISO"
  echo "==> verified $(basename "$ISO") against SHA256SUMS"
fi

COMMIT="$(git -C "$SOURCE" rev-parse --verify "${REF}^{commit}")" || die "cannot resolve $REF"
SHORT="${COMMIT:0:12}"
OUT="${OUT:-homelab-monitor-$SHORT.iso}"
[[ -e "$OUT" ]] && die "$OUT already exists"
[[ -e "$OUT.provenance.json" ]] && die "$OUT.provenance.json already exists"
if [[ -n "$(git -C "$SOURCE" status --porcelain --untracked-files=no)" ]]; then
  echo "WARN: uncommitted changes are NOT included; embedding commit $SHORT" >&2
fi

WORK="$(mktemp -d)"
TMP_REF="refs/heads/homelab-image-$$"
SUCCESS=0
cleanup() {
  git -C "$SOURCE" update-ref -d "$TMP_REF" 2>/dev/null || true
  rm -rf "$WORK"
  if [[ $SUCCESS -eq 0 ]]; then rm -f "$OUT" "$OUT.provenance.json"; fi
}
trap cleanup EXIT

mkdir -p "$WORK/homelab"
git -C "$SOURCE" update-ref "$TMP_REF" "$COMMIT"
git -C "$SOURCE" bundle create --quiet "$WORK/homelab/homelab-monitor.bundle" "$TMP_REF"
git -C "$SOURCE" bundle verify --quiet "$WORK/homelab/homelab-monitor.bundle"
printf '%s\n' "$COMMIT" > "$WORK/homelab/ref"
# Use the autoinstall file from the embedded commit, not the working tree.
git -C "$SOURCE" show "$COMMIT:image/autoinstall.yaml" > "$WORK/autoinstall.yaml"
mkdir -p "$WORK/boot/grub"
for config in grub.cfg loopback.cfg; do
  xorriso -osirrox on -indev "$ISO" -extract "/boot/grub/$config" "$WORK/boot/grub/$config"
  python3 "$SOURCE/image/iso_tools.py" boot "$WORK/boot/grub/$config"
done
xorriso -osirrox on -indev "$ISO" -extract /md5sum.txt "$WORK/md5sum.txt"
python3 "$SOURCE/image/iso_tools.py" media-sums "$WORK"

xorriso -indev "$ISO" -outdev "$OUT" \
  -map "$WORK/autoinstall.yaml" /autoinstall.yaml \
  -map "$WORK/homelab" /homelab \
  -map "$WORK/boot/grub/grub.cfg" /boot/grub/grub.cfg \
  -map "$WORK/boot/grub/loopback.cfg" /boot/grub/loopback.cfg \
  -map "$WORK/md5sum.txt" /md5sum.txt \
  -boot_image any replay

bash "$SOURCE/image/verify-iso.sh" "$OUT" "$COMMIT"
python3 "$SOURCE/image/iso_tools.py" provenance "$OUT" "$COMMIT" "$ISO" "$KEY" > "$OUT.provenance.json"
SUCCESS=1
echo "==> wrote $OUT (commit $SHORT, sha256 $(sha256 "$OUT"))"
echo "Write it to a USB stick (balenaEtcher, 'dd', or Rufus in DD mode) and boot the PC from it."
