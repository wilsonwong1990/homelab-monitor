#!/usr/bin/env python3
"""Extract matched Casper boot assets and render credential-free LAN menus."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(data)
    return result.hexdigest()


def validate_url(url):
    parsed = urlsplit(url)
    if parsed.port is not None and not 0 < parsed.port <= 65535:
        raise ValueError("invalid LAN URL port")
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
            or parsed.netloc.endswith(":")
            or not re.fullmatch(r"https?://[A-Za-z0-9.:\[\]-]+(?:/[A-Za-z0-9._/-]*)?", url)
            or any(part in (".", "..") for part in parsed.path.split("/"))):
        raise ValueError("expected a plain HTTP(S) LAN URL without credentials, query, fragments or iPXE expressions")
    return url.rstrip("/")


def render_menu(base):
    base = validate_url(base)
    return f"#!ipxe\nset asset_base {base}\nchain ${{asset_base}}/homelab.ipxe || exit 1\n"


def verify_assets(directory):
    entries = {}
    for line in (directory / "PXE-SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (pxe-vmlinuz|pxe-initrd|homelab.ipxe)", line)
        if not match or match[2] in entries:
            raise ValueError("invalid PXE checksum manifest")
        entries[match[2]] = match[1]
    if set(entries) != {"pxe-vmlinuz", "pxe-initrd", "homelab.ipxe"}:
        raise ValueError("incomplete PXE assets")
    for name, sha in entries.items():
        if digest(directory / name) != sha:
            raise ValueError(f"PXE checksum mismatch: {name}")


def extract(iso, destination):
    destination.mkdir(parents=True, exist_ok=True)
    for source, name in (("vmlinuz", "pxe-vmlinuz"), ("initrd", "pxe-initrd")):
        subprocess.run(["xorriso", "-osirrox", "on", "-indev", str(iso),
                        "-extract", f"/casper/{source}", str(destination / name)], check=True)
        path = destination / name
        if not path.stat().st_size or path.stat().st_size >= 2 * 1024**3:
            raise ValueError("empty or oversized PXE asset")
    (destination / "homelab.ipxe").write_bytes(Path(__file__).with_name("homelab.ipxe").read_bytes())
    names = ("pxe-vmlinuz", "pxe-initrd", "homelab.ipxe")
    (destination / "PXE-SHA256SUMS").write_text(
        "".join(f"{digest(destination / name)}  {name}\n" for name in names))
    metadata = json.loads(Path(str(iso) + ".provenance.json").read_text())
    metadata["pxe"] = {name: digest(destination / name) for name in names}
    Path(str(iso) + ".provenance.json").write_text(json.dumps(metadata, indent=2) + "\n")
    verify_assets(destination)
    print("PASS: extracted and verified matched Casper kernel/initrd and iPXE menu")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("iso", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    extract(args.iso, args.destination)
