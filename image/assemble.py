#!/usr/bin/env python3
"""Verify release parts and reconstruct a USB-writeable ISO (Python 3.9+)."""
import argparse
import hashlib
import re
from pathlib import Path


def assemble(directory, check_only=False):
    entries = {}
    for line in (directory / "SHA256SUMS").read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (homelab-monitor-[0-9a-f]{12}\.iso(?:\.part[0-9]{3})?)", line)
        if not match or match[2] in entries:
            raise ValueError("invalid or duplicate checksum entry")
        entries[match[2]] = match[1]
    images = [name for name in entries if name.endswith(".iso")]
    if len(images) != 1:
        raise ValueError("expected exactly one image checksum")
    image = images[0]
    parts = sorted(name for name in entries if name.startswith(image + ".part"))
    if not parts or len(entries) != len(parts) + 1:
        raise ValueError("missing parts or unexpected entries")
    if parts != [f"{image}.part{index:03d}" for index in range(len(parts))]:
        raise ValueError("non-contiguous release parts")
    output = directory / image
    temporary = directory / f"{image}.assembling"
    if not check_only and output.exists():
        raise FileExistsError(output)
    combined = hashlib.sha256()
    stream = None if check_only else temporary.open("xb")
    try:
        for name in parts:
            part = hashlib.sha256()
            with (directory / name).open("rb") as source:
                for data in iter(lambda: source.read(1024 * 1024), b""):
                    part.update(data)
                    combined.update(data)
                    if stream:
                        stream.write(data)
            if part.hexdigest() != entries[name]:
                raise ValueError(f"checksum mismatch: {name}")
        if combined.hexdigest() != entries[image]:
            raise ValueError("reassembled ISO checksum mismatch")
        if stream:
            stream.close()
            temporary.rename(output)
    except BaseException:
        if stream:
            stream.close()
            temporary.unlink()
        raise
    return image, combined.hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=Path("."))
    parser.add_argument("--check-only", action="store_true", help="verify concatenated bytes without writing an ISO")
    args = parser.parse_args()
    name, checksum = assemble(args.directory, args.check_only)
    print(f"PASS: all parts and complete ISO verified: {name} sha256={checksum}")
