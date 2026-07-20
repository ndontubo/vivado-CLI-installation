# MANUAL_CHECKLIST.md

Things that need a real Apple Silicon Mac (and eventually a real FPGA
board) to verify — not automatable in CI. Run through this before tagging
a release, and whenever touching VM bring-up or provisioning code.

## Phase 1 checks
- [ ] `vivado-mac doctor` correctly flags a Mac with <150GB free disk
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

## Notes template (fill in per test run)
- Mac model / chip:
- macOS version:
- Vivado version tested:
- Date:
- Result / issues found:
