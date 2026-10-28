#!/usr/bin/env bash
# Fetch a specific Desktop image; authenticate the manifest before downloading it.
set -euo pipefail
DEST="${1:-build/upstream}"
VERSION="${2:-latest}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE=https://releases.ubuntu.com/24.04
bash "$ROOT/ubuntu-manifest.sh" "$DEST"
selection="$(python3 "$ROOT/ubuntu_release.py" "$DEST/SHA256SUMS" --version "$VERSION")"
VERSION="$(sed -n 's/^version=//p' <<< "$selection")"
expected="$(sed -n 's/^sha256=//p' <<< "$selection")"
filename="ubuntu-$VERSION-desktop-amd64.iso"
curl --fail --location --retry 3 --proto '=https' --proto-redir '=https' \
  "$BASE/$filename" -o "$DEST/$filename"
actual="$(shasum -a 256 "$DEST/$filename" | cut -d' ' -f1)"
[[ "$actual" == "$expected" ]] || { echo "ERROR: Ubuntu image checksum mismatch" >&2; exit 1; }
printf 'PASS: authenticated Ubuntu image %s (%s)\n' "$filename" "$actual"
