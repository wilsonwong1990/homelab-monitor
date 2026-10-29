#!/usr/bin/env python3
"""Choose an idempotent patch release from `git ls-remote --tags` on stdin."""
import re
import sys

VERSION = re.compile(r"refs/tags/v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(\^\{\})?")


def choose_version(remote, commit):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("expected a full SHA-1 commit")
    tags = {}
    peeled = {}
    for line in remote.splitlines():
        if not line.strip():
            continue
        sha, ref = line.split()
        match = VERSION.fullmatch(ref)
        if match:
            if not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise ValueError("invalid remote tag object")
            version = tuple(int(match[index]) for index in (1, 2, 3))
            (peeled if match[4] else tags)[version] = sha
    tags.update(peeled)
    existing = [version for version, sha in tags.items() if sha == commit]
    if existing:
        version = max(existing)
        state = "existing"
    elif tags:
        major, minor, patch = max(tags)
        version = major, minor, patch + 1
        state = "new"
    else:
        version = 0, 1, 0
        state = "new"
    return "v" + ".".join(map(str, version)), state


if __name__ == "__main__":
    print(*choose_version(sys.stdin.read(), sys.argv[1]))
