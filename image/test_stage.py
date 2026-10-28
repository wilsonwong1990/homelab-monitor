#!/usr/bin/env python3
"""Verify real release assets through LAN-style staging and local HTTP, without PXE."""
import argparse
import functools
import hashlib
import http.server
import tempfile
import threading
import urllib.request
from pathlib import Path

from stage_netboot import stage, verify_release
from pxe import digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assets", type=Path)
    args = parser.parse_args()
    image, _ = verify_release(args.assets)
    if (args.assets / image).exists():
        raise FileExistsError("HTTP smoke test requires parts only, not a preexisting reconstructed ISO")
    try:
        with tempfile.TemporaryDirectory(prefix="homelab-http-test-") as temp:
            root = Path(temp)
            handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                stage(args.assets, root, base, "ci")
                stage(args.assets, root, base, "ci")
                for name in ("vmlinuz", "initrd", "installer.iso", "homelab.ipxe"):
                    url = f"{base}/homelab/ci/{name}"
                    with urllib.request.urlopen(urllib.request.Request(url, method="HEAD")) as response:
                        if int(response.headers["Content-Length"]) != (root / "homelab/ci" / name).stat().st_size:
                            raise ValueError(f"incorrect HTTP size: {name}")
                    with urllib.request.urlopen(url) as response:
                        sha = hashlib.sha256()
                        for data in iter(lambda: response.read(1024 * 1024), b""):
                            sha.update(data)
                        if sha.hexdigest() != digest(root / "homelab/ci" / name):
                            raise ValueError(f"incorrect HTTP bytes: {name}")
                print("PASS: real release staged twice; LAN HTTP kernel/initrd/ISO/menu paths served")
            finally:
                server.shutdown()
                thread.join()
                server.server_close()
    finally:
        # This command creates only this one reconstructed image in the input directory.
        (args.assets / image).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
