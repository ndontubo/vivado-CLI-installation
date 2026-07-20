"""
vivado-mac init / start / stop / status / destroy -- Phase 1 VM bring-up.

Goal (ROADMAP.md phase 1): a bare ARM64 Linux VM, Rosetta-for-Linux
configured, reachable over SSH, in one command, no manual UTM clicking.

IMPORTANT ARCHITECTURE CORRECTION (found while building this phase, see
ARCHITECTURE.md's "Virtualization approach -- CORRECTED" section for the
full writeup): ARCHITECTURE.md originally called for driving
`qemu-system-aarch64` directly for Rosetta-for-Linux support. That doesn't
actually work -- Rosetta's virtiofs directory share is a
Virtualization.framework-only API (`VZLinuxRosettaDirectoryShare`), not
reachable from a plain QEMU process regardless of `-accel hvf`. This module
instead shells out to `vfkit` (github.com/crc-org/vfkit, Apache-2.0,
`brew install vfkit`) -- a small, actively-maintained CLI hypervisor built
directly on Virtualization.framework (same one podman/minikube/colima use
for their own Rosetta support). This keeps the "no UTM GUI app dependency"
decision intact while actually getting Rosetta. `qemu-img` (from the
existing `qemu` Homebrew dependency) is still used, but only as an offline
image-conversion tool -- not as the VM launcher.

Consequence for disk strategy: vfkit only accepts raw or ISO disk images,
not qcow2 (Apple Virtualization Framework has no qcow2 support). Thin
provisioning is instead achieved the way vfkit's own docs recommend: a
sparse raw file (APFS supports sparse files natively) created as a
copy-on-write clone (`cp -c` / clonefile(2)) of a cached "golden" raw image,
which is itself produced once via `qemu-img convert` from the official
cloud qcow2. The clone is instant and consumes no extra disk space until
written to -- functionally equivalent to qcow2's thin provisioning.

Networking: vfkit's `virtio-net,nat` uses VZNATNetworkDeviceAttachment,
which (unlike QEMU user-mode/SLIRP networking) lets the host reach the
guest directly at its DHCP-assigned IP -- no port-forwarding flag needed.
The guest's IP is looked up after boot by matching its (fixed, generated)
MAC address against host's /var/db/dhcpd_leases, per vfkit's own docs.

NOTE: everything in this file is logic-complete but UNVERIFIED against
real hardware -- see MANUAL_CHECKLIST.md's Phase 1 section. VM boot,
Rosetta translation, and cloud-init behavior all need a real Apple Silicon
Mac to confirm, same as Phase 0.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

STATE_PATH = Path.home() / ".vivado-mac" / "state.json"
DEFAULT_BASE_DIR = Path.home() / ".vivado-mac"

# --- Guest OS / image info, keyed by the same --vivado-version pairing ----
# doctor.py uses (see PAIRING_INFO there for the disk-footprint side of
# this). default_disk_gb is deliberately larger than doctor's peak_gb
# estimate for the pairing, since this is a *virtual* (sparse) size, not
# real consumed space -- see "Thin images" note above.
IMAGE_INFO = {
    "2018": {
        "guest_label": "Ubuntu 20.04 LTS (Focal Fossa) arm64",
        "qcow2_url": "https://cloud-images.ubuntu.com/releases/focal/release/ubuntu-20.04-server-cloudimg-arm64.img",
        "checksum_url": "https://cloud-images.ubuntu.com/releases/focal/release/SHA256SUMS",
        "checksum_algo": "sha256",
        # SHA256SUMS lines look like "<hash> *ubuntu-20.04-server-cloudimg-arm64.img"
        "checksum_filename": "ubuntu-20.04-server-cloudimg-arm64.img",
        "cache_basename": "ubuntu-20.04-arm64",
        "default_disk_gb": 35,
    },
    "modern": {
        "guest_label": "Debian 12 (Bookworm) arm64",
        "qcow2_url": "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-arm64.qcow2",
        "checksum_url": "https://cloud.debian.org/images/cloud/bookworm/latest/SHA512SUMS",
        "checksum_algo": "sha512",
        "checksum_filename": "debian-12-generic-arm64.qcow2",
        "cache_basename": "debian-12-arm64",
        "default_disk_gb": 100,
    },
}

SSH_USER = "vivado"
VM_HOSTNAME = "vivado-mac"
VFKIT_RESTFUL_PORT = 34521  # arbitrary fixed local port; one VM at a time


# --- small helpers ----------------------------------------------------

def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _confirm(prompt: str) -> bool:
    """Same shape as doctor.py's _confirm -- default 'no', never installs/
    destroys anything on a bare Enter or non-interactive stdin."""
    try:
        answer = input(f"{prompt} [y/N]: ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def _base_dir(storage_path: Optional[str]) -> Path:
    """Where big VM artifacts (images, disks, cloud-init) live. state.json
    itself always stays at ~/.vivado-mac/state.json (small, host-local) --
    only the bulky stuff moves to --storage-path, matching doctor's
    --storage-path semantics."""
    return Path(storage_path).expanduser() if storage_path else DEFAULT_BASE_DIR


def load_state() -> Optional[dict]:
    if not STATE_PATH.exists():
        return None
    try:
        return json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _generate_mac() -> str:
    """52:54:00 is the OUI QEMU/libvirt reserve for locally-generated
    virtual NICs; reusing it here is conventional, not load-bearing."""
    tail = secrets.token_bytes(3)
    return "52:54:00:" + ":".join(f"{b:02x}" for b in tail)


def _normalize_mac(mac: str) -> str:
    """Normalize to zero-padded lowercase octets so '52:54:0:1:2:3' and
    '52:54:00:01:02:03' compare equal (dhcpd_leases uses the former)."""
    return ":".join(f"{int(o, 16):02x}" for o in mac.split(":"))


# --- image acquisition (download, verify, convert, cache) -------------

def _download(url: str, dest: Path, description: str) -> None:
    print(f"  -> Downloading {description}...")
    print(f"     {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url) as resp, open(tmp, "wb") as f:
            total = resp.headers.get("Content-Length")
            total = int(total) if total else None
            read = 0
            chunk = 1024 * 1024
            last_pct = -1
            while True:
                block = resp.read(chunk)
                if not block:
                    break
                f.write(block)
                read += len(block)
                if total:
                    pct = int(read * 100 / total)
                    if pct != last_pct and pct % 10 == 0:
                        print(f"     {pct}% ({read / (1024**2):.0f} MB)")
                        last_pct = pct
        tmp.rename(dest)
    except (urllib.error.URLError, OSError) as e:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"download of {description} failed: {e}") from e


def _fetch_checksum(checksum_url: str, filename: str) -> str:
    """Parse a SHA256SUMS/SHA512SUMS-style file for one filename's hash.
    Format is '<hex digest>  <filename>' or '<hex digest> *<filename>'."""
    try:
        with urllib.request.urlopen(checksum_url) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise RuntimeError(f"could not fetch checksum file {checksum_url}: {e}") from e

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts
        name = name.strip().lstrip("*")
        if name == filename:
            return digest.lower()
    raise RuntimeError(f"'{filename}' not found in checksum file {checksum_url}")


def _verify_checksum(path: Path, expected_hex: str, algo: str) -> bool:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest().lower() == expected_hex.lower()


def ensure_base_image(pairing: str, base_dir: Path) -> Path:
    """Download (if needed), checksum-verify, and convert-to-raw the
    official cloud image for this pairing. Cached under base_dir/images/
    and reused across `init` calls -- this is the slow, network-heavy step
    idempotency matters most for.

    Returns the path to the verified, converted RAW "golden" image that
    per-VM disks get cloned from.
    """
    info = IMAGE_INFO[pairing]
    images_dir = base_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    qcow2_path = images_dir / f"{info['cache_basename']}.qcow2"
    raw_path = images_dir / f"{info['cache_basename']}.raw"
    verified_marker = images_dir / f"{info['cache_basename']}.verified-sha"

    # Already downloaded, verified, and converted? Skip everything.
    if raw_path.exists() and verified_marker.exists():
        print(f"  -> Using cached, verified base image: {raw_path}")
        return raw_path

    if not qcow2_path.exists():
        _download(info["qcow2_url"], qcow2_path, info["guest_label"])
    else:
        print(f"  -> Found cached download: {qcow2_path} (re-verifying checksum)")

    print(f"  -> Fetching official checksum ({info['checksum_algo']})...")
    expected = _fetch_checksum(info["checksum_url"], info["checksum_filename"])
    print("  -> Verifying checksum (this reads the whole file)...")
    if not _verify_checksum(qcow2_path, expected, info["checksum_algo"]):
        qcow2_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Checksum mismatch for {info['guest_label']} download -- deleted "
            "the bad file. This means either a corrupted download or a "
            "network tampering issue. Re-run to retry the download."
        )
    verified_marker.write_text(expected)

    if not shutil.which("qemu-img"):
        raise RuntimeError(
            "qemu-img not found (part of the 'qemu' Homebrew formula from "
            "Phase 0's doctor check). Run `vivado-mac doctor --fix` first."
        )

    print(f"  -> Converting qcow2 -> raw (one-time): {raw_path}")
    result = _run(["qemu-img", "convert", "-f", "qcow2", "-O", "raw", str(qcow2_path), str(raw_path)])
    if result.returncode != 0:
        raw_path.unlink(missing_ok=True)
        raise RuntimeError(f"qemu-img convert failed: {result.stderr.strip()}")

    return raw_path


def clone_disk(golden_raw: Path, dest: Path, size_gb: int) -> None:
    """Copy-on-write clone the golden raw image (instant, no extra space
    via APFS clonefile), then grow it (sparse, still no cost until written)
    to the target virtual size."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()

    result = _run(["cp", "-c", str(golden_raw), str(dest)])
    if result.returncode != 0:
        raise RuntimeError(
            f"cp -c (APFS clonefile) failed: {result.stderr.strip()}. "
            "This usually means the destination isn't on an APFS volume "
            "that supports clonefile -- check --storage-path if you used one."
        )

    result = _run(["truncate", "-s", f"{size_gb}G", str(dest)])
    if result.returncode != 0:
        raise RuntimeError(f"truncate failed while growing disk: {result.stderr.strip()}")


# --- SSH keys -----------------------------------------------------------

def ensure_ssh_keypair(base_dir: Path) -> tuple[Path, Path]:
    ssh_dir = base_dir / "ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    key_path = ssh_dir / "id_ed25519"
    pub_path = ssh_dir / "id_ed25519.pub"
    if key_path.exists() and pub_path.exists():
        return key_path, pub_path

    print(f"  -> Generating SSH keypair: {key_path}")
    result = _run([
        "ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key_path),
        "-C", "vivado-mac",
    ])
    if result.returncode != 0:
        raise RuntimeError(f"ssh-keygen failed: {result.stderr.strip()}")
    key_path.chmod(0o600)
    return key_path, pub_path


# --- cloud-init -----------------------------------------------------------

def write_cloud_init(vm_dir: Path, pub_key_path: Path, hostname: str) -> Path:
    ci_dir = vm_dir / "cloud-init"
    ci_dir.mkdir(parents=True, exist_ok=True)
    pub_key = pub_key_path.read_text().strip()

    # The binfmt_misc registration line below is systemd-binfmt's config
    # syntax (":name:type:offset:magic:mask:interpreter:flags"), taken
    # verbatim from vfkit's own usage docs for the rosetta device. The
    # \x.. sequences are literal text here -- systemd-binfmt parses the
    # hex escapes itself, so this must NOT be shell- or YAML-escaped
    # further. mountTag "rosetta" below must match build_vfkit_args'
    # `--device rosetta,mountTag=rosetta`.
    user_data = f"""#cloud-config
hostname: {hostname}
manage_etc_hosts: true
users:
  - name: {SSH_USER}
    groups: [sudo]
    shell: /bin/bash
    sudo: ["ALL=(ALL) NOPASSWD:ALL"]
    ssh_authorized_keys:
      - {pub_key}
ssh_pwauth: false
package_update: true
write_files:
  - path: /etc/binfmt.d/rosetta.conf
    content: |
      :rosetta:M::\\x7fELF\\x02\\x01\\x01\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x02\\x00\\x3e\\x00:\\xff\\xff\\xff\\xff\\xff\\xfe\\xfe\\x00\\xff\\xff\\xff\\xff\\xff\\xff\\xff\\xff\\xfe\\xff\\xff\\xff:/mnt/rosetta/rosetta:F
mounts:
  - [ "rosetta", "/mnt/rosetta", "virtiofs", "ro,nofail", "0", "0" ]
runcmd:
  - [ systemctl, enable, --now, ssh ]
  - [ mkdir, -p, /mnt/rosetta ]
  - [ mount, -t, virtiofs, rosetta, /mnt/rosetta ]
  - [ systemctl, restart, systemd-binfmt ]
"""
    meta_data = f"instance-id: {hostname}\nlocal-hostname: {hostname}\n"

    (ci_dir / "user-data").write_text(user_data)
    (ci_dir / "meta-data").write_text(meta_data)
    return ci_dir


# --- vfkit invocation -----------------------------------------------------

def _vfkit_path() -> str:
    path = shutil.which("vfkit")
    if not path:
        raise RuntimeError(
            "vfkit not found. Install it with: brew install vfkit "
            "(or re-run `vivado-mac doctor --fix`)."
        )
    return path


def build_vfkit_args(state: dict) -> list[str]:
    vm_dir = Path(state["vm_dir"])
    args = [
        "--cpus", str(state["cpus"]),
        "--memory", str(state["memory_mb"]),
        "--bootloader", f"efi,variable-store={vm_dir / 'efi-vars.fd'},create",
        "--device", f"virtio-blk,path={state['disk_path']}",
        "--cloud-init", f"{vm_dir / 'cloud-init' / 'user-data'},{vm_dir / 'cloud-init' / 'meta-data'}",
        "--device", f"virtio-net,nat,mac={state['mac_address']}",
        "--device", "virtio-rng",
        "--device", f"virtio-serial,logFilePath={vm_dir / 'console.log'}",
        # NOT ignoreIfMissing: if Rosetta genuinely can't be installed/found,
        # vfkit should fail loudly, not silently boot without translation --
        # that's the one failure mode this whole project exists to avoid.
        "--device", "rosetta,mountTag=rosetta,install",
        "--restful-uri", f"tcp://127.0.0.1:{state['restful_port']}",
    ]
    return args


def _vfkit_running(state: dict) -> bool:
    """Ground truth for 'is the VM running' -- always probe the REST API
    live rather than trusting a stale pid in state.json."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{state['restful_port']}/vm/state", timeout=2
        ) as resp:
            data = json.loads(resp.read())
            return data.get("state") == "VirtualMachineStateRunning"
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return False


def _vfkit_wait_for_state(state: dict, target: str, timeout: int = 60) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{state['restful_port']}/vm/state"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                data = json.loads(resp.read())
                if data.get("state") == target:
                    return True
        except (urllib.error.URLError, OSError, TimeoutError, ValueError):
            pass
        time.sleep(2)
    return False


def _launch_vfkit(state: dict) -> int:
    vm_dir = Path(state["vm_dir"])
    log_path = vm_dir / "vfkit.log"
    cmd = [_vfkit_path()] + build_vfkit_args(state)
    print("  -> Starting vfkit:")
    print(f"     {' '.join(cmd)}")
    with open(log_path, "ab") as log_f:
        log_f.write(f"\n--- launching {time.ctime()} ---\n".encode())
        proc = subprocess.Popen(
            cmd, stdout=log_f, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    (vm_dir / "vfkit.pid").write_text(str(proc.pid))
    return proc.pid


def _get_guest_ip(hostname: str, mac_address: str, timeout: int = 60) -> Optional[str]:
    """Resolve the guest's DHCP-assigned IP via macOS's own
    /var/db/dhcpd_leases.

    ORIGINALLY matched purely on MAC address (vfkit's own documented
    approach). CORRECTED after real-hardware testing: Ubuntu 20.04's
    systemd-networkd sends a DHCP client-identifier (a type-255 DUID) by
    default instead of the raw hardware address, so `hw_address` in the
    lease often does NOT match the MAC we told vfkit to use. `name=` (the
    DHCP hostname option, which cloud-init sets from our own `hostname:`
    value) turned out to be the reliable match in practice, so that's
    tried first. MAC matching is kept as a fallback for any guest/
    networking-stack combination that *does* send a plain hardware
    address -- costs nothing to check both.

    CORRECTED AGAIN after real-hardware testing: bootpd never prunes old
    leases from this file. Since every `init` reuses the same hostname
    ("vivado-mac"), repeated destroy/init cycles accumulate multiple
    `name=vivado-mac` entries -- one per VM instance that ever existed,
    not just the current one. Returning the *first* match (as this
    function originally did) can silently return a stale IP from an
    already-destroyed VM. Fix: collect every matching entry, along with
    its `lease=` value (a monotonically increasing counter), and return
    the most recently issued one.
    """
    leases_path = Path("/var/db/dhcpd_leases")
    target_mac = _normalize_mac(mac_address)
    deadline = time.time() + timeout

    while time.time() < deadline:
        if leases_path.exists():
            try:
                text = leases_path.read_text(errors="replace")
            except PermissionError:
                return None  # caller should fall back to manual lookup

            name_matches: list[tuple[int, str]] = []
            mac_matches: list[tuple[int, str]] = []
            ip = name = hw = None
            lease_val = 0
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("ip_address="):
                    ip = line.split("=", 1)[1]
                elif line.startswith("name="):
                    name = line.split("=", 1)[1]
                elif line.startswith("hw_address="):
                    raw = line.split("=", 1)[1]
                    # format: "1,52:54:0:12:34:56" (plain MAC) or
                    # "ff,f1:f5:...:29:7a" (DUID client-id, NOT a MAC --
                    # normalizing this will just fail to match, harmlessly)
                    hw = raw.split(",", 1)[-1]
                elif line.startswith("lease="):
                    try:
                        lease_val = int(line.split("=", 1)[1], 16)
                    except ValueError:
                        lease_val = 0
                elif line == "}":
                    if ip and name == hostname:
                        name_matches.append((lease_val, ip))
                    elif ip and hw:
                        try:
                            if _normalize_mac(hw) == target_mac:
                                mac_matches.append((lease_val, ip))
                        except ValueError:
                            pass  # not a plain MAC (e.g. a DUID) -- ignore
                    ip = name = hw = None
                    lease_val = 0

            # Prefer the newest hostname match; fall back to the newest
            # MAC match only if no hostname match exists at all.
            if name_matches:
                return max(name_matches, key=lambda t: t[0])[1]
            if mac_matches:
                return max(mac_matches, key=lambda t: t[0])[1]
        time.sleep(2)
    return None


def _wait_for_ip_and_ssh(hostname: str, mac_address: str, ssh_port: int = 22,
                          timeout: int = 240) -> Optional[str]:
    """Resolve the guest's IP and confirm SSH is reachable there --
    RE-resolving the IP on every attempt, not just once up front.

    ORIGINALLY resolved the IP once (via _get_guest_ip) and then waited
    for SSH at that single fixed address. CORRECTED after real-hardware
    testing: on first boot, a freshly cloned Ubuntu 20.04 image can make
    an early DHCP request under a transient identity (before
    `systemd-machine-id-setup` finalizes the real machine-id for this
    instance), get a lease, and then renegotiate under the *real* identity
    moments later once machine-id settles -- observed directly as the
    resolved IP changing partway through a single boot, not across
    separate boots (that separate problem -- stale leases accumulating
    across destroy/init cycles -- is what _get_guest_ip's max-lease-value
    logic handles). Waiting at a single IP resolved too early can time out
    against an address that's already been superseded by the time SSH
    would otherwise be reachable.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        ip = _get_guest_ip(hostname, mac_address, timeout=5)
        if ip:
            try:
                with socket.create_connection((ip, ssh_port), timeout=3):
                    return ip
            except OSError:
                pass
        time.sleep(3)
    return None


# --- subcommand entry points -----------------------------------------------

def cmd_init(pairing: str, storage_path: Optional[str], cpus: int,
             memory_mb: int, disk_gb: Optional[int], force: bool) -> int:
    existing = load_state()
    if existing and existing.get("initialized") and not force:
        if existing["pairing"] != pairing:
            print(
                f"A VM is already initialized for pairing '{existing['pairing']}', "
                f"but you asked for '{pairing}'. Run `vivado-mac destroy` first, "
                "or re-run `init` with --force to replace it."
            )
            return 1
        print(f"Already initialized ({IMAGE_INFO[pairing]['guest_label']}). "
              "Nothing to do -- run `vivado-mac start` to boot it, or "
              "`init --force` to recreate it from scratch.")
        return 0

    if existing and force:
        print("`--force` given: destroying existing VM state first.")
        cmd_destroy(assume_yes=True)

    info = IMAGE_INFO[pairing]
    base_dir = _base_dir(storage_path)
    vm_dir = base_dir / "vm"
    base_dir.mkdir(parents=True, exist_ok=True)
    vm_dir.mkdir(parents=True, exist_ok=True)

    print(f"vivado-mac init -- {info['guest_label']}\n")

    try:
        golden_raw = ensure_base_image(pairing, base_dir)

        disk_gb = disk_gb or info["default_disk_gb"]
        disk_path = vm_dir / "disk.raw"
        print(f"  -> Cloning disk (sparse, {disk_gb}GB virtual size)...")
        clone_disk(golden_raw, disk_path, disk_gb)

        key_path, pub_path = ensure_ssh_keypair(base_dir)
        write_cloud_init(vm_dir, pub_path, hostname=VM_HOSTNAME)

        state = {
            "pairing": pairing,
            "guest_label": info["guest_label"],
            "vm_dir": str(vm_dir),
            "disk_path": str(disk_path),
            "disk_gb": disk_gb,
            "ssh_key_path": str(key_path),
            "ssh_user": SSH_USER,
            "hostname": VM_HOSTNAME,
            "mac_address": _generate_mac(),
            "restful_port": VFKIT_RESTFUL_PORT,
            "cpus": cpus,
            "memory_mb": memory_mb,
            "storage_path": storage_path,
            "initialized": True,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        save_state(state)

        print("\n  -> Booting VM for the first time (cloud-init needs to run)...")
        _launch_vfkit(state)

        if not _vfkit_wait_for_state(state, "VirtualMachineStateRunning", timeout=60):
            print("FAIL -- vfkit did not report Running within 60s. "
                  f"Check the log: {vm_dir / 'vfkit.log'}")
            return 1

        print("  -> VM running. Waiting for a DHCP lease and SSH to come up "
              "(re-resolves the IP as it goes, since it can legitimately "
              "change partway through first boot -- see vm.py)...")
        ip = _wait_for_ip_and_ssh(state["hostname"], state["mac_address"], timeout=240)
        if not ip:
            print(
                "WARN -- never got a reachable IP+SSH within 240s. The VM "
                "is running (check console.log), but you may need to find its "
                "IP manually via /var/db/dhcpd_leases and SSH in yourself. "
                f"Key: {key_path}, user: {SSH_USER}."
            )
            return 0
        state["last_known_ip"] = ip
        save_state(state)

        print(f"\nPASS -- VM is up and reachable: ssh -i {key_path} {SSH_USER}@{ip}")
        print("Next (Phase 1 checklist): verify an x86_64 hello-world binary "
              "runs under Rosetta inside the guest -- see MANUAL_CHECKLIST.md, "
              "this needs real hardware and hasn't been done yet.")
        return 0

    except RuntimeError as e:
        print(f"FAIL -- {e}")
        return 1


def cmd_start() -> int:
    state = load_state()
    if not state or not state.get("initialized"):
        print("No VM found. Run `vivado-mac init` first.")
        return 1
    if _vfkit_running(state):
        ip = state.get("last_known_ip", "unknown")
        print(f"Already running (last known IP: {ip}). Nothing to do.")
        return 0

    print("Starting VM...")
    _launch_vfkit(state)
    if not _vfkit_wait_for_state(state, "VirtualMachineStateRunning", timeout=60):
        print("FAIL -- vfkit did not report Running within 60s. "
              f"Check the log: {Path(state['vm_dir']) / 'vfkit.log'}")
        return 1

    ip = _get_guest_ip(state.get("hostname", VM_HOSTNAME), state["mac_address"], timeout=60)
    if ip:
        state["last_known_ip"] = ip
        save_state(state)
        print(f"PASS -- running at {ip}.")
    else:
        print("PASS -- running, but couldn't confirm IP yet. Try `status` shortly.")
    return 0


def cmd_stop(assume_yes: bool = True) -> int:
    state = load_state()
    if not state or not state.get("initialized"):
        print("No VM found. Nothing to stop.")
        return 0
    if not _vfkit_running(state):
        print("Already stopped. Nothing to do.")
        return 0

    print("Stopping VM (graceful)...")
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{state['restful_port']}/vm/state",
            data=json.dumps({"state": "Stop"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except (urllib.error.URLError, OSError) as e:
        print(f"  -> Graceful stop request failed ({e}), trying HardStop...")

    deadline = time.time() + 30
    while time.time() < deadline and _vfkit_running(state):
        time.sleep(2)

    if _vfkit_running(state):
        print("  -> Still running after 30s, sending HardStop...")
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{state['restful_port']}/vm/state",
                data=json.dumps({"state": "HardStop"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except (urllib.error.URLError, OSError) as e:
            print(f"FAIL -- HardStop request also failed: {e}")
            return 1
        time.sleep(3)

    print("PASS -- stopped." if not _vfkit_running(state) else
          "WARN -- vfkit may still be shutting down; check `status`.")
    return 0


def cmd_status() -> int:
    state = load_state()
    if not state or not state.get("initialized"):
        print("No VM initialized. Run `vivado-mac init` to create one.")
        return 1

    running = _vfkit_running(state)
    print(f"Pairing: {state['guest_label']}")
    print(f"VM dir:  {state['vm_dir']}")
    print(f"State:   {'RUNNING' if running else 'stopped'}")
    if running:
        ip = _get_guest_ip(state.get("hostname", VM_HOSTNAME), state["mac_address"], timeout=5) or state.get("last_known_ip")
        if ip:
            print(f"IP:      {ip}")
            print(f"SSH:     ssh -i {state['ssh_key_path']} {state['ssh_user']}@{ip}")
        else:
            print("IP:      unknown (no matching DHCP lease found yet)")
    return 0


def cmd_destroy(assume_yes: bool = False) -> int:
    state = load_state()
    if not state or not state.get("initialized"):
        print("Nothing to destroy.")
        return 0

    if not assume_yes:
        if not _confirm(
            f"This will stop and permanently delete the VM at {state['vm_dir']} "
            "(disk image, cloud-init config, state). Continue?"
        ):
            print("Aborted -- nothing was deleted.")
            return 1

    if _vfkit_running(state):
        cmd_stop(assume_yes=True)

    vm_dir = Path(state["vm_dir"])
    if vm_dir.exists():
        shutil.rmtree(vm_dir, ignore_errors=True)
        print(f"  -> Removed {vm_dir}")

    STATE_PATH.unlink(missing_ok=True)
    print("PASS -- destroyed. (Cached base images under images/ were kept -- "
          "delete that directory yourself if you want to reclaim that space too.)")
    return 0
