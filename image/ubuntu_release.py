#!/usr/bin/env python3
"""Select Ubuntu 24.04 Desktop amd64 images from an authenticated manifest."""
import argparse
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--version", default="latest")
    parser.add_argument("--commit")
    parser.add_argument("--validation", action="store_true")
    args = parser.parse_args()
    version, sha = select_image(args.manifest.read_text(), args.version)
    print(f"version={version}\nsha256={sha}")
    if args.commit:
        print(f"tag={release_tag(version, args.commit, sha, args.validation)}")
