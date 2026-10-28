import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from image import site_profile
from image.pxe import render_menu
from image.site_profile import parse_cmdline, record, validate

ROOT = Path(__file__).resolve().parents[1]
KEY = "ssh-ed25519 " + "A" * 68 + " wilson@laptop"
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def profile(**overrides):
    data = {"version": 1, "ssh_authorized_keys": {"wilson": [KEY]}, "packages": ["htop", "tmux"],
            "agents": [{"name": "homelab-assistant-agent",
                        "url": "https://github.com/wilsonwong1990/homelab-assistant-agent.git",
                        "commit": COMMIT}]}
    data.update(overrides)
    return json.dumps(data).encode()


class SiteProfileTests(unittest.TestCase):
    def test_accepts_public_keys_packages_and_pinned_agents(self):
        self.assertEqual(validate(profile())["agents"][0]["commit"], COMMIT)
        self.assertEqual(validate(b'{"version": 1}'), {"version": 1})

    def test_rejects_secrets_and_unpinned_or_unsafe_entries(self):
        bad = {
            "private key": profile(ssh_authorized_keys={"wilson": ["-----BEGIN OPENSSH PRIVATE KEY-----"]}),
            "webhook": profile(packages=["https://discord.com/api/webhooks/1/abc"]),
            "github token": profile(packages=["ghp_" + "a" * 36]),
            "escaped token": profile().replace(b"wilson@laptop", b"\\u0067hp_" + b"a" * 36),
            "unknown field": profile(env={"DISCORD_BOT_TOKEN": "x"}),
            "root keys": profile(ssh_authorized_keys={"root": [KEY]}),
            "two keys in one line": profile(ssh_authorized_keys={"wilson": [KEY + "\n" + KEY]}),
            "key options": profile(ssh_authorized_keys={"wilson": ['command="sh" ' + KEY]}),
            "branch pin": profile(agents=[{"name": "a", "url": "https://h/a.git", "commit": "main"}]),
            "credential url": profile(agents=[{"name": "a", "url": "https://u:p@h/a.git", "commit": COMMIT}]),
            "token query": profile(agents=[{"name": "a", "url": "https://h/a.git?t=1", "commit": COMMIT}]),
            "http agent": profile(agents=[{"name": "a", "url": "http://h/a.git", "commit": COMMIT}]),
            "path name": profile(agents=[{"name": "../etc", "url": "https://h/a.git", "commit": COMMIT}]),
            "package option": profile(packages=["-oAPT::x=1"]),
            "wrong version": profile(version=2),
            "oversize": b" " * (site_profile.MAX_BYTES + 1),
        }
        for name, raw in bad.items():
            with self.subTest(name), self.assertRaises(ValueError):
                validate(raw)

    def test_cmdline_requires_url_and_hash_together(self):
        sha = "a" * 64
        self.assertIsNone(parse_cmdline("boot=casper autoinstall ---"))
        self.assertEqual(parse_cmdline(f"x homelab.profile=http://10.0.0.5/p.json homelab.profile_sha256={sha} ---"),
                         {"url": "http://10.0.0.5/p.json", "sha256": sha})
        for line in ("homelab.profile=http://h/p.json", f"homelab.profile_sha256={sha}",
                     "homelab.profile=http://h/p.json homelab.profile_sha256=ABC",
                     f"homelab.profile=http://u:p@h/p.json homelab.profile_sha256={sha}"):
            with self.subTest(line), self.assertRaises(ValueError):
                parse_cmdline(line)

    def test_record_writes_source_only_when_offered(self):
        with tempfile.TemporaryDirectory() as temp:
            cmdline, source = Path(temp, "cmdline"), Path(temp, "etc/source.json")
            cmdline.write_text("boot=casper ---\n")
            self.assertFalse(record(cmdline, source))
            self.assertFalse(source.exists())
            cmdline.write_text(f"homelab.profile=http://h/p.json homelab.profile_sha256={'b' * 64}\n")
            self.assertTrue(record(cmdline, source))
            self.assertEqual(json.loads(source.read_text())["sha256"], "b" * 64)

    def test_fetch_rejects_hash_mismatch(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = profile()
        with mock.patch("urllib.request.urlopen", return_value=response):
            self.assertEqual(site_profile.fetch("http://h/p.json", hashlib.sha256(profile()).hexdigest()), profile())
            with self.assertRaisesRegex(ValueError, "pinned SHA-256"):
                site_profile.fetch("http://h/p.json", "0" * 64)

    def test_agent_checkout_is_pinned_and_not_executed(self):
        with tempfile.TemporaryDirectory() as temp:
            upstream = Path(temp, "upstream")
            subprocess.run(["git", "init", "-q", str(upstream)], check=True)
            (upstream / "install.sh").write_text("touch should-not-run\n")
            git = ["git", "-C", str(upstream), "-c", "user.name=t", "-c", "user.email=t@t"]
            subprocess.run([*git, "add", "."], check=True)
            subprocess.run([*git, "commit", "-qm", "init"], check=True)
            commit = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
            agent = {"name": "demo", "url": upstream.as_uri(), "commit": commit}
            root = Path(temp, "agents")
            site_profile.install_agent(agent, root)
            site_profile.install_agent(agent, root)  # idempotent
            self.assertTrue((root / "demo/install.sh").exists())
            self.assertFalse((root / "demo/should-not-run").exists())
            with self.assertRaisesRegex(ValueError, "pinned commit"):
                site_profile.install_agent(dict(agent, commit="f" * 40), root)

    def test_keys_are_written_as_the_target_user(self):
        with mock.patch("pwd.getpwnam", side_effect=KeyError), self.assertRaisesRegex(ValueError, "does not exist"):
            site_profile.install_keys("ghost", [KEY])
        with mock.patch("pwd.getpwnam", return_value=mock.Mock(pw_uid=999, pw_dir="/var/lib/x")), \
                self.assertRaisesRegex(ValueError, "system account"):
            site_profile.install_keys("svc", [KEY])
        with mock.patch("pwd.getpwnam", return_value=mock.Mock(pw_uid=1000, pw_dir="/home/wilson")), \
                mock.patch.object(site_profile, "run") as run:
            site_profile.install_keys("wilson", [KEY])
        self.assertEqual(run.call_args.args[:4], ("runuser", "-u", "wilson", "--"))
        self.assertEqual(run.call_args.args[-1], "/home/wilson")
        self.assertEqual(run.call_args.kwargs["input"], KEY + "\n")

    def test_key_writer_appends_once_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            keys = Path(temp, ".ssh/authorized_keys")
            for _ in range(2):
                subprocess.run([sys.executable, "-c", site_profile.KEY_WRITER, temp],
                               input=KEY + "\n", text=True, check=True)
            self.assertEqual(keys.read_text(), KEY + "\n")
            keys.write_text("ssh-rsa existing")
            subprocess.run([sys.executable, "-c", site_profile.KEY_WRITER, temp],
                           input=KEY + "\n", text=True, check=True)
            self.assertEqual(keys.read_text(), "ssh-rsa existing\n" + KEY + "\n")
            self.assertEqual(keys.stat().st_mode & 0o777, 0o600)
            self.assertEqual(keys.parent.stat().st_mode & 0o777, 0o700)

    def test_menu_and_install_wiring(self):
        sha = "c" * 64
        menu = render_menu("http://10.0.0.5/homelab/t", "http://10.0.0.5/homelab-site/p.json", sha)
        self.assertIn(f"set profile_sha256 {sha}\n", menu)
        self.assertTrue(menu.startswith("#!ipxe\n"))
        for url, digest in (("http://10.0.0.5/p.json", None), (None, sha), ("http://10.0.0.5/p.json", "x")):
            with self.subTest(url=url, digest=digest), self.assertRaises(ValueError):
                render_menu("http://10.0.0.5/homelab/t", url, digest)
        ipxe = (ROOT / "image/homelab.ipxe").read_text()
        self.assertIn("${profile_args} --- ||", ipxe)
        autoinstall = (ROOT / "image/autoinstall.yaml").read_text()
        self.assertIn("image/site_profile.py record", autoinstall)
        self.assertIn("systemctl enable homelab-site-profile.service", autoinstall)
        unit = (ROOT / "image/homelab-site-profile.service").read_text()
        self.assertIn("ConditionPathExists=/etc/homelab-site/source.json", unit)
        self.assertIn("ConditionPathExists=!/var/lib/homelab-site/.applied", unit)

    def test_example_profile_is_valid(self):
        validate((ROOT / "examples/site-profile.example.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
