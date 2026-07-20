"""
vivado-mac -- CLI entry point

6.3.26 9(phase0)
	- only implements`doctor`
	- pyproject.toml is the packaging/install manifest for the vivado-mac Python package
	- has standard package metadata like name, version, what Python versions it supports, and its dependency list (empty, per the stdlib-heavy tech stack decision  without use of Click/Typer)
	- pip/setuptools to generate a vivado-mac executable on your $PATH that calls main() in vivado_mac/cli.py. Without this, you'd have to run python3 -m vivado_mac.cli doctor every time instead of just vivado-mac doctor.
	- Tells pip install which build tool to use to actually package the thing. setuptools is the boring, standard choice here.

7.14.26 (phase1)
	- adds init/start/stop/status/destroy -- VM bring-up, see vm.py.
	  Still routing only: all lifecycle logic lives in vm.py, same split as
	  doctor.py holds all phase 0 logic.
"""

from __future__ import annotations

import argparse
import sys

from vivado_mac.doctor import PAIRING_INFO, run_doctor
from vivado_mac.vm import cmd_destroy, cmd_init, cmd_start, cmd_status, cmd_stop


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vivado-mac")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_p = subparsers.add_parser("doctor", help="check this mac is ready for vivado_mac")

    doctor_p.add_argument(
        "--vivado-version",
        choices=sorted(PAIRING_INFO.keys()),
        default="modern",
        help="which vivado/guest-OS pairing to check disk space against "
            "(2018=smallest footprint, mordern=current tooling). Default: modern.",
    )

    doctor_p.add_argument(
        "--storage-path",
        default=None,
        help="check free space at this path instead of boot volume"
            "(e.g. an external SSD mount point).",
    )

    doctor_p.add_argument(
        "--fix",
        action="store_true",
        dest="auto_fix",
        help="attempt to automatically install missing prerequisites - QEMU/vfkit "
            "using Homebrew, Rosetta 2 using softwareupdate - instead of just "
            "reporting them. Will prompt for confirmation before each install "
            "unless --yes is also given",
    )

    doctor_p.add_argument(
        "-y", "--yes",
        action="store_true",
        dest="assume_yes",
        help="skip the y/n confirmation prompt when --fix would install something."
            "Has no effect without --fix."
            "This is useful for scripted/non-interactive runs -- use deliberately.")

    # --- init: first-time VM creation + boot -------------------------------
    init_p = subparsers.add_parser(
        "init",
        help="create and boot the VM for the first time (idempotent -- safe to re-run)",
    )
    init_p.add_argument(
        "--vivado-version",
        choices=sorted(PAIRING_INFO.keys()),
        default="modern",
        help="which vivado/guest-OS pairing to provision "
            "(2018=Ubuntu 20.04 smallest footprint, modern=Debian 12). Default: modern.",
    )
    init_p.add_argument(
        "--storage-path",
        default=None,
        help="put the VM's disk image, cloud image cache, and cloud-init config "
            "under this path instead of ~/.vivado-mac (e.g. an external SSD).",
    )
    init_p.add_argument("--cpus", type=int, default=4, help="virtual CPUs for the VM. Default: 4.")
    init_p.add_argument(
        "--memory-mb", type=int, default=8192,
        help="RAM in MiB for the VM. Default: 8192 (8GB).",
    )
    init_p.add_argument(
        "--disk-gb", type=int, default=None,
        help="virtual (sparse) disk size in GB. Defaults to a pairing-appropriate "
            "size with headroom over doctor's peak-footprint estimate.",
    )
    init_p.add_argument(
        "--force", action="store_true",
        help="destroy and recreate the VM if one already exists, instead of "
            "leaving an existing VM alone (the default, idempotent behavior).",
    )

    subparsers.add_parser("start", help="boot an already-initialized VM (no-op if already running)")
    subparsers.add_parser("stop", help="gracefully shut down the VM (no-op if already stopped)")
    subparsers.add_parser("status", help="show whether the VM is running and its IP/SSH command")

    destroy_p = subparsers.add_parser(
        "destroy",
        help="stop and permanently delete the VM (disk, cloud-init config, state)",
    )
    destroy_p.add_argument(
        "-y", "--yes",
        action="store_true",
        dest="assume_yes",
        help="skip the y/n confirmation prompt -- useful for scripted use.",
    )

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return run_doctor(args.vivado_version, args.storage_path, args.auto_fix, args.assume_yes)

    if args.command == "init":
        return cmd_init(
            pairing=args.vivado_version,
            storage_path=args.storage_path,
            cpus=args.cpus,
            memory_mb=args.memory_mb,
            disk_gb=args.disk_gb,
            force=args.force,
        )

    if args.command == "start":
        return cmd_start()

    if args.command == "stop":
        return cmd_stop(assume_yes=True)

    if args.command == "status":
        return cmd_status()

    if args.command == "destroy":
        return cmd_destroy(assume_yes=args.assume_yes)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
