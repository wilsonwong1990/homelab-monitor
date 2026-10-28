import unittest

from image.ubuntu_release import release_tag, select_image


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
