#!/usr/bin/env bash
# Authenticate Ubuntu's release manifest without downloading multi-GB images.
set -euo pipefail
DEST="${1:?Usage: ubuntu-manifest.sh DIRECTORY}"
KEY=843938DF228D22F7B3742BC0D94AA3F0EFE21092
mkdir -p "$DEST"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
chmod 0700 "$WORK"
for file in SHA256SUMS SHA256SUMS.gpg; do
  curl --fail --location --retry 3 --proto '=https' --proto-redir '=https' \
    "https://releases.ubuntu.com/24.04/$file" -o "$DEST/$file"
done
gpg --batch --homedir "$WORK" --keyserver hkps://keyserver.ubuntu.com --recv-keys "$KEY"
gpg --batch --homedir "$WORK" --export "$KEY" > "$DEST/ubuntu-cdimage.gpg"
gpgv --keyring "$(cd "$DEST" && pwd)/ubuntu-cdimage.gpg" --status-fd 1 \
  "$DEST/SHA256SUMS.gpg" "$DEST/SHA256SUMS" > "$WORK/status"
grep -q "^\[GNUPG:\] VALIDSIG $KEY " "$WORK/status"
echo "PASS: authenticated Ubuntu 24.04 release manifest"
