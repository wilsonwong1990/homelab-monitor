import unittest

from image.ubuntu_release import installer_parts_from_sums, published_release, release_tag, select_image


class UbuntuReleaseTests(unittest.TestCase):
    def test_numeric_point_releases_and_supported_flavor_only(self):
        text = "\n".join(f"{'a' * 64} *ubuntu-{version}-{flavor}.iso" for version, flavor in (
            ("24.04.9", "desktop-amd64"), ("24.04.10", "desktop-amd64"),
            ("24.04.10.1", "desktop-amd64"), ("26.04.1", "desktop-amd64"),
            ("24.04.99", "live-server-amd64"), ("24.04.99", "desktop-arm64")))
        self.assertEqual(select_image(text), ("24.04.10.1", "a" * 64))
        self.assertEqual(select_image(text, "24.04.9")[0], "24.04.9")
        for version in ("26.04.1", "24.04.11", "$(bad)"):
            with self.assertRaises(ValueError):
                select_image(text, version)

    def test_fail_closed_and_deterministic_identity(self):
        line = f"{'a' * 64} *ubuntu-24.04.5.1-desktop-amd64.iso"
        for text in ("", "garbage", line + "\n" + line, line.replace("a" * 64, "invalid")):
            with self.assertRaises(ValueError):
                select_image(text)
        tag = release_tag("24.04.5.1", "b" * 40, "a" * 64)
        self.assertEqual(tag, "ubuntu-24.04.5.1-bbbbbbbbbbbb-aaaaaaaaaaaa")
        self.assertNotEqual(tag, release_tag("24.04.5.1", "c" * 40, "a" * 64))
        self.assertNotEqual(tag, release_tag("24.04.5.1", "b" * 40, "d" * 64))
        self.assertTrue(release_tag("24.04.5.1", "b" * 40, "a" * 64, True).startswith("image-validation-"))

    def test_published_skip_requires_complete_assets_and_exact_source(self):
        commit, tag = "b" * 40, "ubuntu-test"
        self.assertFalse(published_release([[]], tag, commit))
        release = {"tag_name": tag, "draft": True}
        self.assertFalse(published_release([[release]], tag, commit))
        release.update(draft=False, target_commitish=commit, assets=[])
        sums = "".join(f"{'a' * 64}  {name}\n" for name in (
            "homelab-monitor-bbbbbbbbbbbb.iso",
            "homelab-monitor-bbbbbbbbbbbb.iso.part000",
            "homelab-monitor-bbbbbbbbbbbb.iso.part001"))
        with self.assertRaisesRegex(ValueError, "missing"):
            published_release([[release]], tag, commit, sums)
        for name in ("provenance.json", "SHA256SUMS", "assemble.py", "pxe-vmlinuz", "pxe-initrd",
                     "PXE-SHA256SUMS", "homelab.ipxe", "INSTALL.md", "NETBOOT.md", "BUILD.txt",
                     "homelab-monitor-bbbbbbbbbbbb.iso.part000",
                     "homelab-monitor-bbbbbbbbbbbb.iso.part001"):
            release["assets"].append({"name": name, "size": 1, "state": "uploaded"})
        self.assertEqual(installer_parts_from_sums(sums, commit), [
            "homelab-monitor-bbbbbbbbbbbb.iso.part000", "homelab-monitor-bbbbbbbbbbbb.iso.part001"])
        self.assertTrue(published_release([[], [release]], tag, commit, sums))
        release["assets"] = [asset for asset in release["assets"] if not asset["name"].endswith("part001")]
        with self.assertRaisesRegex(ValueError, "missing required assets"):
            published_release([[release]], tag, commit, sums)
        with self.assertRaisesRegex(ValueError, "different source"):
            published_release([[release]], tag, "c" * 40, sums)

    def test_published_release_needs_valid_complete_sums(self):
        commit, tag = "b" * 40, "ubuntu-test"
        release = {"tag_name": tag, "draft": False, "target_commitish": commit, "assets": []}
        with self.assertRaisesRegex(ValueError, "SHA256SUMS"):
            published_release([[release]], tag, commit)
        for sums in ("garbage", f"{'a' * 64}  homelab-monitor-{commit[:12]}.iso\n",
                     f"{'a' * 64}  homelab-monitor-{commit[:12]}.iso\n{'b' * 64}  homelab-monitor-{commit[:12]}.iso.part001\n"):
            with self.subTest(sums=sums), self.assertRaises(ValueError):
                installer_parts_from_sums(sums, commit)
