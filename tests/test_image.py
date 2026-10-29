import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "image" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tools = load("iso_tools")
assemble = load("assemble").assemble


class ImageTests(unittest.TestCase):
    def test_boot_arguments_preserve_installer_and_enable_serial(self):
        result = tools.configure_boot("menuentry 'Ubuntu' {\n linux /casper/vmlinuz quiet splash ---\n initrd /casper/initrd\n}\n")
        self.assertIn("autoinstall console=tty0 console=ttyS0,115200n8", result)
        self.assertIn(" ---\n", result)
        self.assertIn("initrd /casper/initrd", result)
        self.assertNotIn("quiet", result)
        with self.assertRaises(ValueError):
            tools.configure_boot("unsupported boot config")

    def test_media_sums_preserve_unchanged_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "md5sum.txt").write_text("abc  ./casper/filesystem.squashfs\n")
            for name in ("boot/grub/grub.cfg", "boot/grub/loopback.cfg", "autoinstall.yaml",
                         "homelab/ref", "homelab/homelab-monitor.bundle"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"test")
            tools.update_media_sums(root)
            sums = (root / "md5sum.txt").read_text()
            self.assertIn("abc  ./casper/filesystem.squashfs", sums)
            self.assertEqual(sums.count("098f6bcd4621d373cade4e832627b4f6"), 5)

    def test_release_roundtrip_boundaries_corruption_and_missing_parts(self):
        for size in (1, 7, 8, 15, 16, 17):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                image = root / "homelab-monitor-012345abcdef.iso"
                image.write_bytes(bytes(range(size)))
                release = root / "release"
                tools.split_image(image, release, chunk_size=8)
                self.assertEqual(assemble(release, True)[1], tools.digest(image))
                assemble(release)
                self.assertEqual(image.read_bytes(), (release / image.name).read_bytes())
                with self.assertRaises(FileExistsError):
                    assemble(release)
                (release / f"{image.name}.part000").write_bytes(b"corrupt")
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    assemble(release, True)

    def test_reject_oversized_parts_and_untrusted_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValueError):
                tools.split_image(root / "absent", root, 2 * 1024**3)
            (root / "SHA256SUMS").write_text("0" * 64 + "  ../outside.iso\n")
            with self.assertRaises(ValueError):
                assemble(root, True)
            image = root / "homelab-monitor-012345abcdef.iso"
            image.write_bytes(b"123456789")
            tools.split_image(image, root, chunk_size=8)
            (root / f"{image.name}.part001").unlink()
            with self.assertRaises(FileNotFoundError):
                assemble(root, True)

    def test_release_workflow_uses_trusted_verifier_and_serial_latest_reconciliation(self):
        workflow = (ROOT / ".github/workflows/image-release.yml").read_text()
        self.assertIn("cmp image/assemble.py release/assemble.py", workflow)
        self.assertIn("cmp image/assemble.py downloaded/assemble.py", workflow)
        self.assertNotIn("python3 release/assemble.py", workflow)
        self.assertNotIn("python3 downloaded/assemble.py", workflow)
        self.assertIn('GITHUB_EVENT_NAME" == push && "$REF_TYPE" == tag', workflow)
        self.assertIn("concurrency:\n      group: stable-release-latest-${{ github.repository }}", workflow)
        self.assertIn("--latest=false", workflow)
        self.assertIn("| .id' releases.json", workflow)
        self.assertIn("-f make_latest=true", workflow)

    def test_firstboot_is_pinned_and_never_enables_monitoring(self):
        unit = (ROOT / "image/homelab-firstboot.service").read_text()
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("ConditionPathExists=!", unit)
        self.assertNotIn("--enable", unit)
        self.assertIn("--source /opt/homelab-monitor-src", unit)
