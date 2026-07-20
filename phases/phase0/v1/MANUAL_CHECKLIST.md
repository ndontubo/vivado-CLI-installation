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
- [ ] `vivado-mac init` succeeds on a clean machine with no prior state
- [ ] `vivado-mac init` run twice in a row doesn't duplicate/corrupt the VM
- [ ] Rosetta x86_64 test binary runs inside guest after `init`
- [ ] `vivado-mac destroy` fully cleans up (disk image, state file)

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

## Notes template (fill in per additional test run)
- Mac model / chip:
- macOS version:
- Vivado version tested:
- Date:
- Result / issues found:
