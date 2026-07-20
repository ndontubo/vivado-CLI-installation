# MANUAL_CHECKLIST.md

Things that need a real Apple Silicon Mac (and eventually a real FPGA
board) to verify — not automatable in CI. Run through this before tagging
a release, and whenever touching VM bring-up or provisioning code.

## Phase 0 checks
- [x] `vivado-mac doctor` correctly detects Apple Silicon vs Intel
- [x] `vivado-mac doctor` correctly parses real macOS version (incl.
      unusual major versions, e.g. 26.x) against the 13.0 minimum
- [x] `vivado-mac doctor` correctly reads real RAM via `sysctl hw.memsize`
      and treats low RAM as a warning, not a hard fail
- [x] `vivado-mac doctor` gates disk space on the chosen pairing's peak
      estimate (not a flat 150GB) and reports real free space accurately
- [x] `vivado-mac doctor` correctly detects missing QEMU and gives a
      working remediation command
- [x] `vivado-mac doctor` correctly detects QEMU once installed via
      `brew install qemu` (version string read back correctly)
- [x] `vivado-mac doctor` correctly detects installed Rosetta 2
- [x] `vivado-mac doctor --vivado-version 2018` full clean run: PASS with
      only the (expected, non-blocking) RAM warning
- [x] `vivado-mac doctor --fix` confirmed working on real hardware: after
      `brew uninstall qemu`, `--fix` correctly detected QEMU missing,
      prompted for confirmation, ran `brew install qemu` live on accept,
      and the QEMU check flipped to PASS in the same run
- [~] `--storage-path` correctly redirects the disk check target (confirmed
      via `--storage-path ~/Downloads` — reported path matched the flag's
      argument exactly, not silently falling back to home). Still NOT
      tested against a genuinely separate volume (e.g. real external SSD)
      since `~/Downloads` lives on the same internal APFS volume as
      everything else — that would only catch a bug where the flag is
      ignored entirely, not one where it's accepted but not truly
      redirected to a different disk. Test again once an external drive is
      available.

**Phase 0 exit criteria (ROADMAP.md) considered MET** for the 2018 pairing
on the test machine below. Only remaining gap: `--storage-path` against a
genuinely separate volume (e.g. real external SSD) — flag wiring itself is
confirmed, just not tested against a truly different disk yet.

## Phase 1 checks

All verified on real Apple Silicon hardware (see test run log below), for
BOTH pairings (Ubuntu 20.04 / Vivado 2018 and Debian 12 / modern Vivado).

- [x] `brew install vfkit` works and `vivado-mac doctor` correctly detects
      it
- [x] `vivado-mac init` succeeds on a clean machine with no prior state
      (downloads + checksum-verifies the cloud image, converts to raw,
      clones the disk, boots via vfkit, waits for SSH)
- [x] `vivado-mac init` run twice in a row doesn't duplicate/corrupt the VM
      (correctly detects existing state and no-ops)
- [x] `vivado-mac init --force` correctly destroys and recreates
- [x] Guest IP is correctly discovered via `/var/db/dhcpd_leases` — NOT by
      MAC as originally designed (see bug log below), by DHCP hostname
      instead, taking the most recent lease among any duplicates
- [x] Rosetta x86_64 test binary runs inside guest after `init` — confirmed
      on BOTH pairings via a static busybox binary (`./busybox uname -m`
      → `x86_64`)
- [x] `vivado-mac start`/`stop`/`status` correctly reflect real VM state via
      vfkit's REST API
- [x] `vivado-mac destroy` fully cleans up (disk image, state file) and is
      safe to run when nothing exists
- [x] Debian 12 arm64 cloud image URL/filename in `IMAGE_INFO["modern"]`
      confirmed working against a real download + SHA512SUMS verification
- [x] `cp -c` (APFS clonefile) confirmed as a true near-zero-extra-space
      clone: 20GB apparent size (`ls -la`) vs. ~1.6GB actual disk usage
      (`du -h`) on a freshly initialized VM
- [x] *(researched, not a hardware test)* Confirmed `vfkit` has no
      host-USB-device passthrough path currently — see ARCHITECTURE.md and
      ROADMAP.md's Phase 3 section for the finding and its implication for
      planning (XVC/`xvcd` is now the assumed primary path, not a fallback)

## Phase 2 checks
- [ ] Vivado installer transferred and silent-installs without hanging
- [ ] Vivado GUI opens over X11 forwarding and is usable (not just launches)
- [ ] A trivial project can be created, synthesized, and implemented
- [ ] License activation flow is understood and documented step by step

## Phase 3 checks
- [ ] Basys 3 board is detected inside the VM after USB passthrough
- [ ] A generated bitstream flashes successfully to the physical board
- [ ] Board runs the flashed design correctly (visible LED/output test)

## Test run log

### Run 1 — 2026-07-03 (initial run)
- Mac model / chip: Apple Silicon (arm64), MacBook Pro
- macOS version: 26.4
- Vivado version tested: n/a (Phase 0 only)
- Result: All six checks behaved correctly. Real blockers found (not code
  bugs): only 16GB free disk, QEMU not installed.

### Run 2 — 2026-07-03 (after cleanup)
- Mac model / chip: same as Run 1
- macOS version: 26.4
- Vivado version tested: n/a (Phase 0 only)
- Steps taken: thinned local Time Machine/update snapshots via
  `sudo tmutil thinlocalsnapshots / 999999999999 4` (16GB -> 35GB free);
  cleared `~/Library/Caches`; stopped an unrelated CVAT Docker stack that
  was consuming CPU/RAM; installed QEMU via `brew install qemu`
- Result: `vivado-mac doctor --vivado-version 2018` -> full PASS (46GB
  free vs. ~30GB peak needed), only the expected RAM warning (8GB
  physical RAM, below the 16GB recommendation, non-blocking).
- Issues found: none — Phase 0 logic confirmed correct end to end for the
  2018 pairing on real hardware.

### Run 3 — 2026-07-20 (Phase 1, VM bring-up, both pairings)
- Mac model / chip: same reference machine (Apple Silicon arm64, 8GB RAM)
- macOS version: 26.4
- Vivado version tested: n/a (Phase 1 only — no Vivado yet)
- Pairings tested: both 2018 (Ubuntu 20.04) and modern (Debian 12)
- `vivado-mac doctor --vivado-version 2018 --fix --yes`: `vfkit` not yet
  installed, `--fix` correctly detected it missing, prompted, and
  installed it live via Homebrew (v0.6.4) in the same run. Full PASS
  afterward (only the expected RAM warning).
- Three real bugs found and fixed during `init` testing against real
  hardware, none of which were caught by unit tests (all three needed an
  actual DHCP negotiation against a real bootpd to surface):
  1. **IP lookup matched the wrong field.** Original design matched the
     guest's MAC address against `hw_address=` in
     `/var/db/dhcpd_leases`, per vfkit's own documented approach. In
     practice, Ubuntu 20.04's systemd-networkd sends a DHCP client
     identifier (a type-255 DUID) instead of the raw MAC, so `hw_address=`
     never matched. Fixed to match on `name=` (the DHCP hostname option,
     set via our own `hostname:` cloud-init field) instead, with MAC
     matching kept as a secondary fallback.
  2. **Stale leases returned the wrong IP.** bootpd never prunes old
     lease entries. Since every VM reuses the same hostname
     ("vivado-mac"), repeated `destroy`/`init` cycles accumulate multiple
     `name=vivado-mac` entries in the lease file — one per VM instance
     that ever existed. The original fix (bug 1) returned the *first*
     matching entry found while scanning the file, which could be a
     long-destroyed VM's stale IP. Fixed to parse each entry's `lease=`
     value (a monotonically increasing counter) and return the most
     recently issued match.
  3. **IP legitimately changes mid-boot.** On a freshly cloned disk's
     first boot, Ubuntu 20.04 was observed requesting an early, transient
     DHCP lease under one DUID, then renegotiating under a *different*
     DUID moments later (most likely `systemd-machine-id-setup`
     finalizing the instance's real machine-id partway through first
     boot, after networking had already made an initial request under a
     throwaway identity). `init` originally resolved the IP once and then
     waited for SSH at that fixed address, which could time out against
     an address already superseded by the time SSH was reachable. Fixed
     to re-resolve the IP on every retry instead of once up front.
- After all three fixes: `vivado-mac init` succeeded cleanly end-to-end
  on both pairings, no manual intervention, ending in a `PASS` line with a
  working SSH command.
- Rosetta confirmed working on both pairings: `/proc/sys/fs/binfmt_misc/rosetta`
  showed `enabled` after boot, and a static x86_64 busybox binary executed
  correctly (`./busybox uname -m` printed `x86_64`) on both the Ubuntu
  20.04 and Debian 12 guests.
- Idempotency confirmed: re-running `init` on an already-initialized VM
  correctly no-op'd; `init --force` correctly destroyed and recreated;
  `destroy` run a second time on nothing correctly no-op'd rather than
  erroring.
- `stop`/`start`/`status` full lifecycle round-trip confirmed working via
  vfkit's REST API.
- Disk thin-provisioning confirmed: a 20GB (`--disk-gb 20`) VM disk showed
  20GB apparent size via `ls -la` but only ~1.6GB actual consumption via
  `du -h`, confirming the `cp -c` (APFS clonefile) + `truncate` approach
  is genuinely sparse in practice, not just in theory.
- Issues found: the three described above, all fixed and re-verified in
  the same session. No outstanding known bugs in Phase 1 code as of this
  run.

## Notes template (fill in per additional test run)
- Mac model / chip:
- macOS version:
- Vivado version tested:
- Date:
- Result / issues found:
