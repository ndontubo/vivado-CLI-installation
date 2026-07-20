# ARCHITECTURE.md

Technical design and the reasoning behind each decision. Update this doc
whenever a decision changes — it should stay the source of truth, not the
commit history.

## The core problem

Vivado ships only as x86_64 Linux (RHEL/Ubuntu) or Windows binaries. Apple
Silicon Macs are ARM64. To run Vivado we need an x86_64 (or Rosetta-translated
ARM64) Linux environment, with:
- Enough performance that synthesis/implementation runs don't take hours
- USB device passthrough so JTAG programming cables reach real hardware
- A path to see the Vivado GUI (X11 forwarding or a VNC/RDP-like protocol)

## Virtualization approach — CORRECTED during Phase 1 build

**Original decision (Phase 0 era, WRONG as stated): "drive QEMU directly via
Apple's `Virtualization.framework` Rosetta support, not the UTM GUI app."**

This turned out not to be possible. Rosetta-for-Linux's virtiofs directory
share is a `Virtualization.framework`-only API (`VZLinuxRosettaDirectoryShare`
in Apple's own docs). It is reachable only by a process built directly on
that framework. A plain `qemu-system-aarch64` process — even fully
HVF-accelerated with `-accel hvf` — is a completely separate hypervisor
backend with no path to that API. This isn't a compatibility question that
more testing would resolve; it's a hard API boundary. Confirmed independently
across several projects that hit the same wall: UTM's own maintainers state
Rosetta is only available through their Virtualization.framework backend, not
their QEMU backend; Colima only gets Rosetta when it launches a VM in `vz`
mode rather than QEMU mode (and users have hit cases where requesting `vz`
mode silently fell back to launching QEMU instead); Docker Desktop, Podman
machine, and Rancher Desktop all gate Rosetta behind their VZ-based backend
specifically, never their QEMU backend.

**Corrected decision: drive [`vfkit`](https://github.com/crc-org/vfkit)
(Apache-2.0, `brew install vfkit`) instead of raw `qemu-system-aarch64`.**

`vfkit` is a small, actively-maintained CLI hypervisor built directly on
Virtualization.framework — the same mechanism podman, minikube, and colima's
`vz` mode use for their own Rosetta support. This keeps the original intent
("no dependency on a GUI app like UTM being installed/updated/signed
correctly") fully intact, while actually getting Rosetta:

- `vfkit --device rosetta,mountTag=rosetta,install` shares Apple's Rosetta
  binary into the guest over virtiofs and (with `install`) triggers
  installing it on the host if missing. Deliberately *not* combined with
  vfkit's `ignoreIfMissing` option — if Rosetta genuinely can't be installed,
  `init`/`start` should fail loudly, not silently boot a VM without the one
  thing this whole project depends on.
- `vfkit` handles EFI boot (`--bootloader efi,variable-store=...,create`),
  cloud-init ISO generation (`--cloud-init user-data,meta-data` — it builds
  the ISO itself, no `hdiutil`/`mkisofs` needed), NAT networking, serial
  console logging, and a REST API (`--restful-uri`) for querying/changing VM
  state — all as flags on a single static binary, installed via Homebrew
  like the rest of this project's dependencies.
- `qemu-img` (from the existing `qemu` Homebrew formula) is still a
  dependency, but only as an **offline image-conversion tool** — see the
  disk section below — not as the VM launcher. `doctor`'s QEMU check was
  renamed to make this explicit and a new `vfkit` check was added
  alongside it.
- Fallback, unchanged in spirit from the original doc: if Rosetta proves too
  fragile for Vivado's specific tool set even via `vfkit` (some tools trip on
  Rosetta), `vfkit` can still boot an x86_64 VM under full software emulation
  as a slower, zero-compatibility-risk fallback. This is a `vfkit` config
  change, not an architecture change.

## Guest OS — coupled to the target Vivado version

**Key insight: the guest OS choice is not independent of the Vivado version.
Pick them as a matched pair, because old Vivado on a modern distro means
more library-compatibility fighting.**

Vivado binaries link against the glibc/library versions of their era. Running
an old Vivado on a much newer distro (bigger version gap) means more
compatibility shims (`libtinfo5`, `libncurses5`, etc.) and a higher chance of
subtle breakage. Pair the Vivado version with an era-appropriate OS.

Two supported pairings, chosen by what the user prioritizes:

**Pairing A — smallest footprint (disk-constrained Macs):**
- Vivado **2018.x** + Ubuntu **20.04** ARM64 guest
- Vivado 2018 is dramatically smaller than modern releases (single-digit-to
  low-teens GB for a device-limited install vs. 30GB+ modern). Fully supports
  Artix-7 (xc7a35t), which is all a Basys 3 needs — no device capability lost.
- Ubuntu 20.04 is the era-appropriate OS: RESEARCH_NOTES documents a guide
  that used 20.04 specifically because newer distros break older Vivado's
  dependencies. This avoids the worst of the glibc mismatch.
- Trade-off: 2018 is an old toolchain (older synthesis results, no newer IP),
  but fine for learning, small designs, and Basys 3 work.
- **This is the pairing chosen for the reference test machine** (16GB RAM,
  disk-constrained) — see MANUAL_CHECKLIST.md's test run log. Real-world
  confirmation that Pairing A's target audience (disk-constrained Macs) is
  not a hypothetical scenario. `vivado_mac/vm.py`'s `IMAGE_INFO["2018"]`
  points at Ubuntu's official `ubuntu-20.04-server-cloudimg-arm64.img`
  release build (checksum-verified against Ubuntu's own `SHA256SUMS`).

**Pairing B — modern toolchain (disk-rich Macs):**
- Current Vivado (2024/2025.x) + Debian 12 (bookworm) ARM64 guest
- The one confirmed working prior example (university course VM) used
  Debian 12 + Rosetta + Vivado 2025.2 successfully.
- Larger footprint (see disk section), but current tooling and IP.
- `IMAGE_INFO["modern"]` points at Debian's official
  `debian-12-generic-arm64.qcow2` release build (checksum-verified against
  Debian's `SHA512SUMS`). Unlike the Ubuntu 20.04 filename, this hasn't yet
  been confirmed against a real download on real hardware — Debian's arm64
  generic-image naming follows their documented, standard convention, but
  MANUAL_CHECKLIST.md should confirm it the first time Pairing B is actually
  run through `init`.

**Decision: support both as a `--vivado-version` / guest-OS matched pair; do
NOT hardcode a single guest OS.** Default recommendation depends on the disk
check in `doctor` — if the Mac is tight on space, steer toward Pairing A.
This is a decision to *confirm by testing* both against real installers in
phase 2, not a locked fact — the exact 2018 footprint and the 2018-on-20.04
dependency set both need verification.

Both distros publish their official cloud images as **qcow2**, not raw —
see the disk section below for why that no longer matches what the VM
launcher can consume directly, and how that's handled.

## Provisioning

**Decision: cloud-init on first boot, then idempotent bash scripts over SSH
for anything after.**

- `vm.py`'s `write_cloud_init()` generates `user-data`/`meta-data` directly
  (no template files on disk yet — small enough to inline; revisit if Phase
  2's provisioning needs grow past a single user + SSH key + `runcmd`).
  Passed to `vfkit` via `--cloud-init`, which builds the ISO itself.
- `src/provision/*.sh` (Phase 2+): scripts pushed and run over SSH for
  anything that needs to happen after Vivado is manually placed in the VM —
  installing Vivado's Linux dependency packages (`libtinfo`, `libncurses`,
  etc.), configuring cable drivers, setting udev rules for USB JTAG devices.

## USB / JTAG passthrough

Known hard part. **Confirmed during Phase 1 research: `vfkit` does NOT
currently support host USB device passthrough.** Its documented device set
(`doc/usage.md`) covers `virtio-net`, `virtio-blk`, `virtio-fs`, `nvme`,
`usb-mass-storage` (image-backed only — mounts a raw/ISO file as a virtual
USB drive, not a path to a real host USB device), and `nbd`. No option to
map a host USB device into the guest was found, and no open `vfkit` issue
or PR was found proposing one either.

The underlying OS capability does exist, just not wired up by any
VZ-based tool yet: Apple added `VZXHCIController` and `VZUSBDevice` to
Virtualization.framework in macOS 15. There's recent, active upstream
interest in exactly this — a still-open feature request on Apple's own
`container` project (apple/container#1301, opened March 2026) asks for
native USB passthrough via those same APIs, explicitly citing JTAG/SWD
debugging and embedded-device flashing as the motivating use case, which
is essentially this project's own Phase 3 need. Worth re-checking whether
`vfkit` (or `apple/container`, or another VZ-based tool) has picked this up
by the time Phase 3 actually starts — this is clearly live upstream
interest, not a dead end — but for planning purposes right now, assume no
passthrough path exists and treat XVC/`xvcd` (see RESEARCH_NOTES.md) as the
primary Phase 3 approach rather than a fallback.

Plan:
- Phase 1: get bitstream generation working with no hardware in the loop.
- Phase 2 (see ROADMAP.md): tackle passthrough as its own milestone, tested
  against one specific board (Basys 3) before generalizing, starting from
  the XVC/`xvcd` approach given the finding above.

## GUI access

Vivado's GUI needs a display. Options, in order of preference:
1. X11 forwarding over SSH (`ssh -X`), using XQuartz on the Mac host. Known
   to work (existing Docker-based guide uses this). Simple, no extra guest
   services.
2. VNC/RDP server in the guest, viewed via a native macOS client. More setup,
   but better performance for a GUI-heavy app like Vivado. Consider only if
   X11 forwarding proves too slow in testing.

Start with (1) since it's proven and simpler; revisit if performance is bad.
(`vfkit` also has its own `--gui`/`virtio-gpu` path for a native VM window,
but that's for graphical guests generally — X11 forwarding over the SSH
connection `init`/`status` already establish is still the simpler path for
just Vivado's GUI specifically.)

## Disk footprint & storage strategy — CORRECTED during Phase 1 build

**Original decision (Phase 0 era, no longer accurate): "thin-provisioned
qcow2 disk image."**

Apple's Virtualization Framework — which `vfkit` sits directly on top of —
has no qcow2 support at all; only raw disk images and ISO images. This is
`vfkit`'s own documented limitation, not a workaround-able gap.

**Corrected decision: sparse raw disk image, created as a copy-on-write
clone of a cached "golden" raw image.**

APFS (macOS's default filesystem) natively supports sparse files and
copy-on-write clones, which together give the same practical benefits qcow2
was chosen for:

- **Golden image, built once per pairing:** the official qcow2 cloud image is
  downloaded, checksum-verified against the distro's own published
  checksums, then converted once via `qemu-img convert -O raw` into a cached
  raw "golden" image under `<base>/images/`. This is the only step that
  needs the network and needs to read/write the full image size.
- **Per-VM disk, created on every `init`:** a copy-on-write clone of the
  golden raw image via `cp -c` (`clonefile(2)`) — instant, and consumes zero
  additional disk space at creation time, same property qcow2's backing-file
  mechanism gave us. Then grown to the target virtual size with `truncate`,
  which is also sparse — a 35GB `truncate -s 35G` target still only consumes
  real space as data is actually written.
- **`--storage-path` flag, unchanged in intent:** still redirects both the
  golden-image cache and the per-VM disk to an external SSD if internal
  space is short — same flag, same behavior, just pointed at raw files
  instead of qcow2 files now.
- **Device-family-limited install** (Phase 2) and **deleting the installer
  archive post-install** are unaffected by this change — both are about
  what happens *inside* the guest's own filesystem, not the host-side disk
  image format.

**Estimated footprints (verify against real installers in phase 2 — these
are estimates, not measured):**

| Scenario | Peak during install | Steady-state after |
|---|---|---|
| Full modern install (all families) | ~155 GB | ~131 GB |
| Modern, Artix-7 only, thin-provisioned | ~90 GB | ~60–70 GB |
| Vivado 2018, Artix-7 only (Pairing A) | ~20–30 GB | ~15–25 GB |

Peak > steady-state because the installer archive + its extracted temp
files must coexist with the partial install, then get reclaimed. `doctor`
must gate on the **peak** figure for the chosen pairing, since running out
mid-install is the failure mode — but it should explain that installer
space is reclaimable and suggest `--storage-path` if internal space is
short, rather than just blocking.

**Real-world note (Phase 0 field test):** on the reference test machine, a
seemingly-large 228GB disk had only 16GB genuinely free — the gap was
mostly local `com.apple.os.update-*` snapshots (macOS's own pre-update
rollback snapshots), not user files. `du` against `~/Library` and `~/`
alone undercounted actual usage by well over 100GB because APFS snapshots
don't show up under a plain recursive `du`. This is a real, not
hypothetical, failure mode for disk-constrained Macs and is exactly the
scenario `doctor`'s peak-footprint gate + `--storage-path` flag exist for.
Not something `vivado-mac` should try to clean up automatically (out of
scope, and `tmutil thinlocalsnapshots` requires `sudo`) — but worth noting
in `doctor`'s disk-fail message as a common thing to check if free space
looks surprisingly low. **Open item:** consider adding a one-line hint to
`check_disk_space()`'s fail message suggesting `tmutil listlocalsnapshots /`
as a first thing to check, since it's a very common and easily-reclaimed
source of "phantom" disk usage. Not yet implemented — flagging as a
candidate improvement, not a decision.

## Networking — NEW, Phase 1, CONFIRMED on real hardware

**Decision: `vfkit --device virtio-net,nat,mac=<generated>`, IP discovered
via `/var/db/dhcpd_leases` matching on DHCP hostname (not MAC, and not the
first match — see below).**

`vfkit`'s NAT networking uses Apple's `VZNATNetworkDeviceAttachment`, which
(unlike QEMU's SLIRP user-mode networking) lets the host reach the guest
directly at its DHCP-assigned IP with no port-forwarding flags needed.
Getting the IP-discovery mechanism right took three rounds of fixes against
real hardware, none of which were predictable from documentation alone:

1. **MAC matching doesn't work for this guest OS.** `vfkit`'s own docs
   recommend matching the generated MAC against `hw_address=` in the lease
   file. In practice, Ubuntu 20.04's `systemd-networkd` sends a DHCP client
   identifier (a type-255 DUID, RFC 4361) instead of the raw hardware
   address by default, so `hw_address=` never matches the MAC we told
   `vfkit` to use. Fixed to match on `name=` — the DHCP hostname option,
   which cloud-init sets from our own `hostname:` field — with MAC matching
   kept only as a secondary fallback for guest/networking-stack
   combinations that do send a plain MAC.
2. **Stale leases accumulate and can be matched by mistake.** macOS's
   `bootpd` never prunes old lease entries from `/var/db/dhcpd_leases`.
   Since every VM instance uses the same fixed hostname ("vivado-mac"),
   repeated `destroy`/`init` cycles leave multiple `name=vivado-mac`
   entries in the file — one per VM that ever existed, not just the
   current one. Taking the *first* match (as the hostname fix initially
   did) can return a long-dead VM's IP. Fixed to parse each entry's
   `lease=` value (a monotonically increasing counter/timestamp) and
   return the most recently issued match.
3. **The IP can legitimately change mid-boot.** On a freshly cloned disk's
   first boot, the guest was observed making an early, transient DHCP
   request under one identity, then renegotiating under a *different*
   DUID moments later — most likely `systemd-machine-id-setup` finalizing
   the instance's real machine-id partway through first boot, after
   networking already made an initial request under a throwaway one.
   `init` originally resolved the IP once and waited for SSH at that fixed
   address, which could time out against an address already superseded.
   Fixed (`_wait_for_ip_and_ssh`) to re-resolve the IP on every retry
   instead of once up front.

`/var/db/dhcpd_leases` turned out to be readable without elevated
permissions on the reference test machine — the earlier-flagged open item
about needing `sudo` did not materialize in practice, though `vm.py` still
handles a `PermissionError` gracefully (falls back to a manual-lookup
message) in case that differs on another machine's configuration.

## Host CLI language

**Decision: Python 3 (stdlib-heavy, minimal dependencies).**

Rationale: bash is fine for the provisioning scripts that run *inside* the
VM, but the host-side orchestration (state tracking, argument parsing,
shelling out to `vfkit`/`qemu-img`, polling VM boot state) benefits from
real error handling and structured state (JSON) that's painful in bash.
Avoid heavy frameworks (no Click/Typer dependency unless it becomes clearly
worth it) to keep install friction low — a `pipx install` or single-file
script should be enough to start. `vm.py`'s HTTP calls to `vfkit`'s REST API
use `urllib.request` rather than adding a `requests` dependency, for the
same reason.

## `doctor`'s install-confirmation behavior (Phase 0, implemented)

**Decision: `doctor --fix` never installs anything without an explicit y/n
prompt, unless `--yes` is also passed.**

`doctor` is capable of shelling out to `brew install qemu`, `brew install
vfkit`, and `softwareupdate --install-rosetta --agree-to-license` when
prerequisites are missing and `--fix` is given. Early implementation ran
these immediately once `--fix` was set, with no confirmation. That's too
aggressive for a tool whose main job up to this point has been read-only
inspection — a person running `doctor --fix` to see what's missing
shouldn't be surprised by a live `brew install` firing off.

Implementation: a `_confirm(prompt)` helper prompts `[y/N]:` via `input()`,
defaulting to "no" on any non-`y` answer *and* on `EOFError` (piped/non-
interactive stdin), so a scripted or non-interactive run never silently
installs something. A separate `--yes` flag bypasses the prompt for
deliberate scripted/CI use. This pattern (confirm-by-default, explicit
opt-out for automation) is the template Phase 1's `destroy` command follows
too — the only other command in the CLI so far that mutates/deletes real
state (a VM's disk and cloud-init config) without `--fix`-style gating.

Verified on real hardware: uninstalling QEMU and re-running
`doctor --fix` correctly re-detected it missing, prompted, and on
confirmation ran a live `brew install qemu` that succeeded in the same
invocation (the QEMU check re-verifies after the install attempt rather
than requiring a second `doctor` run). See MANUAL_CHECKLIST.md. The new
`vfkit` check (Phase 1) follows the identical pattern but is UNVERIFIED on
real hardware yet.

## Code layout (Phase 1, as built)

```
vivado-mac/
├── pyproject.toml          # console_scripts entry point: vivado-mac = vivado_mac.cli:main
├── vivado_mac/
│   ├── __init__.py
│   ├── cli.py               # argparse wiring only — no check/lifecycle logic here
│   ├── doctor.py            # all Phase 0 check functions + run_doctor()
│   └── vm.py                # all Phase 1 VM lifecycle logic: state, image
│                             # acquisition, vfkit invocation, init/start/
│                             # stop/status/destroy. Same split as doctor.py:
│                             # one file per phase, not one file per command.
└── tests/
    └── test_doctor.py       # mocked unit tests for doctor's branching logic
                              # (test_vm.py, same pattern, still needed —
                              # see MANUAL_CHECKLIST.md's Phase 1 section for
                              # what's been spot-checked so far vs. what still
                              # needs a proper pytest suite)
```

`cli.py` now routes `doctor` (phase 0) and `init`/`start`/`stop`/`status`/
`destroy` (phase 1). Still no stubs for `install-vivado`/`program` — those
get added when Phase 2/3 are actually built, per the "don't jump ahead"
working style constraint.

## Open questions to resolve during phase 1

- ~~Does Rosetta-for-Linux actually work at all from a scriptable, non-UTM
  CLI?~~ **Resolved: yes, via `vfkit`, not via raw QEMU.** See
  "Virtualization approach — CORRECTED" above.
- ~~Confirm `/var/db/dhcpd_leases` is readable without elevated
  permissions.~~ **Resolved: yes, on the reference test machine.** See
  "Networking" above.
- ~~Confirm whether `vfkit` has any host-USB-device passthrough path.~~
  **Resolved: no, not currently.** See "USB / JTAG passthrough" above.
- ~~The Debian 12 arm64 cloud image filename/URL is unverified.~~
  **Resolved: confirmed working against a real download.** See
  MANUAL_CHECKLIST.md's Run 3.
- Does Rosetta-for-Linux actually handle ALL of Vivado's tools reliably
  (synthesis, implementation, simulator, hardware manager), or only some?
  Does an old (2018) Vivado behave under Rosetta as well as a modern one?
  Still open — needs Phase 2's real installer; Phase 1 only confirmed
  Rosetta works for a trivial static binary, not Vivado's actual toolchain.
- Measure the real disk footprint for each pairing in the table above
  against actual installers, and replace the estimates with measured numbers
  in RESEARCH_NOTES.
- Verify the Vivado-2018-on-Ubuntu-20.04 dependency set actually installs
  cleanly under Rosetta — confirm the exact apt package list needed.
- Licensing UX: Vivado ML Standard (free) needs a node-locked or floating
  license activation step — how much of that can be scripted vs. requires
  the user to click through AMD's own license portal in a browser? (Note:
  2018-era WebPACK licensing flow differs from the modern ML Standard flow —
  confirm which applies to the chosen version.)
