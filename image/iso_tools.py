#!/usr/bin/env python3
"""Boot configuration, media checksums and release transport helpers."""
import argparse
import hashlib
import json
import re
from pathlib import Path


def configure_boot(text):
    count = 0
    lines = []
    for line in text.splitlines(keepends=True):
        if re.match(r"\s*linux\s+/casper/vmlinuz(?:\s|$)", line):
            line = re.sub(r"\s+(quiet|splash)(?=\s|$)", "", line.rstrip("\n"))
            args = "autoinstall console=tty0 console=ttyS0,115200n8 systemd.log_target=console"
            line = line.replace(" ---", f" {args} ---") if " ---" in line else f"{line} {args}"
            line += "\n"
            count += 1
        lines.append(line)
    if not count:
        raise ValueError("no Ubuntu casper kernel entry found")
    return "".join(lines)


def digest(path, algorithm="sha256"):
    result = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def update_media_sums(root):
    sums = root / "md5sum.txt"
    entries = {}
    for line in sums.read_text().splitlines():
        checksum, name = line.split(maxsplit=1)
        entries[name.lstrip("*").removeprefix("./")] = checksum
    for name in ("boot/grub/grub.cfg", "boot/grub/loopback.cfg", "autoinstall.yaml",
                 "homelab/ref", "homelab/homelab-monitor.bundle"):
        entries[name] = digest(root / name, "md5")
    sums.write_text("".join(f"{checksum}  ./{name}\n" for name, checksum in sorted(entries.items())))


def split_image(image, destination, chunk_size=1900 * 1024 * 1024):
    if not 0 < chunk_size < 2 * 1024**3:
        raise ValueError("release parts must be smaller than GitHub's 2 GiB limit")
    destination.mkdir(parents=True, exist_ok=True)
    names = []
    with image.open("rb") as source:
        index = 0
        while True:
            data = source.read(min(chunk_size, 1024 * 1024))
            if not data:
                break
            name = f"{image.name}.part{index:03d}"
            path = destination / name
            with path.open("xb") as target:
                remaining = chunk_size
                while data:
                    target.write(data)
                    remaining -= len(data)
                    if not remaining:
                        break
                    data = source.read(min(remaining, 1024 * 1024))
            names.append((name, digest(path)))
            index += 1
    if not names:
        raise ValueError("empty image")
    sums = [(image.name, digest(image))] + names
    (destination / "SHA256SUMS").write_text(
        "".join(f"{sha}  {name}\n" for name, sha in sums), encoding="ascii"
    )
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("boot", "media-sums"):
        sub = commands.add_parser(command)
        sub.add_argument("path", type=Path)
    split = commands.add_parser("split")
    split.add_argument("image", type=Path)
    split.add_argument("destination", type=Path)
    provenance = commands.add_parser("provenance")
    provenance.add_argument("image", type=Path)
    provenance.add_argument("commit")
    provenance.add_argument("upstream", type=Path)
    provenance.add_argument("fingerprint")
    args = parser.parse_args()
    if args.command == "boot":
        args.path.write_text(configure_boot(args.path.read_text()))
    elif args.command == "media-sums":
        update_media_sums(args.path)
    elif args.command == "split":
        split_image(args.image, args.destination)
    else:
        print(json.dumps({
            "schema": 1,
            "source_commit": args.commit,
            "image": args.image.name,
            "image_sha256": digest(args.image),
            "upstream_image": args.upstream.name,
            "upstream_sha256": digest(args.upstream),
            "ubuntu_signing_fingerprint": args.fingerprint,
            "note": "Source is pinned; apt, PyPI, Ollama and models download during installation.",
        }, indent=2))


if __name__ == "__main__":
    main()
