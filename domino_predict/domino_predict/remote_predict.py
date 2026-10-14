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
# The Install button installs this distro when no Ubuntu is present.
LOCAL_DISTRO = "Ubuntu-24.04"
# torch cu130 (CUDA 13) needs an NVIDIA driver from the R580 branch or newer.
# WSL2 uses the Windows driver, so this is the Windows driver version.
MIN_DRIVER_MAJOR = 580
NVIDIA_DRIVER_URL = "https://www.nvidia.com/Download/index.aspx"
# Ubuntu distro the local backend runs in, found by _diagnose_wsl(). Commands
# target it explicitly so another default distro (e.g. docker-desktop, which
# has no bash) doesn't break them.
_distro: str | None = None

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
    distro = ["-d", _distro] if _distro else []
    return ["wsl.exe", *distro, "-e", "bash", "-lc", command]


async def _run_wsl_exe(args: list[str], timeout: float = 60) -> tuple[int, str]:
    """Runs wsl.exe itself (e.g. -l -v, --set-version) and returns (exit code,
    output); exit code -1 if wsl.exe is missing or the call timed out.
    WSL_UTF8 makes wsl.exe's own messages UTF-8 instead of UTF-16, and stdin is
    closed so Ubuntu's first-run prompt (new user name) fails instead of
    waiting forever."""
    env = dict(os.environ, WSL_UTF8="1")
    try:
        proc = await asyncio.create_subprocess_exec(
            "wsl.exe", *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
    except OSError as e:
        return -1, str(e)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, f"wsl.exe {' '.join(args)} timed out"
    text = out.decode("utf-16-le", errors="replace") if b"\x00" in out else out.decode(errors="replace")
    return proc.returncode, text.replace("\x00", "").strip()


def _last_lines(text: str, n: int = 4) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


async def _diagnose_wsl() -> tuple[str, str]:
    """Finds the Ubuntu distro and checks it can run a command. Returns
    (state, detail), state one of:
      ok          ready to use
      no_wsl      WSL itself is not installed / not enabled
      no_ubuntu   WSL works but no Ubuntu distro is installed
      wsl1        Ubuntu runs on WSL 1 (no GPU access)
      needs_user  Ubuntu's first-run user setup was never done
      broken      Ubuntu exists but does not start (reboot / virtualization)"""
    global _distro
    rc, out = await _run_wsl_exe(["-l", "-v"], timeout=30)
    rows = []  # (name, version)
    for line in out.splitlines():
        parts = line.strip().lstrip("*").split()
        if len(parts) >= 3 and parts[-1] in ("1", "2"):
            rows.append((" ".join(parts[:-2]), parts[-1]))
    if not rows:
        # `wsl -l -v` also fails when WSL works but has no distro at all, so
        # ask `wsl --version` whether WSL itself is there.
        rc_version, _ = await _run_wsl_exe(["--version"], timeout=30)
        return ("no_ubuntu" if rc_version == 0 else "no_wsl"), out
    ubuntu = [r for r in rows if r[0] == LOCAL_DISTRO] or [r for r in rows if r[0].lower().startswith("ubuntu")]
    if not ubuntu:
        return "no_ubuntu", "Installed: " + ", ".join(r[0] for r in rows)
    name, version = ubuntu[0]
    _distro = name
    if version == "1":
        return "wsl1", name
    rc, out = await _run_wsl_exe(["-d", name, "-e", "bash", "-lc", "echo ok"])
    if rc == 0 and "ok" in out:
        return "ok", name
    rc_root, out_root = await _run_wsl_exe(["-d", name, "-u", "root", "-e", "bash", "-lc", "echo ok"])
    if rc_root == 0 and "ok" in out_root:
        return "needs_user", out
    return "broken", out or out_root


def _wsl_state_message(state: str, detail: str) -> str:
    if state == "no_wsl":
        return ("WSL2 is not installed. Click 'Install Local Environment' "
                "(it asks for administrator permission and may need a restart).")
    if state == "no_ubuntu":
        return f"WSL2 is installed but {LOCAL_DISTRO} is not. Click 'Install Local Environment'."
    if state == "wsl1":
        return f"{detail} runs on WSL 1, but the GPU needs WSL 2. Click 'Install Local Environment' to convert it."
    if state == "needs_user":
        return "Ubuntu has not been set up yet. Click 'Install Local Environment'."
    return ("WSL2 is installed but Ubuntu does not start. Restart Windows; if it still fails, "
            "enable virtualization (VT-x / SVM) in the BIOS.\n" + _last_lines(detail))


async def _check_driver() -> tuple[bool, str]:
    """GPU visible inside WSL, with a driver new enough for CUDA 13."""
    try:
        out = await _run(_wsl_cmd("nvidia-smi --query-gpu=driver_version --format=csv,noheader"))
    except (PredictionError, OSError):
        return False, "WSL2 cannot see the NVIDIA GPU. Install the Windows NVIDIA driver."
    version = out.strip().splitlines()[0].strip() if out.strip() else ""
    try:
        major = int(version.split(".")[0])
    except ValueError:
        return True, version  # unknown format: let the install itself decide
    if major < MIN_DRIVER_MAJOR:
        return False, (f"NVIDIA driver {version} is too old for CUDA 13 (needs {MIN_DRIVER_MAJOR} or newer). "
                       "Update the Windows NVIDIA driver, then click Install again.")
    return True, version


async def _install_wsl_elevated() -> tuple[bool, str]:
    """Installing WSL itself needs administrator rights: runs wsl --install
    through a UAC prompt and waits for it. On a PC without WSL this usually
    installs only WSL and asks for a restart; Ubuntu is installed after that
    by _install_ubuntu(). --no-launch skips opening an Ubuntu window."""
    ps = (f"$p = Start-Process -FilePath wsl.exe -ArgumentList '--install','-d','{LOCAL_DISTRO}','--no-launch' "
          "-Verb RunAs -Wait -PassThru; exit $p.ExitCode")
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell.exe", "-NoProfile", "-Command", ps,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as e:
        return False, str(e)
    out, _ = await proc.communicate()
    return proc.returncode == 0, out.decode(errors="replace")


async def _install_ubuntu() -> tuple[bool, str]:
    """Installs the Ubuntu distro into an existing WSL. Needs no administrator
    rights; --no-launch skips Ubuntu's window and its interactive user setup
    (the distro then starts as root). Downloads ~300MB."""
    rc, out = await _run_wsl_exe(["--install", "-d", LOCAL_DISTRO, "--no-launch"], timeout=1800)
    return rc == 0, out


async def _make_root_default(name: str) -> None:
    """Skips Ubuntu's interactive first-run user setup: makes root the default
    user (no password prompt, and the setup script needs no sudo)."""
    await _run_wsl_exe([
        "-d", name, "-u", "root", "-e", "bash", "-lc",
        r"grep -q '^\[user\]' /etc/wsl.conf 2>/dev/null || printf '\n[user]\ndefault=root\n' >> /etc/wsl.conf",
    ])
    await _run_wsl_exe(["--terminate", name])


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
    state, detail = await _diagnose_wsl()
    if state != "ok":
        return False, _wsl_state_message(state, detail)
    ok, msg = await _check_driver()
    if not ok:
        return False, msg
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
    """Gets this PC ready for local inference: installs WSL2 if needed (UAC
    prompt; a first install usually needs a restart and a second click), then
    Ubuntu (no prompt),
    checks the NVIDIA driver, then runs wsl_setup/setup_local_inference.sh
    inside Ubuntu (no sudo needed) and forwards its "=== ... ===" step lines
    to progress_cb. The environment part takes roughly 10-20 minutes on a
    fresh machine (PyTorch + cuML downloads, ~7GB)."""

    def report(msg: str):
        if progress_cb is not None:
            progress_cb(msg)

    # 1) WSL2 + Ubuntu: WSL through a UAC prompt if missing, Ubuntu without
    #    one, convert WSL 1 to 2, skip Ubuntu's interactive first-run setup.
    state, detail = await _diagnose_wsl()
    if state == "no_wsl":
        report("Installing WSL2 -- approve the Windows administrator prompt...")
        ok, out = await _install_wsl_elevated()
        if not ok:
            return False, "WSL install was cancelled or failed.\n" + _last_lines(out)
        state, detail = await _diagnose_wsl()
        if state == "no_wsl":
            return False, "WSL2 was installed. Restart Windows, then click 'Install Local Environment' again."
    if state == "no_ubuntu":
        report(f"Installing {LOCAL_DISTRO} (no administrator prompt, ~300MB)...")
        ok, out = await _install_ubuntu()
        state, detail = await _diagnose_wsl()
        if state in ("no_wsl", "no_ubuntu"):
            return False, (f"Could not install {LOCAL_DISTRO}. If WSL was just installed, restart Windows "
                           "and click 'Install Local Environment' again.\n" + _last_lines(out))
    if state == "wsl1":
        report(f"Converting {detail} to WSL 2...")
        rc, out = await _run_wsl_exe(["--set-version", _distro, "2"], timeout=1800)
        if rc != 0:
            return False, ("Could not switch Ubuntu to WSL 2. Enable virtualization (VT-x / SVM) in the BIOS "
                           "and restart.\n" + _last_lines(out))
        state, detail = await _diagnose_wsl()
    if state == "needs_user":
        report("Setting up Ubuntu (first run)...")
        await _make_root_default(_distro)
        state, detail = await _diagnose_wsl()
    if state != "ok":
        return False, _wsl_state_message(state, detail)

    # 2) GPU driver: checked, not installed -- driver packages are specific to
    #    the GPU/laptop maker, and Kit-CAE already needs a working one.
    ok, msg = await _check_driver()
    if not ok:
        if "too old" in msg:
            import webbrowser

            webbrowser.open(NVIDIA_DRIVER_URL)
        return False, msg

    # 3) Our inference environment inside Ubuntu.
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

    # realpath expands 8.3 short names (C:/Users/KYEONG~1/...), which Kit's
    # file picker can't open from the Payloads asset-path button.
    local_dir = os.path.realpath(tempfile.mkdtemp(prefix=f"domino_predict_{request_id}_"))
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
