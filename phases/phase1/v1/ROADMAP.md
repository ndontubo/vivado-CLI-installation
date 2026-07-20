ROADMAP.md

Build order. Each phase should be genuinely working and manually verified
before starting the next — this project has too many hardware/OS variables
to build speculatively on top of an unverified layer.

Phase 0 — Environment & prerequisites checker

Status: DONE. Verified on real Apple Silicon hardware — see
MANUAL_CHECKLIST.md's Phase 0 section and test run log for the full
verification record.

Goal: vivado-mac doctor command that checks the host is capable, before
any VM work starts.


 Detect Apple Silicon vs Intel, macOS version
 Smart disk-space check (NOT a flat 150GB gate — that fails needlessly
on a used Mac). Gate on the realistic peak install footprint for the
chosen Vivado-version/OS pairing (see ARCHITECTURE disk table):
~20–30GB for Vivado 2018 Artix-7, ~90GB for modern Artix-7. Account
for thin provisioning — check actual free space vs. realistic need,
not virtual disk size.
 --storage-path flag: if internal free space is short, offer to put
the VM image on an external SSD instead of hard-failing. (Flag
wiring confirmed on real hardware; not yet tested against a
genuinely separate physical volume — see MANUAL_CHECKLIST.md.)
 Check RAM (16GB+ recommended) — soft warning, not a hard blocker
 Check/install QEMU via Homebrew — implemented as an explicit y/n
confirmation prompt before installing anything (--fix to enable,
--fix --yes to skip the prompt for scripted use); see
ARCHITECTURE.md's "doctor install confirmation" section for why this
isn't just a silent auto-install
 Verify Virtualization.framework + Rosetta availability (checks for
the Rosetta binary at /Library/Apple/usr/share/rosetta/rosetta;
installable via the same confirm-then---fix pattern as QEMU)
 Clear, actionable error messages for every failure case (this is a
big part of the value-add over the manual guides — most of them
don't tell you why something failed). Disk-short message states
exactly how much is free, how much the chosen pairing needs, that
installer space is reclaimable, and suggests --storage-path.


Phase 1 — VM bring-up (no Vivado yet)

Status: DONE. Verified on real Apple Silicon hardware for BOTH pairings
— see MANUAL_CHECKLIST.md's Phase 1 section and test run log.

Architecture correction made during this phase: the VM launcher is
vfkit (brew install vfkit), not raw qemu-system-aarch64 as originally
planned — see ARCHITECTURE.md's "Virtualization approach — CORRECTED"
section for why. doctor now checks for both qemu-img (still needed, for
offline qcow2→raw conversion) and vfkit (the actual launcher).

Real bugs found and fixed against real hardware during verification
(see ARCHITECTURE.md and MANUAL_CHECKLIST.md for full detail):


DHCP IP lookup originally matched MAC address (vfkit's own documented
approach) — Ubuntu 20.04's systemd-networkd sends a DUID client-id
instead, so this never matched. Fixed to match on DHCP hostname.
IP lookup returned the first hostname match instead of the most
recent one — since bootpd never prunes old leases, repeated
destroy/init cycles accumulate stale entries under the same hostname,
and the first match could be a long-dead VM. Fixed to pick the highest
lease= value.
Fresh cloud images can renegotiate DHCP under a new identity partway
through first boot (machine-id finalizing after an early transient
request) — observed directly as the IP changing mid-boot. init now
re-resolves the IP while waiting for SSH instead of waiting at a single
address resolved once.


Goal: vivado-mac init creates and boots a bare ARM64 Linux VM with
Rosetta configured, reachable over SSH. Guest OS depends on the chosen
Vivado-version pairing (see ARCHITECTURE): Ubuntu 20.04 for Vivado 2018,
Debian 12 for modern Vivado. Sparse raw disk (copy-on-write cloned from a
cached golden image) with optional --storage-path.


 Download/cache the correct official ARM64 cloud image for the chosen
pairing (checksum-verified against the distro's own SHA256SUMS/
SHA512SUMS) — Ubuntu 20.04 AND Debian 12, both confirmed working
against real downloads
 Create the disk as a sparse, copy-on-write-cloned raw image —
confirmed on real hardware: 20GB apparent size, ~1.6GB actual disk
usage via du
 Generate cloud-init user-data (SSH key, hostname, base packages)
 Boot via vfkit with Virtualization.framework acceleration
 Configure Rosetta virtiofs share via vfkit --device     rosetta,mountTag=rosetta,install — CONFIRMED on real hardware for
both pairings: /proc/sys/fs/binfmt_misc/rosetta enabled, a static
x86_64 busybox binary ran correctly (uname -m → x86_64) on both
Ubuntu 20.04 and Debian 12 guests
 vivado-mac start / stop / status / destroy lifecycle
commands, using vfkit's REST API as the source of truth for VM
state — full round-trip verified on real hardware
 State tracking in ~/.vivado-mac/state.json
 Idempotency verified: init re-run on an existing VM correctly
no-ops; init --force correctly destroys and recreates; destroy
is safe to run on nothing
Exit criteria MET: a fresh Mac can go from brew install to an SSH
prompt inside a working ARM64 Linux VM (both Ubuntu 20.04 and Debian 12
confirmed) with confirmed Rosetta x86_64 execution, in one command, no
manual UTM clicking.


Phase 2 — Vivado install automation

Goal: vivado-mac install-vivado <path-to-installer> gets Vivado fully
installed and launchable (GUI opens via X11 forwarding) inside the VM.


 Confirm the target Vivado version + guest OS pairing (2018+Ubuntu20.04
for smallest footprint, or modern+Debian12) — see ARCHITECTURE
 Measure ACTUAL disk footprint for the chosen pairing and replace the
estimated numbers in ARCHITECTURE's table + RESEARCH_NOTES with
measured values. Feed the real peak number back into doctor's check.
 Delete the installer archive inside the VM after a successful install
to reclaim space (~15–25GB)
 Transfer user-supplied installer into the VM (scp, now that init
establishes SSH access — no virtiofs share needed for this)
 Install the Vivado Linux dependencies for the chosen pairing
(libtinfo5, libncurses5, libglu1-mesa, libxtst6, libxrender1, libxi6,
etc.) — pin exact package set once verified. Note: an OLDER Vivado
(2018) on Ubuntu 20.04 may need a different/larger shim set than
modern Vivado on Debian 12 — verify per pairing, don't assume.
 Drive Vivado's installer in unattended/silent mode (Xilinx installers
support a config-file-driven silent install — use that instead of
scripting the interactive TUI/GUI)
 Verify Vivado launches and the GUI is visible via ssh -X + XQuartz
 Document/automate license activation step as far as legally and
technically possible (likely still requires user to complete AMD's
own web-based license flow once)
Exit criteria: Vivado ML Standard installed from a user-supplied
installer, GUI visible on the Mac, project can be created and a design
synthesized (no hardware yet).


Phase 3 — USB / JTAG passthrough

Goal: vivado-mac program --board basys3 flashes a real board.


 (researched, not hardware-testable) Confirmed vfkit does NOT
currently support host USB device passthrough — its documented
device set has no such option (only image-backed usb-mass-storage).
The underlying OS capability exists (Apple added VZXHCIController/
VZUSBDevice to Virtualization.framework in macOS 15), and there's
recent (March 2026) upstream interest in exactly this for embedded/
JTAG use cases (see e.g. apple/container#1301), but no VZ-based tool
has wired it up as of this check. Re-check when Phase 3 actually
starts in case that's changed. Plan for now: assume no passthrough
path via vfkit and go straight to the XVC/xvcd fallback below.
 Install/verify Digilent (or relevant vendor) cable drivers inside VM
 Configure udev rules for the target board's USB VID/PID
 Evaluate the XVC/xvcd fallback documented in RESEARCH_NOTES.md as
the primary Phase 3 approach, not just a fallback, given the USB
passthrough finding above
 Test end-to-end: generate bitstream, flash to physical Basys 3,
confirm it runs on hardware
Exit criteria: a real bitstream reaches a real board without the user
touching vfkit flags or udev rules by hand.


Phase 4 — Polish & distribution


 brew install vivado-mac tap, or single-command install script
 Better error recovery (partial-failure states, retry logic)
 Support a second board (generalize the passthrough config)
 Write up the project properly (blog post / README) — useful resume
and portfolio material given the neuromorphic router project this
supports
 Decide whether to build the menu bar app on top of this CLI (separate
future project, see original discussion)


Explicitly out of scope for now


Intel Mac support
Windows guest path (Linux guest is the only target — matches AMD's
primary-supported Linux distros)
Bundling/redistributing any Xilinx binaries or licenses
GUI/menu bar app (phase 5+, separate project)