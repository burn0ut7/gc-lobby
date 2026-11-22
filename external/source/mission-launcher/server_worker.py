"""Single-owner dedicated server worker. Python standard library only.

The supervisor retains a live child handle; it never kills a process by saved PID.
After a supervisor crash, an ambiguous launch requires operator reconciliation.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from mission_launch import VANILLA, digest, guid, reference
from server_plan import make_plan, project_version, projects


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Launch API redirects are not allowed")


class Api:
    def __init__(self, url, key):
        parsed = urllib.parse.urlsplit(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Invalid launch API URL")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
            raise ValueError("Launch API requires HTTPS except on localhost")
        if not key:
            raise ValueError("Server credential is missing")
        self.url, self.key = url.rstrip("/"), key
        self.opener = urllib.request.build_opener(NoRedirect)

    def post(self, path, value):
        request = urllib.request.Request(self.url + path, data=json.dumps(value).encode(),
                                         headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
        with self.opener.open(request, timeout=15) as response:
            if response.status == 204:
                return None
            data = response.read(16 * 1024 * 1024 + 1)
            if len(data) > 16 * 1024 * 1024:
                raise ValueError("Launch API response exceeds limit")
            return json.loads(data)


def receipt_matches(receipt, plan, nonce):
    if receipt.get("version") != 1 or receipt.get("launchId") != nonce:
        return False
    identity = lambda value: reference(value)[0] if value.startswith("{") else guid(value)
    if identity(receipt["scenario"]) != reference(plan["scenario"])[0]:
        raise ValueError("Server loaded another scenario")
    if identity(receipt["world"]) != reference(plan["world"])[0]:
        raise ValueError("Server loaded another world")
    if receipt["worldFile"].replace("\\", "/").casefold() != plan["worldFile"].replace("\\", "/").casefold():
        raise ValueError("Actual loaded world does not match the requested world")
    actual = {x.upper() for x in receipt["addonGuids"]} - VANILLA
    expected = {x["modId"] for x in plan["mods"]}
    if actual != expected:
        raise ValueError(f"Loaded addon mismatch: missing={sorted(expected-actual)}, extra={sorted(actual-expected)}")
    return True


def arguments(plan, executable, run, nonce, local_base_project=None):
    """Private fresh profile and isolated local search directory; no shell launch."""
    config = run / "server.json"
    profile = run / "profile"
    profile.mkdir()
    save(config, plan["config"])
    args = [str(Path(executable).resolve()), "-profile", str(profile),
            "-gcLaunchId", nonce, "-noSplash", "-maxFPS", "60"]
    if plan["mode"] == "local":
        if plan["config"].get("bindPort", 2001) != 2001:
            raise ValueError("Local diagnostic launch uses the engine's RPL port 2001; JSON server settings require Workshop mode")
        if local_base_project:
            args.extend(["-gproj", str(Path(local_base_project).resolve())])
        stage = run / "addons"
        stage.mkdir()
        for mod in plan["mods"]:
            project = Path(mod["project"])
            if digest(project) != mod["projectSha256"]:
                raise ValueError("Installed project changed after planning")
            expected = plan.get("requiredAddonVersions", {}).get(mod["modId"])
            if expected and project_version(project, mod["modId"]) != expected:
                raise ValueError(f"Installed addon version changed after planning: {mod['modId']}")
            target = stage / mod["id"]
            if os.name == "nt":
                import _winapi
                _winapi.CreateJunction(str(project.parent), str(target))
            else:
                target.symlink_to(project.parent, target_is_directory=True)
        args.extend(["-addonsDir", str(stage), "-addons", ",".join(x["id"] for x in plan["mods"])])
        args.extend(["-server", plan["scenario"]])
    else:
        args.extend(["-config", str(config)])
        if plan.get("requiredAddonVersions"):
            args.extend(["-addonDownloadDir", str(run / "workshop")])
    return args, profile


class Worker:
    def __init__(self, settings, api):
        self.settings, self.api = settings, api
        self.directory = Path(settings["stateDirectory"]).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state_path = self.directory / "state.json"
        self.child = None
        self.output = None
        self.state = read(self.state_path) if self.state_path.exists() else {}
        if self.state.get("phase") in {"starting", "serving"}:
            raise ValueError("Previous server ownership is uncertain; reconcile its process and archive state.json before restarting")

    def persist(self):
        save(self.state_path, self.state)

    def stop_child(self):
        if self.child is not None:
            if self.child.poll() is None:
                self.child.terminate()
                try:
                    self.child.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    self.child.kill()
                    self.child.wait(timeout=10)
            self.child = None
        if self.output:
            self.output.close()
            self.output = None

    def step(self):
        if self.state.get("result"):
            self.api.post(f"/api/v1/catalog/launch-worker/{self.state['assignment']['requestId']}/result", self.state["result"])
            self.state = {"phase": "serving" if self.child and self.child.poll() is None else "idle"}
            self.persist()
            return
        if "claimId" not in self.state:
            self.state["claimId"] = str(uuid.uuid4())
            self.persist()
        assignment = self.api.post("/api/v1/catalog/launch-worker/claim", {"claimId": self.state["claimId"]})
        if assignment is None:
            return
        if assignment["claimId"] != self.state["claimId"]:
            raise ValueError("Queue returned another claim")
        if assignment["status"] != "running":
            # Lost acknowledgement after a terminal result; never execute it again.
            self.state = {"phase": "serving" if self.child and self.child.poll() is None else "idle"}
            self.persist()
            return
        self.state["assignment"] = assignment
        self.persist()
        plan = None
        pid = None
        try:
            deployment = self.settings["deployments"][assignment["missionId"]]
            plan = make_plan(assignment, deployment, projects(self.settings["projects"]),
                             self.settings["coreAddonIds"], self.settings["serverTemplate"], self.settings["mode"])
            run = self.directory / str(uuid.uuid4())
            run.mkdir()
            nonce = str(uuid.uuid4())
            args, profile = arguments(plan, self.settings["executable"], run, nonce, self.settings.get("localBaseProject"))
            save(run / "plan.json", plan)
            self.stop_child()
            self.state.update(phase="starting", run=str(run), nonce=nonce)
            self.persist()  # Crash from this point must not trigger an automatic duplicate launch.
            self.output = (run / "launcher.log").open("wb")
            self.child = subprocess.Popen(args, cwd=str(Path(args[0]).parent), stdout=self.output, stderr=subprocess.STDOUT,
                                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            pid = self.child.pid
            self.state["processId"] = pid
            self.persist()
            deadline = time.monotonic() + self.settings.get("startupTimeoutSeconds", 180)
            ready = False
            while time.monotonic() < deadline:
                if (self.directory / "stop.txt").exists():
                    raise ValueError("Worker shutdown requested during startup")
                if self.child.poll() is not None:
                    raise ValueError(f"Server exited during startup ({self.child.returncode})")
                receipt_path = profile / "profile" / "GCLaunchReceipt.json"
                if receipt_path.exists():
                    try:
                        receipt = read(receipt_path)
                    except (json.JSONDecodeError, OSError):
                        time.sleep(0.5)  # Writer may still be closing its receipt.
                        continue
                    if receipt_matches(receipt, plan, nonce):
                        if plan.get("requiredAddonVersions") and plan["mode"] == "workshop":
                            archives = run / "workshop" / "addons"
                            downloaded = projects(archives.glob("*/*.gproj"))
                            for identity, expected in plan["requiredAddonVersions"].items():
                                if downloaded.get(identity, {}).get("version") != expected:
                                    raise ValueError(f"Downloaded addon version mismatch for {identity}={expected}")
                        ready = True
                        break
                time.sleep(0.5)
            if not ready:
                raise ValueError("Server did not acknowledge the requested mission before startup timeout")
            status, summary = "completed", "Dedicated server confirmed scenario, world and exact addon set"
            self.state["phase"] = "serving"
        except (ValueError, OSError, KeyError, TypeError) as error:
            # Preserve an existing server when planning fails; stop only a new failed launch.
            if self.state.get("phase") == "starting":
                self.stop_child()
            status, summary = "failed", str(error)[:1900]
            self.state["phase"] = "serving" if self.child and self.child.poll() is None else "idle"
        self.state["result"] = {"claimId": self.state["claimId"], "status": status,
                                "summary": " ".join(summary.split()),
                                "planSha256": plan["planSha256"] if plan else "", "processId": pid}
        self.persist()
        print(status + ": " + summary, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True)
    args = parser.parse_args()
    settings = read(args.settings)
    directory = Path(settings["stateDirectory"]).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # OS lock releases on process exit; a stale filename is not a live owner.
    lock = (directory / "worker.lock").open("a+b")
    lock.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    worker = Worker(settings, Api(settings["apiUrl"], os.environ.get(settings["credentialEnvironment"], "")))
    try:
        while not (directory / "stop.txt").exists():
            try:
                worker.step()
            except (urllib.error.URLError, TimeoutError) as error:
                print("Launch API unavailable; retaining the same claim: " + type(error).__name__, flush=True)
            time.sleep(settings.get("pollSeconds", 3))
    finally:
        worker.stop_child()
        if worker.state.get("phase") in {"starting", "serving"}:
            # Keep any unreported outcome/claim for recovery, but no child survives clean shutdown.
            worker.state["phase"] = "idle"
            worker.persist()
        lock.close()


if __name__ == "__main__":
    main()
