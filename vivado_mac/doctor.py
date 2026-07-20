"""
vivado-mac doctor -- Phase 0 environment/prerequisites checker.

Checks whether this Mac is capable of running the rest of vivado-mac,
before any VM work starts. See ROADMAP.md phase 0 for the checklist this
implements, and ARCHITECTURE.md's "Disk footprint & storage strategy"
section for where the footprint numbers below come from.

IMPORTANT: the footprint numbers below are ESTIMATES from RESEARCH_NOTES.md,
not measured values. They get replaced with real measurements in phase 2
once we have an actual Vivado installer to test against (see the "Measure
ACTUAL disk footprint" item in ROADMAP.md phase 2).
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# --- Disk footprint estimates (ESTIMATES, see RESEARCH_NOTES.md) ----------
# Keyed by the --vivado-version pairing. `peak_gb` is the number doctor
# gates on, since running out of space mid-install (installer archive +
# extraction temp + partial install coexisting) is the real failure mode --
# see ARCHITECTURE.md "Disk footprint & storage strategy".
PAIRING_INFO = {
    "2018": {
        "label": "Vivado 2018.x + Ubuntu 20.04 (Pairing A -- smallest footprint)",
        "peak_gb": 30,
        "steady_state_gb": 25,
    },
    "modern": {
        "label": "Modern Vivado (2024/2025.x) + Debian 12 (Pairing B -- current tooling)",
        "peak_gb": 90,
        "steady_state_gb": 70,
    },
}

MIN_MACOS_VERSION = (13, 0)  # Virtualization.framework Rosetta-for-Linux support
RECOMMENDED_RAM_GB = 16

# will be used to update whether the mac os system running will be able to download the toolkit
@dataclass
class CheckResult:
    name: str
    status: str  # "pass", "warn", "fail"
    message: str


def _run(cmd: list[str]) -> tuple[int, str]:
    """Run a command, return (returncode, stdout) without raising."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return proc.returncode, proc.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 1, ""


def _run_streaming(cmd: list[str]) -> int:
    """Run a command with output streamed live (for long installs like brew/softwareupdate)."""
    try:
        proc = subprocess.run(cmd)
        return proc.returncode
    except FileNotFoundError:
        return 1

def _confirm(prompt: str) -> bool:
    """Ask the user to confirm before doctor installs something on their machine.
 
    Defaults to "no" on empty input or a non-interactive stdin (e.g. piped
    input, a script, or a CI runner without a TTY) -- doctor should never
    install something the user didn't explicitly agree to.
    """
    try:
        answer = input(f"{prompt} [y/N]: ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")

def check_apple_silicon() -> CheckResult:
    #reads platform.machine(). Hard-fails on Intel, per constraint #2 (Apple Silicon only for phase 1).
    machine = platform.machine()
    if machine == "arm64":
        return CheckResult("Apple Silicon", "pass", f"Detected {machine} (Apple Silicon).")
    if machine in ("x86_64", "i386"):
        return CheckResult(
            "Apple Silicon", "fail",
            f"Detected {machine} (Intel Mac). vivado-mac targets Apple Silicon "
            "only in phase 1 -- Intel support is explicitly out of scope for now "
            "(see ROADMAP.md).",
        )
    return CheckResult("Apple Silicon", "fail", f"Unrecognized architecture '{machine}'.")


def check_macos_version() -> CheckResult:
    # requires ≥13.0, since that's the actual minimum for Virtualization.framework's Rosetta-for-Linux support 
    mac_ver = platform.mac_ver()[0]
    if not mac_ver:
        return CheckResult("macOS version", "fail", "Could not determine macOS version (are you on macOS?).")
    try:
        parts = tuple(int(p) for p in mac_ver.split(".")[:2])
    except ValueError:
        return CheckResult("macOS version", "fail", f"Could not parse macOS version '{mac_ver}'.")

    if parts >= MIN_MACOS_VERSION:
        return CheckResult(
            "macOS version", "pass",
            f"macOS {mac_ver} (>= {MIN_MACOS_VERSION[0]}.{MIN_MACOS_VERSION[1]} "
            "required for Virtualization.framework Rosetta-for-Linux support).",
        )
    return CheckResult(
        "macOS version", "fail",
        f"macOS {mac_ver} is below the minimum {MIN_MACOS_VERSION[0]}."
        f"{MIN_MACOS_VERSION[1]} needed for Rosetta-for-Linux VM support. "
        "Update macOS (Ventura/13 or later; README targets 14+) and re-run.",
    )


def check_ram() -> CheckResult:
    # warns but does not fail if available RAM gb is less that 16GB
    rc, out = _run(["sysctl", "-n", "hw.memsize"])
    if rc != 0 or not out.isdigit():
        return CheckResult("RAM", "warn", "Could not determine total RAM (sysctl hw.memsize failed).")
    ram_gb = int(out) / (1024 ** 3)
    if ram_gb >= RECOMMENDED_RAM_GB:
        return CheckResult("RAM", "pass", f"{ram_gb:.0f} GB detected (>= {RECOMMENDED_RAM_GB} GB recommended).")
    return CheckResult(
        "RAM", "warn",
        f"{ram_gb:.0f} GB detected, below the {RECOMMENDED_RAM_GB} GB recommended for "
        "running a Linux VM + Vivado comfortably. Not a hard blocker -- synthesis/"
        "implementation will just be slower and more prone to swapping.",
    )


def check_disk_space(pairing: str, storage_path: Optional[str]) -> CheckResult:
    # Instead of a flat 150GB gate, it looks up the peak footprint estimate for whichever --vivado-version pairing you're targeting (2018+Ubuntu20.04 ≈ 30GB peak, modern+Debian12 ≈ 90GB peak which are both flagged in the code as estimates
    # compares that against real free space via shutil.disk_usage(), checked against --storage-path if you passed one instead of the boot volume
    info = PAIRING_INFO[pairing]
    peak_gb = info["peak_gb"]
    target = Path(storage_path).expanduser() if storage_path else Path.home()
    target_for_check = target if target.exists() else target.parent

    try:
        usage = shutil.disk_usage(target_for_check)
    except OSError as e:
        return CheckResult("Disk space", "fail", f"Could not check free space at {target_for_check}: {e}")

    free_gb = usage.free / (1024 ** 3)
    location_desc = f"'{storage_path}'" if storage_path else "the boot volume (internal disk)"

    if free_gb >= peak_gb:
        return CheckResult(
            "Disk space", "pass",
            f"{free_gb:.0f} GB free on {location_desc}. Needed at peak for "
            f"{info['label']} (ESTIMATE): ~{peak_gb} GB. Steady-state after "
            f"install + cleanup: ~{info['steady_state_gb']} GB.",
        )

    suggestion = (
        "" if storage_path else
        " Re-run with --storage-path pointing at an external SSD to put the VM "
        "image and Vivado install there instead of the boot volume."
    )
    return CheckResult(
        "Disk space", "fail",
        f"Only {free_gb:.0f} GB free on {location_desc}, but {info['label']} needs "
        f"~{peak_gb} GB free at peak during install (ESTIMATE, not yet measured "
        f"against a real installer. This is the *peak* "
        f"figure: the installer archive + its extraction temp coexist briefly "
        f"with the partial install, then get reclaimed down to a steady-state of "
        f"~{info['steady_state_gb']} GB.{suggestion}",
    )


def check_qemu(auto_fix: bool) -> CheckResult:
    # checks for qemu-img on $PATH. NOTE (Phase 1 correction -- see vm.py's
    # module docstring and ARCHITECTURE.md): qemu is no longer the VM
    # *launcher* -- vfkit is (see check_vfkit below), because Rosetta-for-
    # Linux's virtiofs share is a Virtualization.framework-only API that
    # plain qemu-system-aarch64 can't reach. qemu is kept as a Phase 0/1
    # prerequisite anyway because qemu-img is used offline to convert the
    # official Ubuntu/Debian qcow2 cloud images to raw (vfkit can't read
    # qcow2 directly -- Apple Virtualization Framework has no qcow2 support).
    if shutil.which("qemu-img"):
        rc, out = _run(["qemu-img", "--version"])
        version = out.splitlines()[0] if out else "unknown version"
        return CheckResult("QEMU (qemu-img)", "pass", f"Found qemu-img ({version}).")

    if not shutil.which("brew"):
        return CheckResult(
            "QEMU (qemu-img)", "fail",
            "qemu-img not found, and Homebrew is not installed either. "
            "Install Homebrew (https://brew.sh) then run: brew install qemu",
        )

    if auto_fix:
        print("  -> qemu-img not found. Running: brew install qemu")
        rc = _run_streaming(["brew", "install", "qemu"])
        if rc == 0 and shutil.which("qemu-img"):
            return CheckResult("QEMU (qemu-img)", "pass", "Installed qemu via Homebrew.")
        return CheckResult("QEMU (qemu-img)", "fail", "brew install qemu did not succeed. Try running it manually.")

    return CheckResult(
        "QEMU (qemu-img)", "fail",
        "qemu-img not found. Install it with: brew install qemu "
        "(or re-run doctor with --fix to install it automatically). Needed "
        "to convert cloud images from qcow2 to raw for `vivado-mac init`.",
    )


def check_vfkit(auto_fix: bool) -> CheckResult:
    # vfkit (github.com/crc-org/vfkit, Apache-2.0) is the actual VM
    # launcher used by `init`/`start`/`stop`/`status`/`destroy` -- a small
    # CLI hypervisor built directly on Virtualization.framework, which is
    # what makes real Rosetta-for-Linux support possible (see vm.py).
    if shutil.which("vfkit"):
        rc, out = _run(["vfkit", "--version"])
        version = out.splitlines()[0] if out else "unknown version"
        return CheckResult("vfkit", "pass", f"Found vfkit ({version}).")

    if not shutil.which("brew"):
        return CheckResult(
            "vfkit", "fail",
            "vfkit not found, and Homebrew is not installed either. "
            "Install Homebrew (https://brew.sh) then run: brew install vfkit",
        )

    if auto_fix:
        print("  -> vfkit not found. Running: brew install vfkit")
        rc = _run_streaming(["brew", "install", "vfkit"])
        if rc == 0 and shutil.which("vfkit"):
            return CheckResult("vfkit", "pass", "Installed vfkit via Homebrew.")
        return CheckResult("vfkit", "fail", "brew install vfkit did not succeed. Try running it manually.")

    return CheckResult(
        "vfkit", "fail",
        "vfkit not found. Install it with: brew install vfkit "
        "(or re-run doctor with --fix to install it automatically). This is "
        "the VM launcher `vivado-mac init`/`start` will shell out to.",
    )


def check_rosetta(auto_fix: bool) -> CheckResult:
    # checks for the Rosetta binary at /Library/Apple/usr/share/rosetta/rosetta. Same suggest-or---fix pattern.
    rosetta_bin = Path("/Library/Apple/usr/share/rosetta/rosetta")
    if rosetta_bin.exists():
        return CheckResult("Rosetta", "pass", "Rosetta 2 is installed.")

    if auto_fix:
        print("  -> Rosetta not found. Running: softwareupdate --install-rosetta --agree-to-license")
        rc = _run_streaming(["softwareupdate", "--install-rosetta", "--agree-to-license"])
        if rc == 0 and rosetta_bin.exists():
            return CheckResult("Rosetta", "pass", "Installed Rosetta 2.")
        return CheckResult("Rosetta", "fail", "Rosetta install did not succeed. Try running the command manually.")

    return CheckResult(
        "Rosetta", "fail",
        "Rosetta 2 not found at /Library/Apple/usr/share/rosetta/rosetta. "
        "Install it with: softwareupdate --install-rosetta --agree-to-license "
        "(or re-run doctor with --fix).",
    )


def run_doctor(pairing: str, storage_path: Optional[str], auto_fix: bool, assume_yes: bool = False) -> int:
    checks = [
        check_apple_silicon(),
        check_macos_version(),
        check_ram(),
        check_disk_space(pairing, storage_path),
        check_qemu(auto_fix),
        check_vfkit(auto_fix),
        check_rosetta(auto_fix),
    ]

    icons = {"pass": "\033[32m[PASS]\033[0m", "warn": "\033[33m[!!]\033[0m", "fail": "\033[31m[FAIL]\033[0m"}

    print(f"vivado-mac doctor -- checking pairing: {PAIRING_INFO[pairing]['label']}\n")
    for c in checks:
        print(f"{icons[c.status]} {c.name}: {c.message}")

    failed = [c for c in checks if c.status == "fail"]
    warned = [c for c in checks if c.status == "warn"]

    print()
    if failed:
        print(f"FAIL -- {len(failed)} check(s) must be fixed before `vivado-mac init` will work.")
        return 1
    if warned:
        print(f"PASS with {len(warned)} warning(s) -- you can proceed, but see notes above.")
        return 0
    print("PASS -- this Mac looks ready for `vivado-mac init`.")
    return 0
