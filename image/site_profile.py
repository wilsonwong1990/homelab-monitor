#!/usr/bin/env python3
"""Apply a non-secret, hash-pinned site profile on first boot.

The profile adds SSH *public* keys, extra apt packages and agent repositories
pinned to exact commits. It is served on the LAN beside the netboot assets, so
anything inside it must be safe for every device on that network to read.
Credentials stay in /etc/homelab-monitor/*.env and are entered on the machine.
"""
import argparse
import hashlib
import json
import os
import pwd
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

SOURCE = Path("/etc/homelab-site/source.json")
DONE = Path("/var/lib/homelab-site/.applied")
AGENTS = Path("/opt/homelab-agents")
MAX_BYTES = 1024 * 1024
SHA = re.compile(r"[0-9a-f]{64}")
TOP_KEYS = {"version", "ssh_authorized_keys", "packages", "agents"}
AGENT_KEYS = {"name", "url", "commit"}
PUBLIC_KEY = re.compile(
    r"(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)|sk-ssh-ed25519@openssh\.com"
    r"|sk-ecdsa-sha2-nistp256@openssh\.com) [A-Za-z0-9+/]{40,}={0,3}(?: [ -~]{1,100})?")
# Defense in depth: a strict schema already leaves no field for credentials.
SECRET_PATTERNS = (
    re.compile(r"PRIVATE KEY"),
    re.compile(r"discord(?:app)?\.com/api/webhooks/", re.I),
    re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"PVEAPIToken", re.I),
)


def _url(value, schemes):
    parsed = urlsplit(value) if isinstance(value, str) else None
    if (parsed is None or parsed.scheme not in schemes or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
            or not re.fullmatch(r"[a-z]+://[A-Za-z0-9.:\[\]-]+(?:/[A-Za-z0-9._/~-]*)?", value)
            or any(part in (".", "..") for part in parsed.path.split("/"))):
        raise ValueError("invalid URL: credentials, queries and fragments are not allowed")
    return value


def validate(raw):
    """Return the parsed profile or raise ValueError; never echoes rejected content."""
    if len(raw) > MAX_BYTES:
        raise ValueError("site profile is larger than 1 MiB")
    text = raw.decode("utf-8")
    profile = json.loads(text)
    # Scan the decoded values too, so JSON \u escapes cannot hide a token.
    for candidate in (text, json.dumps(profile, ensure_ascii=False)):
        if any(pattern.search(candidate) for pattern in SECRET_PATTERNS):
            raise ValueError("site profile appears to contain a credential; keep secrets out of LAN-served files")
    if not isinstance(profile, dict) or profile.get("version") != 1 or set(profile) - TOP_KEYS:
        raise ValueError(f"site profile must be an object with version 1 and only {sorted(TOP_KEYS)}")
    keys = profile.get("ssh_authorized_keys", {})
    if not isinstance(keys, dict):
        raise ValueError("ssh_authorized_keys must map usernames to public key lists")
    for user, entries in keys.items():
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user) or user == "root":
            raise ValueError(f"invalid or disallowed username: {user!r}")
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"ssh_authorized_keys.{user} must be a non-empty list")
        for entry in entries:
            if not isinstance(entry, str) or not PUBLIC_KEY.fullmatch(entry):
                raise ValueError(f"ssh_authorized_keys.{user} has an entry that is not a single public key")
    packages = profile.get("packages", [])
    if not isinstance(packages, list) or not all(
            isinstance(name, str) and re.fullmatch(r"[a-z0-9][a-z0-9+.-]{1,62}", name) for name in packages):
        raise ValueError("packages must be a list of plain Debian package names")
    agents = profile.get("agents", [])
    if not isinstance(agents, list):
        raise ValueError("agents must be a list")
    names = set()
    for agent in agents:
        if not isinstance(agent, dict) or set(agent) != AGENT_KEYS:
            raise ValueError(f"each agent needs exactly {sorted(AGENT_KEYS)}")
        name = agent["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name) or name in names:
            raise ValueError(f"invalid or duplicate agent name: {name!r}")
        names.add(name)
        _url(agent["url"], ("https",))
        if not isinstance(agent["commit"], str) or not re.fullmatch(r"[0-9a-f]{40}", agent["commit"]):
            raise ValueError(f"agent {name} must pin a full 40-character commit")
    return profile


def parse_cmdline(text):
    """Read homelab.profile= and homelab.profile_sha256= from a kernel command line."""
    values = {}
    for token in text.split():
        key, _, value = token.partition("=")
        if key in ("homelab.profile", "homelab.profile_sha256"):
            values[key] = value
    if not values:
        return None
    if set(values) != {"homelab.profile", "homelab.profile_sha256"}:
        raise ValueError("homelab.profile and homelab.profile_sha256 must be given together")
    if not SHA.fullmatch(values["homelab.profile_sha256"]):
        raise ValueError("homelab.profile_sha256 must be 64 lowercase hex characters")
    return {"url": _url(values["homelab.profile"], ("http", "https")),
            "sha256": values["homelab.profile_sha256"]}


def record(cmdline=Path("/proc/cmdline"), source=SOURCE):
    """Installer late-command: remember the pinned profile for first boot, if one was offered."""
    found = parse_cmdline(Path(cmdline).read_text())
    if found is None:
        print("no site profile on the kernel command line; skipping")
        return False
    source.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    source.write_text(json.dumps(found) + "\n")
    source.chmod(0o644)
    print(f"recorded site profile {found['sha256'][:12]}")
    return True


def fetch(url, sha256):
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - scheme validated
        raw = response.read(MAX_BYTES + 1)
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError("site profile does not match its pinned SHA-256")
    return raw


def run(*command, **kwargs):
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive", GIT_TERMINAL_PROMPT="0")
    subprocess.run(command, check=True, env=env, **kwargs)


# Runs as the target user, so a symlink in their home can only reach files they could already write.
KEY_WRITER = """
import os, sys
os.umask(0o077)
ssh = os.path.join(sys.argv[1], ".ssh")
keys = os.path.join(ssh, "authorized_keys")
os.makedirs(ssh, mode=0o700, exist_ok=True)
try:
    with open(keys) as handle:
        have = handle.read()
except FileNotFoundError:
    have = ""
missing = [key for key in sys.stdin.read().splitlines() if key and key not in have.splitlines()]
if missing:
    with open(keys, "a") as handle:
        handle.write(("\\n" if have and not have.endswith("\\n") else "") + "".join(key + "\\n" for key in missing))
os.chmod(keys, 0o600)
"""


def install_keys(user, entries):
    try:
        account = pwd.getpwnam(user)
    except KeyError:
        raise ValueError(f"user {user!r} does not exist; match the account created in the installer") from None
    if account.pw_uid < 1000:
        raise ValueError(f"refusing to add keys for system account {user!r}")
    # Never touch the user's home as root: write the keys with the user's own privileges.
    run("runuser", "-u", user, "--", sys.executable, "-c", KEY_WRITER, account.pw_dir,
        input="".join(f"{entry}\n" for entry in entries), text=True)


def install_agent(agent, root=AGENTS):
    """Check out an agent at its pinned commit. Nothing from it is executed or enabled."""
    root.mkdir(mode=0o755, parents=True, exist_ok=True)
    target = root / agent["name"]
    if target.is_symlink():
        raise ValueError(f"refusing to follow symlink {target}")
    if not target.exists():
        with tempfile.TemporaryDirectory(prefix=".agent-", dir=root) as temp:
            staging = Path(temp) / "checkout"
            run("git", "clone", "--quiet", "--no-checkout", "--", agent["url"], str(staging))
            run("git", "-C", str(staging), "checkout", "--quiet", "--detach", agent["commit"])
            staging.rename(target)
    head = subprocess.check_output(["git", "-C", str(target), "rev-parse", "HEAD"], text=True).strip()
    if head != agent["commit"]:
        raise ValueError(f"agent {agent['name']} is at {head[:12]}, not the pinned commit; remove it to re-fetch")


def apply(profile, agents_root=AGENTS):
    if profile.get("packages"):
        run("apt-get", "update", "-qq")
        run("apt-get", "install", "-y", "-qq", "--no-install-recommends", "--", *profile["packages"])
    for user, entries in profile.get("ssh_authorized_keys", {}).items():
        install_keys(user, entries)
    for agent in profile.get("agents", []):
        install_agent(agent, agents_root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("record", help="installer: save the profile URL/hash from /proc/cmdline")
    check = commands.add_parser("check", help="validate a local profile file and print its SHA-256")
    check.add_argument("file", type=Path)
    run_apply = commands.add_parser("apply", help="first boot: download, verify and apply the profile")
    run_apply.add_argument("--url", help="manual use, e.g. after a USB install; requires --sha256")
    run_apply.add_argument("--sha256")
    args = parser.parse_args(argv)
    try:
        if args.command == "record":
            record()
        elif args.command == "check":
            raw = args.file.read_bytes()
            validate(raw)
            print(hashlib.sha256(raw).hexdigest())
        else:
            if bool(args.url) != bool(args.sha256):
                parser.error("--url and --sha256 must be given together")
            source = ({"url": args.url, "sha256": args.sha256} if args.url
                      else json.loads(SOURCE.read_text()))
            _url(source["url"], ("http", "https"))
            if not SHA.fullmatch(source["sha256"]):
                raise ValueError("invalid pinned SHA-256")
            apply(validate(fetch(source["url"], source["sha256"])))
            DONE.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            DONE.touch()
            print("PASS: site profile applied")
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"site profile: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
