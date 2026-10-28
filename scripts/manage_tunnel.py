"""Install, run, and inspect a token-file-based Cloudflare connector."""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "output/tunnel"
TOKEN = DIRECTORY / "token.txt"
EXECUTABLE = DIRECTORY / "cloudflared.exe"
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def settings():
    return json.loads((ROOT / "configs/tunnel.json").read_text(encoding="utf-8"))


def read_token(path: Path | None = None):
    path = path or TOKEN
    value = path.read_text(encoding="utf-8-sig").strip()
    try:
        decoded = json.loads(base64.b64decode(value, validate=True))
        if not isinstance(decoded, dict) or not all(decoded.get(key) for key in ("a", "t", "s")):
            raise ValueError
    except (ValueError, UnicodeError, TypeError):
        raise ValueError("Save only the complete connector token in the token file") from None
    return value


def install(config, proxy=None):
    item = config["cloudflared"]
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    if EXECUTABLE.exists() and hashlib.sha256(EXECUTABLE.read_bytes()).hexdigest() == item["sha256"]:
        return {"status": "installed", "version": item["version"], "sha256": item["sha256"]}
    temporary = EXECUTABLE.with_suffix(".download")
    proxies = {"https": proxy} if proxy else None
    checksum = hashlib.sha256()
    step = 4 * 1024 * 1024
    ranges = [(start, min(start + step, item["bytes"]) - 1) for start in range(0, item["bytes"], step)]

    def part(bounds):
        start, end = bounds
        with requests.get(item["url"] + "?part=" + str(start), headers={"Range": f"bytes={start}-{end}"},
                          proxies=proxies, timeout=(15, 45)) as response:
            response.raise_for_status()
            expected = f"bytes {start}-{end}/{item['bytes']}"
            if response.status_code != 206 or response.headers.get("Content-Range") != expected:
                raise ValueError("The release server returned an unexpected byte range")
            if len(response.content) != end - start + 1:
                raise ValueError("The downloaded byte range is incomplete")
            return response.content

    try:
        with temporary.open("wb") as handle, ThreadPoolExecutor(max_workers=4) as pool:
            for block in pool.map(part, ranges):
                handle.write(block)
                checksum.update(block)
        if checksum.hexdigest() != item["sha256"]:
            raise ValueError("The connector checksum does not match the pinned release")
        temporary.replace(EXECUTABLE)
    finally:
        temporary.unlink(missing_ok=True)
    version = subprocess.run([str(EXECUTABLE), "--version"], capture_output=True, text=True,
                             check=True, timeout=15, creationflags=FLAGS).stdout.strip()
    if item["version"] not in version:
        raise ValueError("The connector version does not match the pinned release")
    result = {"status": "installed", "version": item["version"], "sha256": item["sha256"]}
    (DIRECTORY / "installation.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def status(config):
    session = requests.Session()
    session.trust_env = False
    result = {"token_file_present": TOKEN.is_file(), "connector_installed": EXECUTABLE.is_file(),
              "public_url": "https://" + config["hostname"] + "/"}
    try:
        response = session.get(config["origin"] + "/experience/api/health", timeout=3)
        payload = response.json()
        result["backend_ready"] = response.status_code == 200 and payload.get("application") == "FinEvoLab Experience"
        result["public_origin_configured"] = payload.get("public_origin") == "https://" + config["hostname"]
    except (requests.RequestException, ValueError):
        result.update(backend_ready=False, public_origin_configured=False)
    try:
        result["tunnel_ready"] = session.get("http://" + config["metrics"] + "/ready", timeout=3).status_code == 200
    except requests.RequestException:
        result["tunnel_ready"] = False
    return result


def start(config):
    if not EXECUTABLE.is_file():
        raise ValueError("Install the connector first")
    if hashlib.sha256(EXECUTABLE.read_bytes()).hexdigest() != config["cloudflared"]["sha256"]:
        raise ValueError("The connector checksum does not match the pinned release")
    read_token()
    current = status(config)
    if current["tunnel_ready"]:
        pid_path = DIRECTORY / "connector.pid"
        if not pid_path.is_file() or not process(int(pid_path.read_text())):
            raise ValueError("The metrics port belongs to an unmanaged connector")
        return {"status": "already_connected"}
    if not current["backend_ready"] or not current["public_origin_configured"]:
        raise ValueError("Start the analysis backend with the configured public URL first")
    pid_path = DIRECTORY / "connector.pid"
    if pid_path.exists():
        pid = int(pid_path.read_text())
        if process(pid):
            return {"status": "connecting", "pid": pid}
    command = [str(EXECUTABLE), "tunnel", "--no-autoupdate", "--metrics", config["metrics"],
               "--protocol", config["protocol"], "run", "--token-file", str(TOKEN)]
    with (DIRECTORY / "connector.stdout.log").open("ab") as stdout, (DIRECTORY / "connector.stderr.log").open("ab") as stderr:
        child = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
                                 creationflags=FLAGS)
    pid_path.write_text(str(child.pid) + "\n", encoding="ascii")
    return {"status": "connecting", "pid": child.pid}


def process(pid):
    if os.name != "nt":
        raise ValueError("Connector process management requires Windows")
    query = f"Get-CimInstance Win32_Process -Filter 'ProcessId = {int(pid)}' | Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", query], capture_output=True,
                            text=True, check=True, timeout=10, creationflags=FLAGS)
    if not result.stdout.strip():
        return None
    info = json.loads(result.stdout)
    if (info.get("ExecutablePath") or "").lower() != str(EXECUTABLE.resolve()).lower():
        raise ValueError("The saved PID belongs to another executable")
    if str(TOKEN).lower() not in (info.get("CommandLine") or "").lower():
        raise ValueError("The saved PID belongs to another connector")
    return info


def stop():
    path = DIRECTORY / "connector.pid"
    if not path.exists():
        return {"status": "stopped"}
    pid = int(path.read_text())
    if process(pid):
        subprocess.run(["taskkill.exe", "/PID", str(pid)], capture_output=True, check=True,
                       timeout=15, creationflags=FLAGS)
    path.unlink(missing_ok=True)
    return {"status": "stopped"}


def verify(config, proxy=None):
    result = status(config)
    if not all(result.get(key) for key in ("backend_ready", "public_origin_configured", "tunnel_ready")):
        raise ValueError("The local backend and connector must be ready before public verification")
    session = requests.Session()
    if proxy:
        session.proxies.update({"https": proxy})
    origin = "https://" + config["hostname"]
    response = session.get(origin + "/experience/api/health", timeout=25)
    response.raise_for_status()
    if response.url != origin + "/experience/api/health" or response.json().get("public_origin") != origin:
        raise ValueError("The public route does not reach the configured analysis backend")
    response = session.get(origin + "/", timeout=25)
    response.raise_for_status()
    if 'data-live="true"' not in response.text:
        raise ValueError("The public workspace is not connected to the analysis backend")
    if session.get(origin + "/experience/api/tasks", timeout=15).status_code != 401:
        raise ValueError("Anonymous task access must be rejected")
    result.update(status="passed", anonymous_tasks="rejected", https=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "start", "status", "stop", "verify"))
    parser.add_argument("--proxy")
    args = parser.parse_args()
    config = settings()
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    if args.action == "install":
        result = install(config, args.proxy)
    elif args.action == "start":
        result = start(config)
    elif args.action == "stop":
        result = stop()
    elif args.action == "verify":
        result = verify(config, args.proxy)
    else:
        result = status(config)
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, requests.RequestException, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}), file=sys.stderr)
        sys.exit(1)
