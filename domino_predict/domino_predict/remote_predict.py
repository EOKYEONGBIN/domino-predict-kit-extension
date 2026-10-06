"""
SSH/SCP orchestration for the "Request Prediction" button: ships an STL to
the A6000 training server, runs DoMINO inference there (scripts/run_prediction.sh,
see ../../../scripts/run_prediction.sh), and brings the resulting surface
prediction (.vtp) and/or volume grid prediction (.vti) back. No omni/Kit
imports here -- this module only talks to the remote machine over ssh/scp.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid

SSH_USER = "YOUR_USER"  # SSH account on the inference server
SSH_KEY_PATH = os.path.expanduser("~/.ssh/id_ed25519")
REMOTE_REQUESTS_DIR = "~/domino-ahmedml/requests"
REMOTE_RUN_SCRIPT = "~/domino-ahmedml/scripts/run_prediction.sh"

# AUDIT FIX (2026-09-29): the remote host used to be a hard-coded module
# constant. It's now user-settable from the extension's Settings panel and
# persisted here, in a plain home-directory dotfile -- this extension has no
# existing settings/config infrastructure (no carb.settings registration, no
# extension-local data dir convention) to hook into, so a small standalone
# JSON file is the least-invasive way to remember it across sessions.
SETTINGS_PATH = os.path.expanduser("~/.domino_predict_settings.json")
DEFAULT_SSH_HOST = "192.168.0.100"  # example; set the real IP from the UI

SSH_HOST = DEFAULT_SSH_HOST
_SSH_BASE: list[str] = []
_SCP_BASE: list[str] = []


def _rebuild_command_bases() -> None:
    global _SSH_BASE, _SCP_BASE
    _SSH_BASE = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "-i", SSH_KEY_PATH, f"{SSH_USER}@{SSH_HOST}"]
    _SCP_BASE = ["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "-i", SSH_KEY_PATH]


def load_settings() -> dict:
    """Reads the persisted SSH host (if any) into the module-level SSH_HOST.
    Safe to call repeatedly; falls back to DEFAULT_SSH_HOST on any error
    (missing file, corrupt JSON, missing key) instead of raising, since this
    runs at extension startup and shouldn't be able to block it."""
    global SSH_HOST
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        host = data.get("ssh_host")
        if host:
            SSH_HOST = host
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    _rebuild_command_bases()
    return {"ssh_host": SSH_HOST}


def save_settings(ssh_host: str) -> None:
    global SSH_HOST
    SSH_HOST = ssh_host
    _rebuild_command_bases()
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump({"ssh_host": SSH_HOST}, f)


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


async def test_connection(host: str) -> tuple[bool, str]:
    """Quick reachability + key-auth check for the Settings panel's
    "Connect" button -- a bare `ssh ... echo ok`, using the same key/user as
    real requests but a short timeout so a wrong/unreachable IP fails fast
    instead of hanging the UI."""
    cmd = [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
        "-i", SSH_KEY_PATH, f"{SSH_USER}@{host}", "echo ok",
    ]
    try:
        output = await _run(cmd)
    except PredictionError as e:
        return False, f"Connection failed: {e}"
    if "ok" not in output:
        return False, f"Unexpected response from {host}: {output.strip()!r}"
    return True, f"Connected to {host} as {SSH_USER}."


async def request_prediction(
    stl_path: str,
    compute_faces: bool = True,
    compute_streamlines: bool = True,
    progress_cb=None,
) -> tuple[str | None, str | None]:
    """
    Sends `stl_path` to the A6000 and runs the trained DoMINO model on it,
    computing only what's actually requested:
      - compute_faces: predicted surface fields (pressure, wall shear
        stress), full-resolution on the STL's own triangles -- for Kit-CAE's
        Faces operator.
      - compute_streamlines: predicted volume fields (velocity, pressure,
        nut) as a structured grid (.vti) -- Kit-CAE's Streamlines operator
        needs real cell connectivity to advect through, which only the grid
        output has (the scattered point-cloud volume output,
        prediction_volume_0.vtp, fails there with "build_element_locator"
        not implemented for point clouds).
    Skipping whichever one isn't wanted saves real time on the remote side
    (measured: surface-only ~80s, volume-only ~16s, both ~81s -- the surface
    branch's STL subdivision dominates either way, so asking for volume
    alone is the case that actually pays off).

    Returns (local_surface_vtp_path_or_None, local_volume_vti_path_or_None)
    -- whichever wasn't requested comes back None, same as before for a
    volume-less (surface-only) model.
    """
    if not compute_faces and not compute_streamlines:
        raise PredictionError("Nothing requested -- select Faces and/or Streamlines first.")

    def report(msg: str):
        if progress_cb is not None:
            progress_cb(msg)

    request_id = uuid.uuid4().hex[:12]
    stl_name = os.path.basename(stl_path)
    remote_base = f"{REMOTE_REQUESTS_DIR}/{request_id}"

    report("Preparing remote request directory...")
    await _run(_SSH_BASE + [f"mkdir -p {remote_base}/input/case"])

    report(f"Uploading {stl_name}...")
    await _run(_SCP_BASE + [stl_path, f"{SSH_USER}@{SSH_HOST}:{remote_base}/input/case/{stl_name}"])

    report("Running DoMINO inference on the A6000 (this may take a while)...")
    surface_flag = "true" if compute_faces else "false"
    volume_flag = "true" if compute_streamlines else "false"
    await _run(_SSH_BASE + [f"{REMOTE_RUN_SCRIPT} {request_id} {surface_flag} {volume_flag}"])

    local_dir = tempfile.mkdtemp(prefix=f"domino_predict_{request_id}_")
    local_surface_vtp_path: str | None = None
    local_volume_vti_path: str | None = None

    if compute_faces:
        report("Downloading surface prediction...")
        local_surface_vtp_path = os.path.join(local_dir, "prediction_surface.vtp")
        await _run(_SCP_BASE + [f"{SSH_USER}@{SSH_HOST}:{remote_base}/output/prediction_0.vtp", local_surface_vtp_path])

    if compute_streamlines:
        report("Downloading volume prediction...")
        candidate_path = os.path.join(local_dir, "prediction_volume_grid.vti")
        try:
            await _run(_SCP_BASE + [f"{SSH_USER}@{SSH_HOST}:{remote_base}/output/prediction_volume_grid_0.vti", candidate_path])
            local_volume_vti_path = candidate_path
        except PredictionError:
            # Volume output isn't produced for a surface-only model -- that's
            # fine, the caller just won't get streamlines/volume visualization.
            local_volume_vti_path = None

    # Best-effort cleanup -- a failure here shouldn't fail the whole request,
    # the prediction is already downloaded at this point.
    try:
        await _run(_SSH_BASE + [f"rm -rf {remote_base}"])
    except PredictionError:
        pass

    report("Done.")
    return local_surface_vtp_path, local_volume_vti_path
