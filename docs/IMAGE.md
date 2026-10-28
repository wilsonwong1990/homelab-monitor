# PC installer downloads and releases

This image installs Ubuntu Desktop 24.04 LTS amd64 and stages the monitoring
companion at the exact source commit in `provenance.json`. It targets an x86-64
PC such as a Ryzen 7 with 16 GB RAM, an SSD and an RX 480. CPU inference is the
baseline; RX 480 GPU acceleration is not promised.

For network boot through netboot.xyz, see [NETBOOT.md](NETBOOT.md). Releases
include matching Casper kernel/initrd and a custom iPXE menu. Private assets
are synced by a credentialed LAN staging host, not fetched with tokens in
PXE URLs. You cannot boot a private repository URL directly.

## Download and make a USB installer

Use the repository's **Releases** page:
https://github.com/wilsonwong1990/homelab-monitor/releases

An `image-validation-*` prerelease is an unreviewed test build, not a stable
release. Read its build evidence before using it. Private repository downloads
require GitHub access. Do not share an image outside the repository's intended
audience: its git bundle contains source and repository history, not credentials.

1. Download every `homelab-monitor-<commit>.iso.partNNN` asset, `SHA256SUMS`,
   `assemble.py`, `provenance.json`, `BUILD.txt`, and `INSTALL.md` into one folder.
   Parts are 1,900 MiB or less because GitHub limits individual release assets
   to 2 GiB. You need space for both the parts and reconstructed ISO.
2. With Python 3.9+ installed, open a terminal in that folder and run
   `python3 assemble.py` (Windows: `py -3 assemble.py`). It validates each part
   and the full concatenated ISO before keeping the output. A missing, corrupt,
   or out-of-order part fails explicitly. Do not edit the manifest.
3. Write the reconstructed `.iso` to a USB drive of at least 16 GB with
   balenaEtcher or Rufus in **DD mode**. This erases the selected USB drive.
   Do not write the individual parts. Keep backups before installing an OS.
4. Boot the target PC using its boot menu (UEFI preferred). Select the Ubuntu
   installer and complete language, keyboard, network, disk layout and user
   account screens. **Review the target disk carefully:** proceeding with the
   installer can erase it. No disk choice or password is embedded.
5. After installation, remove the USB drive, reboot, log in, and inspect
   `sudo journalctl -fu homelab-firstboot.service`. Completion is recorded at
   `/var/lib/homelab-monitor/.firstboot-done`. A failure retries after two
   minutes; inspect the journal and restore networking rather than deleting state.

## What is and is not ready

This is **not an offline or preconfigured monitoring appliance**. Network access
is needed during OS installation for packages/security updates and at first
boot for apt, PyPI, the official Ollama installer, and a multi-GB model download.
Those dependencies are not vendored or version-locked; the ISO build is
traceable, not bit-for-bit reproducible. Allow at least 40 GB free on the target
for OS, model, releases and state, in addition to your desktop needs.

First boot installs root-owned application releases, Ollama bound to loopback
with cloud inference disabled, and the configured local model. It disables
suspend/hibernate so monitoring can run while idle. It **does not enable the
monitor/notifier/worker until you configure them**. It does not install Hermes,
enable Discord approvals or remediation, create infrastructure accounts, supply
private tokens, or configure an independent heartbeat observer.

Follow the Configure and Verify and enable sections in the deployment guide:
https://github.com/wilsonwong1990/homelab-monitor/blob/main/docs/DEPLOY.md

Supply read-only Proxmox/UniFi/Home Assistant access as applicable, your
endpoints/expected devices, trusted private CAs, and a Discord webhook in the
root-only environment files. GitHub/Discord service traffic leaves the host;
model inference stays local. A model download completing is not evidence that
it performs well on your PC. Run the deployment smoke test and benchmark.

## Build and verification evidence

`BUILD.txt` links the exact source commit and Actions run. `provenance.json`
records the source commit, upstream filename/digest, Ubuntu signing fingerprint,
and final image digest. It is descriptive build metadata, **not an independent
cryptographic attestation**. Release checksums detect corruption; trust in a
modified image ultimately depends on the repository and its Actions access.

The pipeline authenticates Ubuntu's SHA256SUMS with the pinned Ubuntu CD image
key `843938DF228D22F7B3742BC0D94AA3F0EFE21092`, verifies the upstream ISO,
replays its hybrid BIOS/UEFI boot records, patches both GRUB configurations,
and refreshes changed media-integrity entries. It extracts the finished image
to verify its bundle, exact commit, autoinstall file, boot catalog and checksums.
QEMU boots the real ISO in BIOS and UEFI modes to live systemd userspace with
no network and **no target disk**; serial logs are included.

These are boot smoke checks, not a full graphical installation, Secure Boot
certification, hardware compatibility test, or validation of first-boot external
downloads/live API permissions. Test those on a disposable disk/VM before
relying on a physical deployment.

## Maintainer release workflow

**Rule: every PR merged into `main` automatically gets a stable version tag.**
`Version and release` checks the pushed commit's associated merged PR through
GitHub's API. It starts at `v0.1.0` if there are no stable version tags, otherwise
increments the patch of the highest `vMAJOR.MINOR.PATCH` tag. Prerelease and
validation tags do not affect that calculation. Direct pushes and unmerged or
closed-without-merge PRs do not get stable release tags. There are no bump labels
or label exceptions: all merged PRs get the same patch-release rule.

Remote tag creation is atomic and never force-pushed. Concurrent merges retry
after a tag-name collision; a rerun reuses the version already pointing at that
commit, including annotated tags. There is deliberately no shared Actions
concurrency group for version allocation: GitHub's single pending-slot behavior
could otherwise drop a merge. Each assigned version has its own image-build
concurrency group. A stable release is marked Latest only when its tag is the
highest published `vX.Y.Z`, so neither the completion order of concurrent
uploads nor an `ubuntu-*` refresh release can take the Latest badge.

The version workflow **calls the reusable installer workflow directly after
pushing the tag**. Tags pushed by `GITHUB_TOKEN` do not trigger another workflow
run, so relying only on the tag push would not work. This design needs no PAT or
additional repository secrets, only Actions `contents: write` and PR read access.
The installed workflow becomes active when reviewed changes reach `main`;
opening a PR does not merge it or publish a stable release.
The same orchestrator calls the reusable pipeline for `image-validation/**`
branches without allocating a stable tag, so that call path can be tested
before a PR is merged.

Normal PR CI runs unit tests, shell/workflow lint and Ubuntu host install,
enable, upgrade and rollback checks. Installer-related PRs also build and
boot-check the ISO (without publishing). The installer workflow repeats the
quality checks before downloading an ISO. Release builds run on:

- A PR merge into `main`: allocates a patch tag and calls the installer workflow.
- A reviewed `vMAJOR.MINOR.PATCH` tag: publishes a release for that exact existing tag.
- An `image-validation/**` branch push: publishes a clearly nonstable prerelease
  for that branch commit, never moves a stable tag, and never marks it latest.
- Manual dispatch after the workflow is on the default branch: build-only by
  default, with optional validation-prerelease publication.
- The Ubuntu-refresh schedule: publishes the newest supported point-release
  image when that source/upstream combination has not been published.

Use a unique validation branch for each trial, for example
`git push origin HEAD:refs/heads/image-validation/my-trial`. This does not merge
the feature branch. Merging a reviewed PR is the human authorization for its
automatic patch release. The rule creates stable tags only after a PR merge; it never
merges PRs. Existing version tags pushed by an operator can also trigger a build.

The workflow uses an Ubuntu 24.04 x86-64 hosted runner, a 90-minute image-job
timeout, and checks for 26 GB free before download. It reclaims the preinstalled
Android, .NET and GHC SDK directories only on disposable GitHub-hosted runners,
never on a self-hosted machine. Local ARM Macs are not the
preferred full-build host. If runner capacity is insufficient, select a larger
hosted runner; do not silently skip build or boot checks. The default upstream
is the latest **Ubuntu 24.04 Desktop amd64 point release in its authenticated
SHA256SUMS**, including respins such as 24.04.5.1. Manual builds can request a
specific version listed in that signed manifest; the ISO is fetched from
releases.ubuntu.com, then old-releases.ubuntu.com, and must match the signed
digest. Ubuntu sometimes withdraws superseded ISOs from both while still listing
them, in which case the download fails rather than substituting another build.
No automatic 26.04,
Server, beta/RC or other architecture upgrade is performed.

### Automatic Ubuntu point-release refreshes

`Ubuntu point release refresh` checks the signed manifest daily at 07:23 UTC,
or on manual dispatch. It sorts versions numerically (not directory dates or
string order), binds the selected version/digest to the exact source commit,
and calls the same quality/build/boot/publish pipeline. The download is
authenticated again; a changed digest between detection and download fails.
Bad signatures, missing supported images and GitHub API errors fail explicitly.

Production refresh releases use
`ubuntu-<point-version>-<source12>-<upstream-digest12>` tags, for example
`ubuntu-24.04.5.1-012345abcdef-4da4a0c9035d`. They are published normal releases
but never move the latest application-semver release or reuse an application
`vX.Y.Z` tag. The upstream version, hash and source commit identify an immutable
build: daily repeats skip an already published identity, while a changed
upstream digest or source commit permits a new build. Only the newest supported
point release is selected, not every historical ISO in the manifest.

Discovery and image builds have concurrency guards. Interrupted Ubuntu-refresh
drafts resume under the same deterministic tag; published assets are never
replaced. A newly merged application PR may release the same Ubuntu point
version under its own semver tag; the refresh channel is a separate identity.

**Schedules run only once the workflow is on the default branch (`main`).**
Keeping this PR open means no production polling is active yet. For premerge
proof, pushing an `ubuntu-validation/**` branch exercises the actual signed
discovery and pipeline with a deterministic `image-validation-ubuntu-*`
prerelease tag. Rerunning that workflow demonstrates its skip behavior.
No production refresh or stable application tag is created by that test.

The manifest comes from [Ubuntu's official 24.04 release directory](https://releases.ubuntu.com/24.04/).
Authentication follows [Ubuntu's image-verification guidance](https://ubuntu.com/tutorials/how-to-verify-ubuntu),
using the full pinned CD image signing fingerprint above, not trusting a
checksum downloaded over HTTPS alone.

Successful builds retain transport artifacts in Actions for seven days.
Publication creates a draft, uploads assets, downloads and compares every
asset, verifies reconstructed ISO bytes again, then publishes. A failed
publication leaves a draft for inspection, not a successful public release.
Rerun an unsuccessful validation job with a new run attempt; for an existing
stable draft, inspect and remove that draft deliberately before retrying.
An already published release is never overwritten by a rerun; inspect the
existing release rather than moving its tag. Failed image/release builds leave
the assigned stable tag intact so the failed Actions run can be diagnosed and
rerun. Protect stable tags against updates/deletion, and permit the Actions
identity to create tags under the repository's ruleset; an incompatible rule or
read-only token fails explicitly rather than silently skipping a release.
Configure protected branches/tags and restrict workflow modification and
repository write access; no privileged LAN/self-hosted runner is required.
