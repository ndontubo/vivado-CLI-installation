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

## Virtualization approach

**Decision: drive QEMU directly via Apple's `Virtualization.framework`
Rosetta support, not the UTM GUI app.**

Rationale:
- UTM is itself a GUI wrapper around QEMU. Scripting "UTM" really means
  either shelling out to `utmctl` (limited, GUI-app-dependent) or just using
  QEMU underneath directly. Going direct gives full control and no
  dependency on a third-party app being installed/updated/signed correctly.
- Apple added Rosetta-for-Linux support in macOS 13+: a Linux VM can mount
  Apple's Rosetta binary via a virtiofs share and transparently run x86_64
  ELF binaries at near-native speed, without a slow instruction-level QEMU
  emulation. This is what makes Vivado (x86_64-only) viable in an ARM64 VM
  guest at usable speed. Docker Desktop and UTM's "Rosetta" mode both use
  this same OS feature.
- Concretely: guest is an **ARM64** Debian/Ubuntu, not an emulated x86_64
  Debian/Ubuntu. Rosetta translates the x86_64 Vivado binaries at the guest
  OS level. This is faster than QEMU's `-cpu` full x86_64 emulation mode.
- Fallback documented in ROADMAP.md: if Rosetta-for-Linux proves too fragile
  for Vivado's specific dependency set (some tools trip on Rosetta), fall
  back to true x86_64 QEMU emulation (slower, but zero compatibility risk).
  Phase 1 should test both and record which one actually works reliably
  with Vivado's installer and synthesis tools before committing.

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
- Vivado **2018.x** + Ubuntu **20.04** (or 18.04) ARM64 guest
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
  not a hypothetical scenario.

**Pairing B — modern toolchain (disk-rich Macs):**
- Current Vivado (2024/2025.x) + Debian 12 (bookworm) ARM64 guest
- The one confirmed working prior example (university course VM) used
  Debian 12 + Rosetta + Vivado 2025.2 successfully.
- Larger footprint (see disk section), but current tooling and IP.

**Decision: support both as a `--vivado-version` / guest-OS matched pair; do
NOT hardcode a single guest OS.** Default recommendation depends on the disk
check in `doctor` — if the Mac is tight on space, steer toward Pairing A.
This is a decision to *confirm by testing* both against real installers in
phase 2, not a locked fact — the exact 2018 footprint and the 2018-on-20.04
dependency set both need verification.

Cloud images (not full installer ISOs) are used for both, so cloud-init can
script first-boot provisioning (users, packages, network, SSH keys) instead
of automating an interactive installer. Both Ubuntu and Debian ship ARM64
cloud images.

## Provisioning

**Decision: cloud-init on first boot, then idempotent bash scripts over SSH
for anything after.**

- `cloud-init/user-data.yaml`: base packages, user account, SSH key install,
  Rosetta virtiofs mount setup.
- `src/provision/*.sh`: scripts pushed and run over SSH for anything that
  needs to happen after Vivado is manually placed in the VM — installing
  Vivado's Linux dependency packages (`libtinfo`, `libncurses`, etc.),
  configuring cable drivers, setting udev rules for USB JTAG devices.

## USB / JTAG passthrough

Known hard part. QEMU on Apple Silicon can pass through USB devices, but
board vendors' cables (Digilent, Xilinx Platform Cable) need correct udev
rules and sometimes exact USB device/vendor ID matching. Plan:
- Phase 1: get bitstream generation working with no hardware in the loop.
- Phase 2 (see ROADMAP.md): tackle passthrough as its own milestone, tested
  against one specific board (Basys 3) before generalizing.

## GUI access

Vivado's GUI needs a display. Options, in order of preference:
1. X11 forwarding over SSH (`ssh -X`), using XQuartz on the Mac host. Known
   to work (existing Docker-based guide uses this). Simple, no extra guest
   services.
2. VNC/RDP server in the guest, viewed via a native macOS client. More setup,
   but better performance for a GUI-heavy app like Vivado. Consider only if
   X11 forwarding proves too slow in testing.

Start with (1) since it's proven and simpler; revisit if performance is bad.

## Disk footprint & storage strategy

The generic "150GB free" figure from online guides is a worst-case (full
device-family install + installer overhead + buffer) and will needlessly
fail on an already-used Mac. Real requirement is much lower once trimmed.

**Decisions:**
- **Thin-provisioned qcow2 disk image.** The VM's virtual disk is sparse —
  a "150GB" virtual disk only consumes real host space as data is actually
  written. `doctor` gates on realistic *actual* footprint, not virtual size.
- **`--storage-path` flag from day one.** The qcow2 image and Vivado install
  can live on an external USB-C SSD instead of the boot volume. `doctor`
  detects low internal space and offers this rather than hard-failing.
- **Device-family-limited install.** Basys 3 only needs Artix-7. Skipping
  UltraScale/UltraScale+/Versal roughly halves the modern-Vivado footprint.
- **Delete the installer archive post-install** to reclaim ~15–25GB inside
  the guest.

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

## Host CLI language

**Decision: Python 3 (stdlib-heavy, minimal dependencies).**

Rationale: bash is fine for the provisioning scripts that run *inside* the
VM, but the host-side orchestration (state tracking, argument parsing,
calling `qemu-system-aarch64` with the right flags, polling VM boot state)
benefits from real error handling and structured state (JSON) that's
painful in bash. Avoid heavy frameworks (no Click/Typer dependency unless
it becomes clearly worth it) to keep install friction low — a `pipx install`
or single-file script should be enough to start.

## `doctor`'s install-confirmation behavior (Phase 0, implemented)

**Decision: `doctor --fix` never installs anything without an explicit y/n
prompt, unless `--yes` is also passed.**

`doctor` is capable of shelling out to `brew install qemu` and
`softwareupdate --install-rosetta --agree-to-license` when prerequisites
are missing and `--fix` is given. Early implementation ran these
immediately once `--fix` was set, with no confirmation. That's too
aggressive for a tool whose main job up to this point has been read-only
inspection — a person running `doctor --fix` to see what's missing
shouldn't be surprised by a live `brew install` firing off.

Implementation: a `_confirm(prompt)` helper prompts `[y/N]:` via `input()`,
defaulting to "no" on any non-`y` answer *and* on `EOFError` (piped/non-
interactive stdin), so a scripted or non-interactive run never silently
installs something. A separate `--yes` flag bypasses the prompt for
deliberate scripted/CI use. This pattern (confirm-by-default, explicit
opt-out for automation) should be the template for any future subcommand
that mutates host or VM state — `init`, `destroy`, and anything in Phase 2
that touches the Vivado install should follow the same shape rather than
defaulting to silent action.

Verified on real hardware: uninstalling QEMU and re-running
`doctor --fix` correctly re-detected it missing, prompted, and on
confirmation ran a live `brew install qemu` that succeeded in the same
invocation (the QEMU check re-verifies after the install attempt rather
than requiring a second `doctor` run). See MANUAL_CHECKLIST.md.

## Code layout (Phase 0, as built)

```
vivado-mac/
├── pyproject.toml          # console_scripts entry point: vivado-mac = vivado_mac.cli:main
├── vivado_mac/
│   ├── __init__.py
│   ├── cli.py               # argparse wiring only — no check logic here
│   └── doctor.py            # all Phase 0 check functions + run_doctor()
└── tests/
    └── test_doctor.py       # mocked unit tests for doctor's branching logic
```

`cli.py` is deliberately routing-only — it has no stubs yet for
`init`/`start`/`stop`/`destroy`/`install-vivado`/`program`. Those get added
when their phase is actually built, per the "don't jump ahead" working
style constraint, not scaffolded in advance.

## Open questions to resolve during phase 1

- Does Rosetta-for-Linux actually handle all of Vivado's tools reliably
  (synthesis, implementation, simulator, hardware manager), or only some?
  Does an old (2018) Vivado behave under Rosetta as well as a modern one?
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
