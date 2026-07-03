"""
vivado-mac -- CLI entry point

6.3.26 9(phase0) 
	- only implements`doctor`
	- pyproject.toml is the packaging/install manifest for the vivado-mac Python package
	- has standard package metadata like name, version, what Python versions it supports, and its dependency list (empty, per the stdlib-heavy tech stack decision  without use of Click/Typer)
	- pip/setuptools to generate a vivado-mac executable on your $PATH that calls main() in vivado_mac/cli.py. Without this, you'd have to run python3 -m vivado_mac.cli doctor every time instead of just vivado-mac doctor.
	- Tells pip install which build tool to use to actually package the thing. setuptools is the boring, standard choice here.
"""

from __future__ import annotations

import argparse
import sys

from vivado_mac.doctor import PAIRING_INFO, run_doctor

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
		help="attempt to automatically install missing prerequisites - QEMU using Rossetta 2 and Homebrew instead of just reporting them."
			"Will prompt for confirmation before each install unless --yes is also given",
			)

	doctor_p.add_argument(
		"-y", "--yes",
		action="store_true",
		dest="assume_yes",
		help="skip the y/n confirmation prompt when --fix would install something."
			"Has no effect without --fix."
			"This is useful for scripted/non-interactive runs -- use deliberately.")

	return parser

def main(argv=None) -> int:
	parser = build_parser()
	args = parser.parse_args(argv)

	if args.command == "doctor":
		return run_doctor(args.vivado_version, args.storage_path, args.auto_fix, args.assume_yes)

	parser.print_help()
	return 1

if __name__ == "__main__":
	sys.exit(main())