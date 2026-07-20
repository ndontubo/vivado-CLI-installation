# RESEARCH_NOTES.md

Source material behind the decisions in ARCHITECTURE.md, gathered before
this project started. Keep this updated as new findings come in during
implementation — especially anything that contradicts an assumption here.

## Confirmed facts

- AMD/Xilinx does not ship a macOS build of Vivado. Only Windows and
  RHEL/CentOS/Ubuntu LTS Linux are officially supported.
- Vivado's Linux binaries are x86_64 only — they will not run on ARM64
  without translation (Rosetta) or emulation (QEMU x86 mode).
- A full Vivado install footprint is roughly 100GB+; guides recommend
  150–200GB free disk space to be safe.
- macOS version numbering moved to a year-aligned scheme (e.g. "macOS 26")
  as of the 2025/2026 releases. Confirmed via real-hardware `doctor` testing
  (macOS 26.4) that a simple `(major, minor) >= (13, 0)` tuple comparison
  handles this correctly with no special-casing needed — worth noting only
  because it was a real, if minor, point of uncertainty before it was
  tested against an actual machine running it.

## Phase 1 finding: Rosetta-for-Linux is a Virtualization.framework-only API

The single biggest research correction of the project so far. The original
plan (see below, "Evidence that Rosetta-accelerated ARM64 VM approach
works") assumed driving `qemu-system-aarch64` directly, with `-accel hvf`,
would get Rosetta support the same way UTM's "Apple Virtualization" backend
does. This is wrong, and it's a hard API boundary, not a compatibility
question:

- Rosetta's virtiofs directory share is exposed only via Apple's own
  `VZLinuxRosettaDirectoryShare` API, part of Virtualization.framework.
  A UTM maintainer confirmed directly: Rosetta is only available in the
  Virtualization.Framework backend, not UTM's separate QEMU backend.
- Every adjacent tool that supports Rosetta (Docker Desktop, Podman
  machine, Rancher Desktop, Colima) gates it behind a VZ-based launch mode
  specifically — never their QEMU mode. Colima users have hit cases where
  requesting `--vm-type=vz` still silently launched QEMU instead, which is
  its own bug but underscores how separate the two code paths are.
- Conclusion: raw `qemu-system-aarch64`, however configured, cannot reach
  this API. A VZ-based launcher is required.

**Chosen replacement: [`vfkit`](https://github.com/crc-org/vfkit)**
(Apache-2.0, maintained by Red Hat's CRC team, installable via
`brew install vfkit`). A small CLI hypervisor built directly on
Virtualization.framework (via the `Code-Hex/vz` Go bindings) — the same
underlying mechanism `podman machine`, `minikube`'s `vfkit` driver, and
Colima's `vz` mode use. Chosen over writing a custom Swift/VZ launcher
from scratch because it already solves the boring, easy-to-get-subtly-wrong
parts: EFI boot, cloud-init ISO generation, NAT networking, serial console
logging, and a REST API for VM state — all as documented CLI flags,
Homebrew-installed, no custom code-signing/entitlements work needed (brew
bottles ship pre-signed correctly).

Confirmed via real testing (see MANUAL_CHECKLIST.md's Run 3): `vfkit
--device rosetta,mountTag=rosetta,install` genuinely works —
`/proc/sys/fs/binfmt_misc/rosetta` shows `enabled` inside the guest, and a
static x86_64 binary executed correctly, on both the Ubuntu 20.04 and
Debian 12 pairings.

## Phase 1 finding: disk format changed from qcow2 to sparse raw

Apple Virtualization Framework — which `vfkit` sits on top of — has no
qcow2 support at all, only raw and ISO images (this is `vfkit`'s own
documented limitation). The original "thin-provisioned qcow2" decision in
ARCHITECTURE.md doesn't work with a `vfkit`-based launcher.

Replacement: a sparse raw file, created as a copy-on-write clone
(`cp -c` / `clonefile(2)`) of a cached "golden" raw image (itself produced
once via `qemu-img convert -O raw` from the official qcow2 download). APFS
natively supports both sparse files and clonefile, which together give the
same practical space-efficiency qcow2 was originally chosen for. Confirmed
on real hardware: a 20GB virtual disk (`truncate -s 20G`) consumed only
~1.6GB of actual space (`du -h`) immediately after `init`.

## Phase 1 finding: guest IP discovery needed three iterations to get right

`vfkit`'s NAT networking (`VZNATNetworkDeviceAttachment`) lets the host
reach the guest directly at its DHCP IP, discovered via macOS's own
`/var/db/dhcpd_leases` — this part of the plan (from `vfkit`'s own
quickstart guide) was correct from the start. Getting the *matching logic*
right took three real-hardware-driven fixes:

1. `vfkit`'s docs recommend matching by MAC address (`hw_address=` in the
   lease file). Ubuntu 20.04's `systemd-networkd` sends a DHCP client
   identifier (a type-255 DUID, RFC 4361) by default instead of the raw
   MAC, so this never matched in practice. Switched to matching on DHCP
   hostname (`name=`) instead, which cloud-init sets from our own
   `hostname:` config.
2. macOS's `bootpd` never prunes old lease entries. Repeated
   `destroy`/`init` cycles under the same hostname accumulate multiple
   `name=` matches — taking the first one found can return a long-dead
   VM's stale IP. Fixed by parsing each entry's `lease=` value (a
   monotonically increasing counter) and taking the most recent one.
3. A freshly cloned disk's first boot can renegotiate DHCP under a new
   identity partway through — most likely `systemd-machine-id-setup`
   finalizing the real machine-id after networking already made an early
   request under a transient one. Observed directly as the resolved IP
   changing mid-boot. Fixed by re-resolving the IP on every SSH-readiness
   retry instead of resolving once up front.

## Phase 1 finding: `vfkit` has no host-USB-passthrough path (yet)

Checked directly against `vfkit`'s documented device set (`doc/usage.md`):
`virtio-net`, `virtio-blk`, `virtio-fs`, `nvme`, `usb-mass-storage`
(image/ISO-backed only, not a real host device), `nbd`. No option to map a
real host USB device into the guest. No open `vfkit` issue or PR proposing
one was found either.

The underlying OS capability exists but is unused by any VZ-based tool so
far: Apple added `VZXHCIController` and `VZUSBDevice` to
Virtualization.framework in macOS 15. There's live upstream interest in
exactly this — a still-open feature request on Apple's own `container`
project (`apple/container#1301`, opened March 2026) asks for native USB
passthrough via those APIs, explicitly for JTAG/SWD debugging and embedded
device flashing — essentially this project's own Phase 3 need. Worth
re-checking whether `vfkit` (or another VZ tool) has picked this up by the
time Phase 3 starts; for now, planning assumes no passthrough path and
treats XVC/`xvcd` (see below) as the primary approach rather than a
fallback.

## Field data from Phase 0 `doctor` testing (real Apple Silicon hardware)

Distinct from the Vivado-installer-specific estimates below, which still
need phase 2 verification — this section is about what was learned running
`doctor` itself against a real, moderately-used Mac (228GB total disk,
8GB RAM, macOS 26.4).

- **A "big" disk can still be nearly full in ways `du` doesn't show.** The
  test machine reported only 16GB free out of 228GB total. Recursive `du`
  against `~/Library` and the rest of the home folder only accounted for
  roughly 60–70GB — nowhere near the ~196GB `df` reported as actually used.
  The gap was local `com.apple.os.update-*` APFS snapshots (macOS's own
  pre-update rollback snapshots), which don't show up under a plain `du`
  because of how APFS snapshots/clones work. `sudo tmutil thinlocalsnapshots
  / <bytes> 4` reclaimed ~19GB in this case. This is a genuine, common
  failure mode worth documenting since it's exactly the kind of thing that
  would make a user wrongly conclude `doctor`'s disk check is broken or
  overly conservative, when the real issue is hidden snapshot usage.
- **8GB RAM is a real scenario, not just a hypothetical low end.** The test
  machine has 8GB RAM, well under the 16GB recommendation. `doctor`
  correctly treated this as a warning rather than a hard block, which
  matches the design intent — worth flagging as validated, since the
  alternative (hard-blocking under 16GB) would have made `doctor` unusable
  on real hardware people actually have.
- **`~/Library/Caches` and Docker Desktop's reported "disk limit" are easy
  to misread.** Docker Desktop shows a large configured disk *limit*
  (e.g. "223GB") that has nothing to do with actual usage (which was ~5GB
  on the test machine) — worth remembering if a future troubleshooting
  guide ever tells people to check Docker's disk usage as a space-recovery
  step; the limit number is not the usage number.

## Disk footprint breakdown (ESTIMATES — verify in phase 2)

The generic 150GB figure decomposes roughly as:
- Host tooling (QEMU, `vfkit`, cloud image cache, XQuartz): ~1–1.2 GB
- Guest base OS + Vivado deps: ~4–6 GB
- Installer archive in-VM (temporary): ~15–25 GB (full offline installer;
  the small web installer is ~300MB but pulls the rest down during install)
- Installer extraction temp (temporary): ~15–25 GB
- Vivado installed, full device families: ~100–120 GB
- Vivado installed, Artix-7 only (Basys 3 needs only this): ~30–50 GB modern
- Per-project build artifacts headroom: ~10–20 GB

Peak (during install) > steady-state, because the installer archive + its
extraction temp must coexist with the partial install, then get reclaimed:

| Scenario | Peak install | Steady-state |
|---|---|---|
| Full modern, all families | ~155 GB | ~131 GB |
| Modern, Artix-7 only, thin qcow2 | ~90 GB | ~60–70 GB |
| Vivado 2018, Artix-7 only | ~20–30 GB | ~15–25 GB |

Mitigations that lower the real requirement: sparse raw disk images (see
Phase 1 finding above — replaces the original qcow2-based mitigation, same
practical effect); delete installer archive after install; limit device
families to Artix-7; `--storage-path` to an external SSD. ALL these numbers
are estimates until measured against a real installer — this has NOT
changed based on the Phase 0/1 field testing above, since that testing was
about the host Mac's own free space and `doctor`/`init`'s own logic, not
about Vivado's actual install footprint. Phase 2 (real installer) is still
required to replace these numbers with measured ones.

## Vivado version choice — size vs. compatibility trade-off

Vivado 2018 is dramatically smaller than modern releases (~8GB cited for a
device-limited/compressed form, vs. 30GB+ trimmed modern / 100GB+ full).
It fully supports Artix-7 (xc7a35t), so NO device capability is lost for a
Basys 3 target. Attractive for disk-constrained Macs.

BUT: Vivado 2018 was built against ~2016–2018-era Linux libraries (Ubuntu
16.04/18.04, older glibc). Running it on a modern distro like Debian 12
(2023) widens the version gap and increases library-compatibility friction
(more `libtinfo5`/`libncurses5`-style shimming, higher breakage risk).

Conclusion → couple the Vivado version with an era-appropriate guest OS:
- Vivado 2018 → Ubuntu 20.04 (or 18.04) guest
- Modern Vivado → Debian 12 guest
The Medium guide below independently supports this: it used Ubuntu 20.04
specifically because newer distros broke Vivado's dependencies. The exact
"8GB" figure and the 2018-on-20.04 dependency set both still need direct
verification against a real installer before being hardcoded.

**Real-world grounding:** the reference test machine (disk-constrained,
8GB RAM) is exactly the profile this pairing was designed for, and
`doctor --vivado-version 2018` passed clean on it once genuinely-free disk
space was recovered (46GB free vs. ~30GB peak needed). This doesn't confirm
the 2018/Artix-7 footprint estimate itself (still needs phase 2), but it
does confirm the pairing strategy is solving a real problem for a real
machine, not a hypothetical one. Phase 1 went further: `vivado-mac init
--vivado-version 2018` actually provisioned and booted a working VM with
confirmed Rosetta support on this exact machine.

## Evidence that Rosetta-accelerated ARM64 VM approach works

A university course (50.002 CS) published a working setup: Debian 12 +
Rosetta, running under UTM on Apple Silicon, with Vivado 2025.2 and
Alchitry Labs V2 pre-installed. Tested on M2 Max Mac Studio and M2 MacBook
Air. They successfully generated bitstreams and flashed an Alchitry Au
FPGA. This was the strongest existing evidence that the Rosetta-for-Linux
path (not full x86 emulation) is viable for Vivado specifically — note
this was via UTM's Virtualization.framework backend, consistent with the
Phase 1 finding above that this only works through that API, not raw QEMU.
Source: https://natalieagus.github.io/50002/fpga/fpga_applesilicon

## Evidence that Windows-guest + Parallels/VMware also works

A Digilent forum user reported success running Vivado/Vitis 2025.1 inside
a Windows 11 VM (via Parallels) on both M1-Max and M2 Apple Silicon
MacBooks, including full simulate/implement/bitstream-write to a physical
Basys 3 board attached to the Mac. They noted performance was "almost
native speed" on modern ARM Macs. This is a Windows-guest path, not our
chosen Linux-guest path, but it's useful evidence that USB passthrough to
real boards is achievable on Apple Silicon at all, and gives a fallback
architecture if the Linux+Rosetta path hits a wall.
Source: https://forum.digilent.com/topic/32526-report-on-vivado-working-on-arm-macos-to-program-basys-3/

## Evidence for pure QEMU emulation (no Rosetta) as a fallback

A Medium walkthrough demonstrates UTM's "Emulate" (QEMU x86_64 emulation,
not Rosetta translation) mode running Vivado on M1/M2/M3, using Ubuntu
20.04 specifically because newer Ubuntu versions "sometimes break Vivado's
dependencies." Confirms Ubuntu 20.04 as a known-safe fallback guest version
if Debian 12 causes dependency issues, and lists the specific apt packages
Vivado needs: build-essential, libtinfo5, libncurses5, libncurses-dev,
libglu1-mesa, libxtst6, libxrender1, libxi6, unzip.
Source: https://medium.com/@burakscha/running-xilinx-vivado-on-your-m1-m2-m3-mac-482badb89de4

## Evidence for the Docker approach (why we're not using it as primary)

An open-source repo (yokeTH/vivado-mac — note: same rough project name,
different author, worth checking on GitHub before we settle on our repo
name) provides Docker + XQuartz environment setup for running Vivado on
Apple Silicon. It stops short of a full automated pipeline: user still
supplies and manually runs the Vivado installer inside the container, and
the README documents known friction points (Docker memory limits needing
manual increase, JVM install killed under default resource limits). Useful
prior art for the X11/XQuartz forwarding approach, but Docker's weaker
USB/JTAG passthrough support (compared to a VM with real USB passthrough)
is why we're going VM-first, not container-first.
Source: https://github.com/yokeTH/vivado-mac

## Open items to verify once implementation continues

- Whether Rosetta-for-Linux handles ALL of Vivado's toolchain (synthesis,
  implementation, simulator, hardware manager/JTAG tools) or only some —
  Phase 1 only confirmed Rosetta works for a trivial static binary, not
  Vivado's actual tools. Needs Phase 2's real installer.
- Exact package list needed for Vivado 2025.2 on Debian 12 specifically
  (the confirmed package list above is from a Medium post using Ubuntu
  20.04 + Vivado, an older/different combination — verify against Debian
  12 + newer Vivado before hardcoding).
- Whether AMD's Vivado installer supports a documented silent/unattended
  install mode with a config file (commonly `install_config.txt` in
  Xilinx installers) — assumed yes based on general Xilinx installer
  behavior, needs direct confirmation against the current installer.
- Whether the same local-snapshot disk-usage gotcha found during Phase 0
  field testing (see above) could also apply *inside* the guest VM once
  Vivado's install is underway — worth a quick check during phase 2 VM
  disk-usage debugging if numbers look off.
- Whether `vfkit` (or another VZ-based tool) has added host-USB-device
  passthrough by the time Phase 3 starts — see the Phase 1 finding above.
