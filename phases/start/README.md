# vivado-mac

A CLI tool that automates the painful parts of running AMD/Xilinx Vivado on
Apple Silicon Macs: VM creation, Rosetta-accelerated x86 emulation, Linux
provisioning, Vivado dependency installation, and USB/JTAG passthrough for
programming real FPGA boards (Basys 3, Arty, Nexys, etc.).

Vivado has no native macOS build and never will (AMD only supports Windows
and RHEL/Ubuntu Linux). Every existing "how to run Vivado on Mac" guide is a
manual, multi-hour, easy-to-get-wrong checklist: install UTM, pick the right
Ubuntu version, remember which libncurses package Vivado needs, fix cable
driver permissions, configure USB passthrough, etc. This project turns that
checklist into one command.

## Why this is worth doing

No existing tool automates the *whole* pipeline end to end for Apple
Silicon specifically. What exists today (as of mid-2026):
- Blog posts / Medium articles with manual UTM click-through steps
- One prebuilt VM image shared informally by a university course (Debian 12
  + Rosetta + Vivado 2025.2), not a general tool, not maintained as software
- A Docker-based repo that handles environment setup but still requires the
  user to manually feed in the Vivado installer and fight XQuartz/USB issues
  themselves

There is real room for a maintained, scriptable, versioned tool here.

## What this tool will NOT do

- **It will not bundle or redistribute Vivado itself.** AMD's EULA does not
  permit redistribution. The user must download their own installer and
  accept AMD's license. The tool automates *everything around* that step
  (fetching the right installer type, running it unattended, configuring
  it) but the installer binary always comes from the user's own AMD account.
- It will not target Intel Macs in phase 1 (see ROADMAP.md) — Apple Silicon
  first, since that's the harder and more common case going forward.

## Quick start (target end-state, not yet built)

```bash
brew install vivado-mac        # or: curl -fsSL .../install.sh | bash
vivado-mac doctor              # checks RAM/disk/Rosetta/UTM prerequisites
vivado-mac init                # creates and provisions the VM
vivado-mac install-vivado ~/Downloads/Vivado_Installer_2025.2.tar.gz
vivado-mac start                # boots the VM, opens Vivado GUI via X11/RDP
vivado-mac program --board basys3   # USB passthrough + bitstream flash
```

## Status

Planning stage. See ARCHITECTURE.md for technical design and ROADMAP.md for
the build order. Working conventions and constraints live in this project's
Custom Instructions.

## Target platform (phase 1)

- Apple Silicon (M1–M4), macOS 14+
- Guest: Debian 12 (bookworm) or Ubuntu 22.04 LTS — TBD in ARCHITECTURE.md
- Vivado ML Standard (free edition) as primary test target
- Test board: Basys 3 (cheap, common, well-documented JTAG behavior)
