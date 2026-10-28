# netboot.xyz and private release assets

This project supplies a **custom iPXE menu plus matching Casper kernel/initrd**.
It does not chainload an arbitrary desktop ISO, and netboot.xyz cannot take a
private repository URL and discover/authenticate its release assets by itself.
The practical route is:

1. A trusted staging host downloads and verifies a release using its own GitHub
   credentials, reconstructs the ISO, and serves the ISO/kernel/initrd over LAN HTTP.
2. Your existing DHCP/PXE setup boots netboot.xyz. Its custom menu chains the
   generated `homelab.ipxe` on that LAN host.
3. iPXE loads `vmlinuz` and `initrd` extracted from the **same custom ISO**.
   Casper downloads that ISO using `iso-url=`, mounts it, and reads the embedded
   `autoinstall.yaml` and pinned source bundle. No separate secret-bearing seed
   or unattended disk layout is served.

The menu defaults to **cancel**, requires selection and a keypress, and still
leaves locale, keyboard, network, storage and account setup interactive.
Choosing to install an OS can erase the selected disk. No deployment here
changes DHCP, installs a server, or writes any physical target disk.

## Stage a private release

Use a Linux LAN host with Python 3.9+, GitHub CLI (`gh`), sufficient free space
(allow at least 30 GB across temporary storage and web storage), and an existing
HTTP server. The staging script itself does not need root, run a daemon, or
install dependencies. Authenticate `gh` **on this host only**, using an account
or read-only fine-grained token with Contents read access to this private repo.
Keep its auth state out of the web root and give it no release-write permission.

Run the tools from a reviewed checkout of the repository:

```bash
# Example: /srv/netboot-assets is already served at http://192.168.1.50:8080
# by your LAN web server and is writable by the dedicated staging account.
python3 image/stage_netboot.py \
  --repo wilsonwong1990/homelab-monitor \
  --tag image-validation-EXACT-RUN-ID-1 \
  --web-root /srv/netboot-assets \
  --base-url http://192.168.1.50:8080
```

Replace the example tag with an actual release that contains `pxe-vmlinuz`,
`pxe-initrd`, `homelab.ipxe`, `PXE-SHA256SUMS`, ISO parts, checksums and provenance.
Older validation releases made before PXE support cannot be staged.
An explicit `--tag` accepts a published validation prerelease for testing.
Drafts, missing assets and corrupt files fail explicitly.

For ongoing updates after the workflows reach `main`, use:

```bash
python3 image/stage_netboot.py \
  --repo wilsonwong1990/homelab-monitor --latest-ubuntu \
  --web-root /srv/netboot-assets --base-url http://192.168.1.50:8080
```

This selects the newest **published production Ubuntu-refresh release**,
excluding drafts, validation prereleases and application-only tags. Run it
daily with your existing scheduler under the staging identity; nonzero exit
means sync failed and needs inspection. Scheduling is an operator step, not
silently installed by this repository. For pre-downloaded assets use
`--tag TAG --from-directory /private/downloads/TAG` instead (no GitHub call).

The tool downloads to a private temporary directory outside the web root,
verifies all ISO parts/full ISO, kernel/initrd/menu digests and their provenance
binding, then atomically publishes an immutable `homelab/TAG/` folder. A rerun
verifies an existing staged copy rather than overwriting it. The generated
top-level `homelab.ipxe` is atomically updated to the selected release only
after verification. Earlier release directories stay intact for clients that
are already booting; prune only after all such clients have finished.
A staging lock rejects overlapping syncs; retry later. After a host crash,
inspect for an orphaned `.homelab-sync-lock` before removing that empty directory.

Never put `GH_TOKEN`, GitHub download URLs with signed credentials, userinfo,
query tokens, infrastructure secrets or passwords into iPXE, DHCP, kernel
arguments, templates or web-served files. The script accepts only plain HTTP(S)
asset URLs and rejects credentials/query strings and iPXE expression injection.
The release image/menu itself contains no LAN hostname or credentials.

**LAN HTTP is a trust boundary:** any allowed client can fetch the staged
private source/history and installer, and an on-path attacker could alter
unsigned HTTP traffic. Restrict HTTP and PXE/DHCP to an isolated provisioning
VLAN or trusted LAN, firewall the staging server, and never expose its assets
publicly. HTTPS requires trust support in both your iPXE build and initramfs;
do not bypass certificate checks to make it work. Checksums verified by the
staging host do not authenticate a subsequently modified HTTP transfer.

## Add the custom menu in netboot.xyz

In the netboot.xyz custom menu (for Docker, edit the existing `custom.ipxe` in
the web configuration interface; preserve other entries), add an entry that
chains:

```ipxe
chain http://192.168.1.50:8080/homelab.ipxe || goto failed
```

For a complete minimal `custom.ipxe`, use:

```ipxe
#!ipxe
chain http://192.168.1.50:8080/homelab.ipxe || goto failed
exit
:failed
echo Unable to load the homelab installer menu
prompt Press a key to return... ||
exit 1
```

For an Ansible/self-hosted netboot.xyz deployment, enable
`custom_generate_menus: true` and provide `custom/custom.ipxe` as described in
the official self-hosting guide. For the official Docker container, a dedicated
host directory mounted as `/assets` is served on its asset HTTP port (commonly
8080). Point `--web-root` at that **host** directory and `--base-url` at its
LAN-visible URL; do not stage into the container's menu/config credential area.
The staging tool writes `homelab.ipxe`, not your existing `custom.ipxe`.

From netboot.xyz's iPXE shell you can also type the `chain` line directly.
Provide the LAN URL, not a GitHub repository URL. A repo URL is supplied to the
**staging command**, where authentication is safe, not to the PXE client.

## Requirements and acceptance checks

- x86-64/amd64, working wired DHCP and a NIC supported by both iPXE and Ubuntu.
  Keep the existing BIOS/UEFI-specific netboot.xyz loader selection.
- Prefer **16 GB RAM or more**: Casper downloads the roughly 6 GB Desktop ISO
  into RAM in addition to initrd/live userspace. This is not a tiny netinstaller.
  Future ISO growth can raise that requirement. Use USB if memory is insufficient.
- Serve large files without auth redirects or download limits; test the LAN
  URLs for `homelab/TAG/{vmlinuz,initrd,installer.iso,homelab.ipxe}` from the
  provisioning VLAN. The HTTP server must allow multi-GB ISO responses.
- First boot still needs apt, PyPI, Ollama and model downloads; private API
  tokens/configuration remain manual. Remediation stays disabled.

CI extracts real PXE assets from the verified ISO, checks digests and provenance,
tests staging, idempotency and URL safety with fixtures, and stages/HTTP-downloads
the actual kernel, initrd, ISO and menu while comparing full digests.
BIOS/UEFI VM checks boot the ISO itself; they are **not a complete PXE deployment**.
DHCP, physical NICs, iPXE firmware/UEFI trust, memory headroom, a full HTTP ISO
boot and interactive install remain provisioning-network/hardware acceptance
checks. No live DHCP/router configuration or physical installation is performed.

## Upstream references used

- [netboot.xyz custom/self-hosted menus](https://netboot.xyz/docs/selfhosting/#self-hosted-custom-options)
- [netboot.xyz Docker assets and ports](https://netboot.xyz/docs/docker/usage/)
- [Official netboot.xyz Ubuntu iPXE implementation](https://github.com/netbootxyz/netboot.xyz/blob/master/roles/netbootxyz/templates/menu/ubuntu.ipxe.j2)
  uses kernel/initrd and an ISO URL, not arbitrary ISO chainloading.
- [Ubuntu Noble Casper boot parameters](https://manpages.ubuntu.com/manpages/noble/man7/casper.7.html)
  documents `boot=casper`, `netboot=url` and `iso-url=`.
- [Ubuntu network installer overview](https://discourse.ubuntu.com/t/netbooting-the-live-server-installer/14510)
  describes extracting the kernel/initrd and downloading/mounting the ISO;
  modern releases accept `iso-url=` to avoid cloud-init downloading it twice.
- [Ubuntu media-based autoinstall](https://canonical-subiquity.readthedocs-hosted.com/en/latest/tutorial/providing-autoinstall.html#autoinstall-on-the-installation-media)
  describes loading `autoinstall.yaml` directly from the ISO.
