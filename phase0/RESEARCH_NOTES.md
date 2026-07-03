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
- Host tooling (QEMU, cloud image cache, XQuartz): ~1–1.2 GB
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

Mitigations that lower the real requirement: thin-provisioned (sparse)
qcow2 so virtual size != consumed size; delete installer archive after
install; limit device families to Artix-7; `--storage-path` to an external
SSD. ALL these numbers are estimates until measured against a real installer
— this has NOT changed based on the Phase 0 field testing above, since that
testing was about the host Mac's free space and `doctor`'s own logic, not
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
machine, not a hypothetical one.

## Evidence that Rosetta-accelerated ARM64 VM approach works

A university course (50.002 CS) published a working setup: Debian 12 +
Rosetta, running under UTM on Apple Silicon, with Vivado 2025.2 and
Alchitry Labs V2 pre-installed. Tested on M2 Max Mac Studio and M2 MacBook
Air. They successfully generated bitstreams and flashed an Alchitry Au
FPGA. This is the strongest existing evidence that the Rosetta-for-Linux
path (not full x86 emulation) is viable for Vivado specifically, not just
theoretically possible.
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

## Open items to verify once implementation starts

- Whether Rosetta-for-Linux handles ALL of Vivado's toolchain (synthesis,
  implementation, simulator, hardware manager/JTAG tools) or only some —
  the course writeup didn't stress-test every Vivado subsystem.
- Exact package list needed for Vivado 2025.2 on Debian 12 specifically
  (the confirmed package list above is from a Medium post using Ubuntu
  20.04 + Vivado, an older/different combination — verify against Debian
  12 + newer Vivado before hardcoding).
- Whether AMD's Vivado installer supports a documented silent/unattended
  install mode with a config file (commonly `install_config.txt` in
  Xilinx installers) — assumed yes based on general Xilinx installer
  behavior, needs direct confirmation against the current installer.
- (New, Phase 1) Whether the same local-snapshot disk-usage gotcha found
  during Phase 0 field testing (see above) could also apply *inside* the
  guest VM once Vivado's install is underway — worth a quick check during
  phase 1/2 VM disk-usage debugging if numbers look off.
