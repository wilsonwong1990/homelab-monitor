#!/usr/bin/env python3
"""Select Ubuntu 24.04 Desktop amd64 images from an authenticated manifest."""
import argparse
import json
import re
from pathlib import Path

VERSION = r"24\.04\.(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))?"
IMAGE = re.compile(rf"ubuntu-({VERSION})-desktop-amd64\.iso")


def select_image(text, version="latest"):
    images = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 2:
            raise ValueError("invalid Ubuntu checksum line")
        sha, filename = fields
        match = IMAGE.fullmatch(filename.lstrip("*"))
        if not match:
            continue
        if not re.fullmatch(r"[0-9a-f]{64}", sha) or match[1] in images:
            raise ValueError("invalid or duplicate Ubuntu desktop digest")
        images[match[1]] = sha
    if not images:
        raise ValueError("no supported Ubuntu 24.04 Desktop amd64 image")
    if version == "latest":
        version = max(images, key=lambda value: tuple(map(int, value.split("."))))
    if version not in images:
        raise ValueError("requested point release absent from authenticated manifest")
    return version, images[version]


def release_tag(version, commit, digest, validation=False):
    if not re.fullmatch(VERSION, version) or not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid release identity")
    prefix = "image-validation-ubuntu" if validation else "ubuntu"
    return f"{prefix}-{version}-{commit[:12]}-{digest[:12]}"


def published_release(releases, tag, commit):
    matches = [release for page in releases for release in page if release["tag_name"] == tag]
    if len(matches) > 1:
        raise ValueError("duplicate release identity")
    if not matches or matches[0]["draft"]:
        return False
    release = matches[0]
    if release["target_commitish"] != commit:
        raise ValueError("published Ubuntu release targets a different source")
    assets = {asset["name"] for asset in release["assets"] if asset["size"] > 0 and asset["state"] == "uploaded"}
    required = {"provenance.json", "SHA256SUMS", "assemble.py", "pxe-vmlinuz", "pxe-initrd",
                "PXE-SHA256SUMS", "homelab.ipxe", "INSTALL.md", "NETBOOT.md", "BUILD.txt"}
    parts = sorted(name for name in assets if re.fullmatch(rf"homelab-monitor-{commit[:12]}\.iso\.part[0-9]{{3}}", name))
    if not required <= assets or not parts or parts != [f"homelab-monitor-{commit[:12]}.iso.part{index:03d}" for index in range(len(parts))]:
        raise ValueError("published Ubuntu release is missing required assets; inspect it rather than silently skipping")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--version", default="latest")
    parser.add_argument("--commit")
    parser.add_argument("--validation", action="store_true")
    parser.add_argument("--published", type=Path, help="check the paginated GitHub releases response for this identity")
    args = parser.parse_args()
    version, sha = select_image(args.manifest.read_text(), args.version)
    print(f"version={version}\nsha256={sha}")
    if args.commit:
        tag = release_tag(version, args.commit, sha, args.validation)
        print(f"tag={tag}")
        if args.published:
            found = published_release(json.loads(args.published.read_text()), tag, args.commit)
            print(f"needed={'false' if found else 'true'}")
    elif args.published:
        parser.error("--published requires --commit")
