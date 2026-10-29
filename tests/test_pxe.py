import functools
import http.server
import json
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from image.iso_tools import split_image
from image.pxe import digest, render_menu, validate_url, verify_assets
from image.stage_netboot import download, latest_ubuntu, stage

ROOT = Path(__file__).resolve().parents[1]


class PxeTests(unittest.TestCase):
    def fixture(self, root):
        assets = root / "download"
        image = root / "homelab-monitor-012345abcdef.iso"
        image.write_bytes(b"fixture-iso")
        split_image(image, assets, chunk_size=8)
        for name, data in (("pxe-vmlinuz", b"kernel"), ("pxe-initrd", b"initrd"),
                           ("homelab.ipxe", (ROOT / "image/homelab.ipxe").read_bytes())):
            (assets / name).write_bytes(data)
        sums = {name: digest(assets / name) for name in ("pxe-vmlinuz", "pxe-initrd", "homelab.ipxe")}
        (assets / "PXE-SHA256SUMS").write_text("".join(f"{sha}  {name}\n" for name, sha in sums.items()))
        (assets / "provenance.json").write_text(json.dumps({
            "source_commit": "0" * 40, "image": image.name, "image_sha256": digest(image), "pxe": sums}))
        web = root / "web"
        web.mkdir()
        return assets, web

    def test_reject_credential_urls_and_ipxe_injection(self):
        for url in ("https://user:token@host/boot", "http://host/?token=secret", "http://host/#secret",
                    "http://host/${token}", "http://host/;reboot", "http://host/\nboot", "file:///tmp/iso",
                    "http://host/../private", "http://host/a b", "http://host:99999", "http://host:", "http://host:bad"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)
        self.assertIn("set asset_base http://192.168.1.2:8080/homelab/test", render_menu("http://192.168.1.2:8080/homelab/test"))

    def test_stage_serve_verify_and_idempotency(self):
        with tempfile.TemporaryDirectory() as temp:
            assets, web = self.fixture(Path(temp))
            handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(web))
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                stage(assets, web, base, "test-release")
                stage(assets, web, base, "test-release")
                for path, data in (("homelab/test-release/installer.iso", b"fixture-iso"),
                                   ("homelab/test-release/vmlinuz", b"kernel"),
                                   ("homelab/test-release/initrd", b"initrd")):
                    with urllib.request.urlopen(f"{base}/{path}") as response:
                        self.assertEqual(response.read(), data)
                menu = (web / "homelab.ipxe").read_text()
                # iPXE rejects scripts unless #!ipxe is the very first line.
                self.assertTrue(menu.startswith("#!ipxe\n"))
                self.assertTrue((web / "homelab/test-release/boot.ipxe").read_text().startswith("#!ipxe\n"))
                self.assertIn(f"set asset_base {base}/homelab/test-release", menu)
                install_menu = (web / "homelab/test-release/homelab.ipxe").read_text()
                self.assertIn("--default cancel", install_menu)
                self.assertIn("iso-url=${asset_base}/installer.iso", install_menu)
                self.assertNotIn("github.com", install_menu)
                (web / "homelab/test-release/initrd").write_bytes(b"corrupt")
                with self.assertRaisesRegex(ValueError, "corrupt"):
                    stage(assets, web, base, "test-release")
            finally:
                server.shutdown()
                thread.join()
                server.server_close()

    def test_site_profile_is_pinned_in_root_menu_only(self):
        with tempfile.TemporaryDirectory() as temp:
            assets, web = self.fixture(Path(temp))
            good = Path(temp, "profile.json")
            good.write_text('{"version": 1, "packages": ["htop"]}')
            stage(assets, web, "http://10.0.0.5", "test-release", good)
            sha = digest(good)
            menu = (web / "homelab.ipxe").read_text()
            self.assertTrue(menu.startswith("#!ipxe\n"))
            self.assertLess(menu.index("clear profile_url"), menu.index("set profile_url"))
            self.assertLess(menu.index("clear profile_sha256"), menu.index(f"set profile_sha256 {sha}"))
            self.assertIn(f"set profile_url http://10.0.0.5/homelab-site/profile-{sha}.json\n", menu)
            self.assertIn(f"set profile_sha256 {sha}\n", menu)
            self.assertEqual(digest(web / f"homelab-site/profile-{sha}.json"), sha)
            local_menu = (web / "homelab/test-release/boot.ipxe").read_text()
            self.assertIn("clear profile_url\nclear profile_sha256\n", local_menu)
            self.assertNotIn(f"set profile_sha256 {sha}", local_menu)
            stage(assets, web, "http://10.0.0.5", "test-release")
            wrapper = (web / "homelab.ipxe").read_text()
            self.assertIn("clear profile_url\nclear profile_sha256\n", wrapper)
            self.assertNotIn(f"set profile_sha256 {sha}", wrapper)
            secret = Path(temp, "secret.json")
            secret.write_text('{"version": 1, "token": "ghp_' + "a" * 36 + '"}')
            with self.assertRaisesRegex(ValueError, "credential"):
                stage(assets, web, "http://10.0.0.5", "test-release", secret)
            wrapper = (web / "homelab.ipxe").read_text()
            self.assertIn("clear profile_url\nclear profile_sha256\n", wrapper)
            self.assertNotIn(f"set profile_sha256 {sha}", wrapper)

    def test_corruption_and_concurrent_sync_fail_before_menu_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            assets, web = self.fixture(Path(temp))
            (assets / "pxe-initrd").write_bytes(b"bad")
            with self.assertRaisesRegex(ValueError, "checksum"):
                verify_assets(assets)
            with self.assertRaises(ValueError):
                stage(assets, web, "http://host", "test")
            self.assertFalse((web / "homelab.ipxe").exists())
            (web / ".homelab-sync-lock").mkdir()
            with self.assertRaises(FileExistsError):
                stage(assets, web, "http://host", "test")

    def test_latest_ubuntu_ignores_drafts_prereleases_and_app_tags(self):
        releases = [
            {"tag_name": "ubuntu-24.04.5.1-aaaaaaaaaaaa-bbbbbbbbbbbb", "draft": False, "prerelease": False, "published_at": "2026-10-28"},
            {"tag_name": "v9.0.0", "draft": False, "prerelease": False, "published_at": "2026-10-29"},
            {"tag_name": "ubuntu-24.04.6-aaaaaaaaaaaa-bbbbbbbbbbbb", "draft": True, "prerelease": False, "published_at": None},
        ]
        with mock.patch("image.stage_netboot.gh", return_value=json.dumps([releases])):
            self.assertEqual(latest_ubuntu("owner/repo"), releases[0]["tag_name"])
        with mock.patch("image.stage_netboot.gh", return_value=json.dumps({"isDraft": True, "assets": []})):
            with self.assertRaisesRegex(ValueError, "draft"):
                download("owner/repo", "tag", Path("/unused"))

    def test_http_smoke_command_uses_real_staging_path(self):
        with tempfile.TemporaryDirectory() as temp:
            assets, _ = self.fixture(Path(temp))
            result = subprocess.run(
                ["python3", str(ROOT / "image/test_stage.py"), str(assets)],
                capture_output=True, text=True, check=True)
            self.assertIn("PASS: real release staged twice", result.stdout)
            self.assertFalse((assets / "homelab-monitor-012345abcdef.iso").exists())
