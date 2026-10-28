#!/usr/bin/env bash
# Fetch a specific Desktop image; authenticate the manifest before downloading it.
set -euo pipefail
DEST="${1:-build/upstream}"
VERSION="${2:-24.04.5.1}"
KEY=843938DF228D22F7B3742BC0D94AA3F0EFE21092
[[ "$VERSION" =~ ^24\.04\.[0-9]+(\.[0-9]+)?$ ]] || { echo "ERROR: expected 24.04 point release" >&2; exit 1; }
BASE=https://releases.ubuntu.com/24.04
mkdir -p "$DEST"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
chmod 0700 "$WORK"
for file in SHA256SUMS SHA256SUMS.gpg; do
  curl --fail --location --retry 3 --proto '=https' --proto-redir '=https' \
    "$BASE/$file" -o "$DEST/$file"
done
gpg --batch --homedir "$WORK" --keyserver hkps://keyserver.ubuntu.com --recv-keys "$KEY"
gpg --batch --homedir "$WORK" --export "$KEY" > "$DEST/ubuntu-cdimage.gpg"
gpgv --keyring "$(cd "$DEST" && pwd)/ubuntu-cdimage.gpg" --status-fd 1 \
  "$DEST/SHA256SUMS.gpg" "$DEST/SHA256SUMS" > "$WORK/status"
grep -q "^\[GNUPG:\] VALIDSIG $KEY " "$WORK/status"
filename="ubuntu-$VERSION-desktop-amd64.iso"
expected="$(awk -v file="$filename" '$2 == "*"file || $2 == file { print $1 }' "$DEST/SHA256SUMS")"
[[ "$expected" =~ ^[0-9a-f]{64}$ ]] || { echo "ERROR: image absent from signed manifest" >&2; exit 1; }
curl --fail --location --retry 3 --proto '=https' --proto-redir '=https' \
  "$BASE/$filename" -o "$DEST/$filename"
actual="$(shasum -a 256 "$DEST/$filename" | cut -d' ' -f1)"
[[ "$actual" == "$expected" ]] || { echo "ERROR: Ubuntu image checksum mismatch" >&2; exit 1; }
printf 'PASS: authenticated Ubuntu image %s (%s)\n' "$filename" "$actual"
