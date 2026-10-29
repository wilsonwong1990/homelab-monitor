#!/usr/bin/env bash
# Atomic remote tag creation: no force push, local tag mutation, or shared lock.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMMIT="${1:?Usage: tag-release.sh FULL_COMMIT}"
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo "ERROR: expected full commit SHA" >&2; exit 1; }
git cat-file -e "$COMMIT^{commit}"
for ((attempt=1; attempt<=10; attempt++)); do
  remote="$(git ls-remote --tags origin)"
  decision="$(printf '%s\n' "$remote" | python3 "$ROOT/scripts/next-version.py" "$COMMIT")"
  read -r version state <<< "$decision"
  if [[ "$state" == existing ]]; then
    echo "Reusing $version for $COMMIT" >&2
    printf '%s\n' "$version"
    exit 0
  fi
  if git push origin "$COMMIT:refs/tags/$version" >&2; then
    echo "Created $version for $COMMIT" >&2
    printf '%s\n' "$version"
    exit 0
  fi
  echo "Tag push failed (possible concurrent merge); refreshing remote tags, attempt $attempt/10" >&2
  sleep 2
done
echo "ERROR: unable to assign a release tag after 10 attempts; inspect repository permissions and remote tags" >&2
exit 1
