# ROADMAP.md

Build order. Each phase should be genuinely working and manually verified
before starting the next — this project has too many hardware/OS variables
to build speculatively on top of an unverified layer.

## Phase 0 — Environment & prerequisites checker
**Status: DONE. Verified on real Apple Silicon hardware — see
MANUAL_CHECKLIST.md's Phase 0 section and test run log for the full
verification record.**

Goal: `vivado-mac doctor` command that checks the host is capable, before
any VM work starts.
- [x] Detect Apple Silicon vs Intel, macOS version
- [x] Smart disk-space check (NOT a flat 150GB gate — that fails needlessly
      on a used Mac). Gate on the realistic *peak install* footprint for the
      chosen Vivado-version/OS pairing (see ARCHITECTURE disk table):
      ~20–30GB for Vivado 2018 Artix-7, ~90GB for modern Artix-7. Account
      for thin provisioning — check actual free space vs. realistic need,
      not virtual disk size.
- [x] `--storage-path` flag: if internal free space is short, offer to put
      the VM image on an external SSD instead of hard-failing. (Flag
      wiring confirmed on real hardware; not yet tested against a
      genuinely separate physical volume — see MANUAL_CHECKLIST.md.)
- [x] Check RAM (16GB+ recommended) — soft warning, not a hard blocker
- [x] Check/install QEMU via Homebrew — implemented as an explicit y/n
      confirmation prompt before installing anything (`--fix` to enable,
      `--fix --yes` to skip the prompt for scripted use); see
      ARCHITECTURE.md's "doctor install confirmation" section for why this
      isn't just a silent auto-install
- [x] Verify Virtualization.framework + Rosetta availability (checks for
      the Rosetta binary at `/Library/Apple/usr/share/rosetta/rosetta`;
      installable via the same confirm-then-`--fix` pattern as QEMU)
- [x] Clear, actionable error messages for every failure case (this is a
      big part of the value-add over the manual guides — most of them
      don't tell you *why* something failed). Disk-short message states
      exactly how much is free, how much the chosen pairing needs, that
      installer space is reclaimable, and suggests `--storage-path`.

## Phase 1 — VM bring-up (no Vivado yet)
**Status: NOT STARTED. Next up.**

Goal: `vivado-mac init` creates and boots a bare ARM64 Linux VM with
Rosetta configured, reachable over SSH. Guest OS depends on the chosen
Vivado-version pairing (see ARCHITECTURE): Ubuntu 20.04 for Vivado 2018,
Debian 12 for modern Vivado. Build phase 1 against whichever pairing is
being tested first — thin-provisioned qcow2 with optional `--storage-path`.
- [ ] Download/cache the correct official ARM64 cloud image for the chosen
      pairing (verify checksum) — Ubuntu 20.04 or Debian 12
- [ ] Create the disk as a thin-provisioned (sparse) qcow2 image
- [ ] Generate cloud-init user-data (SSH key, hostname, base packages)
- [ ] Boot via qemu-system-aarch64 with Virtualization.framework acceleration
- [ ] Configure Rosetta virtiofs share, verify an x86_64 "hello world"
      binary actually runs inside the guest via Rosetta
- [ ] `vivado-mac start` / `stop` / `status` / `destroy` lifecycle commands
- [ ] State tracking in `~/.vivado-mac/state.json`
- **Exit criteria:** a fresh Mac can go from `brew install` to an SSH prompt
  inside a working ARM64 Linux VM (Ubuntu 20.04 or Debian 12 per pairing)
  with confirmed Rosetta x86_64 execution, in one command, no manual UTM
  clicking.

## Phase 2 — Vivado install automation
Goal: `vivado-mac install-vivado <path-to-installer>` gets Vivado fully
installed and launchable (GUI opens via X11 forwarding) inside the VM.
- [ ] Confirm the target Vivado version + guest OS pairing (2018+Ubuntu20.04
      for smallest footprint, or modern+Debian12) — see ARCHITECTURE
- [ ] Measure ACTUAL disk footprint for the chosen pairing and replace the
      estimated numbers in ARCHITECTURE's table + RESEARCH_NOTES with
      measured values. Feed the real peak number back into `doctor`'s check.
- [ ] Delete the installer archive inside the VM after a successful install
      to reclaim space (~15–25GB)
- [ ] Transfer user-supplied installer into the VM (scp/virtiofs share)
- [ ] Install the Vivado Linux dependencies for the chosen pairing
      (libtinfo5, libncurses5, libglu1-mesa, libxtst6, libxrender1, libxi6,
      etc.) — pin exact package set once verified. Note: an OLDER Vivado
      (2018) on Ubuntu 20.04 may need a different/larger shim set than
      modern Vivado on Debian 12 — verify per pairing, don't assume.
- [ ] Drive Vivado's installer in unattended/silent mode (Xilinx installers
      support a config-file-driven silent install — use that instead of
      scripting the interactive TUI/GUI)
- [ ] Verify Vivado launches and the GUI is visible via `ssh -X` + XQuartz
- [ ] Document/automate license activation step as far as legally and
      technically possible (likely still requires user to complete AMD's
      own web-based license flow once)
- **Exit criteria:** Vivado ML Standard installed from a user-supplied
  installer, GUI visible on the Mac, project can be created and a design
  synthesized (no hardware yet).

## Phase 3 — USB / JTAG passthrough
Goal: `vivado-mac program --board basys3` flashes a real board.
- [ ] Install/verify Digilent (or relevant vendor) cable drivers inside VM
- [ ] Configure udev rules for the target board's USB VID/PID
- [ ] QEMU USB passthrough configuration (host device -> guest)
- [ ] Test end-to-end: generate bitstream, flash to physical Basys 3,
      confirm it runs on hardware
- **Exit criteria:** a real bitstream reaches a real board without the user
  touching QEMU flags or udev rules by hand.

## Phase 4 — Polish & distribution
- [ ] `brew install vivado-mac` tap, or single-command install script
- [ ] Better error recovery (partial-failure states, retry logic)
- [ ] Support a second board (generalize the passthrough config)
- [ ] Write up the project properly (blog post / README) — useful resume
      and portfolio material given the neuromorphic router project this
      supports
- [ ] Decide whether to build the menu bar app on top of this CLI (separate
      future project, see original discussion)

## Explicitly out of scope for now
- Intel Mac support
- Windows guest path (Linux guest is the only target — matches AMD's
  primary-supported Linux distros)
- Bundling/redistributing any Xilinx binaries or licenses
- GUI/menu bar app (phase 5+, separate project)
