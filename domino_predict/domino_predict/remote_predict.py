"""
Runs DoMINO inference for the "Request Prediction" button, in one of two modes:

- remote: ships the STL to an inference server over SSH/SCP, runs
  scripts/run_prediction.sh there, and copies the results back.
- local:  does the same inside this machine's WSL2 distro (via wsl.exe), so the
  local NVIDIA GPU runs the model and no server is needed.

Both modes expect the same layout on the Linux side (~/domino-ahmedml with
scripts/run_prediction.sh, model/, configs/). Results come back as a surface
prediction (.vtp) and/or a volume grid prediction (.vti). No omni/Kit imports
here.
"""

from __future__ import annotations

import asyncio
import collections
import json
import os
import shlex
import tempfile
import uuid

MODE_LOCAL = "local"
MODE_REMOTE = "remote"

# Shipped inside the extension folder: <extension>/wsl_setup/setup_local_inference.sh
SETUP_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "wsl_setup", "setup_local_inference.sh"
)
LOCAL_VENV_PYTHON = "~/venvs/domino_infer/bin/python"
WSL_INSTALL_HINT = (
    "WSL2 is not available. In an admin PowerShell run 'wsl --install -d Ubuntu-24.04', "
    "reboot, open Ubuntu once to create a user, then click Connect again."
)

SSH_USER = "YOUR_USER"  # SSH account on the inference server
SSH_KEY_PATH = os.path.expanduser("~/.ssh/id_ed25519")
REMOTE_REQUESTS_DIR = "~/domino-ahmedml/requests"
REMOTE_RUN_SCRIPT = "~/domino-ahmedml/scripts/run_prediction.sh"
REMOTE_MODEL_DIR = "~/domino-ahmedml/model"

# AUDIT FIX (2026-09-29): the remote host used to be a hard-coded module
# constant. It's now user-settable from the extension's Settings panel and
# persisted here, in a plain home-directory dotfile -- this extension has no
# existing settings/config infrastructure (no carb.settings registration, no
# extension-local data dir convention) to hook into, so a small standalone
# JSON file is the least-invasive way to remember it across sessions.
SETTINGS_PATH = os.path.expanduser("~/.domino_predict_settings.json")
DEFAULT_SSH_HOST = "192.168.0.100"  # example; set the real IP from the UI
DEFAULT_MODE = MODE_REMOTE

SSH_HOST = DEFAULT_SSH_HOST
_SSH_BASE: list[str] = []
_SCP_BASE: list[str] = []


def _rebuild_command_bases() -> None:
    global _SSH_BASE, _SCP_BASE
    _SSH_BASE = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "-i", SSH_KEY_PATH, f"{SSH_USER}@{SSH_HOST}"]
    _SCP_BASE = ["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "-i", SSH_KEY_PATH]


def load_settings() -> dict:
    """Reads the persisted mode and SSH host. Falls back to the defaults on
    any error (missing file, corrupt JSON, missing key) instead of raising,
    since this runs at extension startup and shouldn't be able to block it."""
    global SSH_HOST
    mode = DEFAULT_MODE
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("ssh_host"):
            SSH_HOST = data["ssh_host"]
        if data.get("mode") in (MODE_LOCAL, MODE_REMOTE):
            mode = data["mode"]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    _rebuild_command_bases()
    return {"mode": mode, "ssh_host": SSH_HOST}


def save_settings(mode: str, ssh_host: str) -> None:
    global SSH_HOST
    if ssh_host:
        SSH_HOST = ssh_host
    _rebuild_command_bases()
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump({"mode": mode, "ssh_host": SSH_HOST}, f)


_rebuild_command_bases()


class PredictionError(RuntimeError):
    pass


async def _run(cmd: list[str]) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode(errors="replace")
    if proc.returncode != 0:
        raise PredictionError(f"Command failed ({' '.join(cmd)}):\n{output}")
    return output


def _wsl_cmd(command: str) -> list[str]:
    # -e runs bash directly (no extra Windows-side shell parsing); -l loads
    # the login profile so ~ and PATH match an interactive WSL session.
    return ["wsl.exe", "-e", "bash", "-lc", command]


def _to_wsl_path(windows_path: str) -> str:
    """C:\\foo\\bar.stl -> /mnt/c/foo/bar.stl"""
    drive, rest = os.path.splitdrive(os.path.abspath(windows_path))
    return f"/mnt/{drive[0].lower()}{rest.replace(os.sep, '/')}"


class _RemoteBackend:
    """Inference server reached over SSH/SCP."""

    async def run(self, command: str) -> str:
        return await _run(_SSH_BASE + [command])

    async def copy_in(self, local_path: str, dest_dir: str, filename: str) -> None:
        await _run(_SCP_BASE + [local_path, f"{SSH_USER}@{SSH_HOST}:{dest_dir}/{filename}"])

    async def copy_out(self, src: str, local_path: str) -> None:
        await _run(_SCP_BASE + [f"{SSH_USER}@{SSH_HOST}:{src}", local_path])


class _LocalBackend:
    """This machine's WSL2 distro. Windows files are reached via /mnt/<drive>."""

    async def run(self, command: str) -> str:
        return await _run(_wsl_cmd(command))

    async def copy_in(self, local_path: str, dest_dir: str, filename: str) -> None:
        # dest_dir starts with ~ and must stay unquoted so bash expands it.
        await self.run(f"cp {shlex.quote(_to_wsl_path(local_path))} {dest_dir}/{shlex.quote(filename)}")

    async def copy_out(self, src: str, local_path: str) -> None:
        await self.run(f"cp {src} {shlex.quote(_to_wsl_path(local_path))}")


def _backend(mode: str):
    return _LocalBackend() if mode == MODE_LOCAL else _RemoteBackend()


async def _local_check() -> tuple[bool, str]:
    """Checks the local WSL2 setup one layer at a time, so the message says
    which layer is missing (WSL itself / GPU driver / our environment)."""
    try:
        await _run(_wsl_cmd("echo ok"))
    except (PredictionError, OSError):
        return False, WSL_INSTALL_HINT
    try:
        await _run(_wsl_cmd("nvidia-smi -L"))
    except PredictionError:
        return False, "WSL2 cannot see the NVIDIA GPU. Install or update the Windows NVIDIA driver."
    try:
        await _run(_wsl_cmd(
            f"test -x {LOCAL_VENV_PYTHON} && test -x {REMOTE_RUN_SCRIPT} && ls {REMOTE_MODEL_DIR}/*.mdlus"
        ))
    except PredictionError:
        return False, "Local inference environment is not installed. Click 'Install Local Environment'."
    return True, "Connected to local WSL2."


async def test_connection(mode: str, host: str = "") -> tuple[bool, str]:
    """Remote: a bare `ssh ... echo ok` with a short timeout, so a wrong or
    unreachable IP fails fast. Local: checks that a request would actually
    work (WSL2, GPU, installed environment), not just that wsl.exe exists."""
    if mode == MODE_LOCAL:
        return await _local_check()
    cmd = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
        "-i", SSH_KEY_PATH, f"{SSH_USER}@{host}", "echo ok",
    ]
    try:
        output = await _run(cmd)
    except (PredictionError, OSError):
        return False, f"Cannot connect to {host} over SSH. Check the IP, the network, and the SSH key."
    if "ok" not in output:
        return False, f"Unexpected response from {host}: {output.strip()!r}"
    return True, f"Connected to {host}."


async def install_local_environment(progress_cb=None) -> tuple[bool, str]:
    """Runs wsl_setup/setup_local_inference.sh inside WSL2 (no sudo needed)
    and forwards its "=== ... ===" step lines to progress_cb. Takes roughly
    10-20 minutes on a fresh machine (PyTorch + cuML downloads, ~7GB)."""

    def report(msg: str):
        if progress_cb is not None:
            progress_cb(msg)

    try:
        await _run(_wsl_cmd("echo ok"))
    except (PredictionError, OSError):
        return False, WSL_INSTALL_HINT
    if not os.path.isfile(SETUP_SCRIPT):
        return False, f"Setup script not found: {SETUP_SCRIPT}"

    # Strip CRLF first: a Windows checkout/zip of the extension may carry
    # Windows line endings, which bash can't run.
    script = shlex.quote(_to_wsl_path(SETUP_SCRIPT))
    proc = await asyncio.create_subprocess_exec(
        *_wsl_cmd(f"tr -d '\\r' < {script} > /tmp/domino_setup.sh && bash /tmp/domino_setup.sh"),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    tail = collections.deque(maxlen=15)
    async for raw in proc.stdout:
        line = raw.decode(errors="replace").strip()
        if not line:
            continue
        tail.append(line)
        if line.startswith("==="):
            report(f"Installing: {line.strip('= ').strip()}")
    if await proc.wait() != 0:
        return False, "Install failed:\n" + "\n".join(tail)
    return True, "Local environment installed."


async def request_prediction(
    stl_path: str,
    mode: str = MODE_REMOTE,
    compute_faces: bool = True,
    compute_streamlines: bool = True,
    progress_cb=None,
) -> tuple[str | None, str | None]:
    """
    Sends `stl_path` to the inference target (remote server or local WSL2)
    and runs the trained DoMINO model on it, computing only what's actually
    requested:
      - compute_faces: predicted surface fields (pressure, wall shear
        stress), full-resolution on the STL's own triangles -- for Kit-CAE's
        Faces operator.
      - compute_streamlines: predicted volume fields (velocity, pressure,
        nut) as a structured grid (.vti) -- Kit-CAE's Streamlines operator
        needs real cell connectivity to advect through, which only the grid
        output has (the scattered point-cloud volume output,
        prediction_volume_0.vtp, fails there with "build_element_locator"
        not implemented for point clouds).
    Skipping whichever one isn't wanted saves real time on the inference
    side (measured on the A6000: surface-only ~80s, volume-only ~16s, both
    ~81s -- the surface branch's STL subdivision dominates either way, so
    asking for volume alone is the case that actually pays off).

    Returns (local_surface_vtp_path_or_None, local_volume_vti_path_or_None)
    -- whichever wasn't requested comes back None, same as before for a
    volume-less (surface-only) model.
    """
    if not compute_faces and not compute_streamlines:
        raise PredictionError("Nothing requested -- select Faces and/or Streamlines first.")

    def report(msg: str):
        if progress_cb is not None:
            progress_cb(msg)

    backend = _backend(mode)
    where = "local WSL2" if mode == MODE_LOCAL else SSH_HOST

    request_id = uuid.uuid4().hex[:12]
    stl_name = os.path.basename(stl_path)
    remote_base = f"{REMOTE_REQUESTS_DIR}/{request_id}"

    report("Preparing request directory...")
    await backend.run(f"mkdir -p {remote_base}/input/case")

    report(f"Sending {stl_name}...")
    await backend.copy_in(stl_path, f"{remote_base}/input/case", stl_name)

    report(f"Running DoMINO inference on {where} (this may take a while)...")
    surface_flag = "true" if compute_faces else "false"
    volume_flag = "true" if compute_streamlines else "false"
    await backend.run(f"{REMOTE_RUN_SCRIPT} {request_id} {surface_flag} {volume_flag}")

    local_dir = tempfile.mkdtemp(prefix=f"domino_predict_{request_id}_")
    local_surface_vtp_path: str | None = None
    local_volume_vti_path: str | None = None

    if compute_faces:
        report("Fetching surface prediction...")
        local_surface_vtp_path = os.path.join(local_dir, "prediction_surface.vtp")
        await backend.copy_out(f"{remote_base}/output/prediction_0.vtp", local_surface_vtp_path)

    if compute_streamlines:
        report("Fetching volume prediction...")
        candidate_path = os.path.join(local_dir, "prediction_volume_grid.vti")
        try:
            await backend.copy_out(f"{remote_base}/output/prediction_volume_grid_0.vti", candidate_path)
            local_volume_vti_path = candidate_path
        except PredictionError:
            # Volume output isn't produced for a surface-only model -- that's
            # fine, the caller just won't get streamlines/volume visualization.
            local_volume_vti_path = None

    # Best-effort cleanup -- a failure here shouldn't fail the whole request,
    # the prediction is already copied back at this point.
    try:
        await backend.run(f"rm -rf {remote_base}")
    except PredictionError:
        pass

    report("Done.")
    return local_surface_vtp_path, local_volume_vti_path
