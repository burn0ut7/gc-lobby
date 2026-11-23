"""Run a matrix of isolated legacy and Dev dedicated-server smoke tests."""

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import time


ONLINE = "Entered online game state."
STARTUP_FAILURE = re.compile(
    r"Can.t compile|Cannot (?:load world|open scenario)|(?:failed|unable) to initialize the game", re.I
)
RESOURCE_FAILURE = re.compile(r"Wrong GUID|Can.t (?:load|open)|Cannot (?:load|open)|Failed to (?:load|open)|Missing (?:prefab|resource)|resource .*not found", re.I)
REFERENCE = re.compile(r"\{([0-9a-f]{16})\}([^\r\n]+)", re.I)
WORLD_LOAD = re.compile(r"\bEntities load '(?:\$[^:']+:)?([^']+)'", re.I)
ADDON = re.compile(r"[a-z0-9_.-]+", re.I)


def inspect_log(log, case):
    expected_world = case["world"].casefold()
    expected_scenario = case["scenario"].casefold()
    scenario_loaded = False
    world_loaded = False
    pregame = False
    missing = {}
    for number, line in enumerate(log.splitlines(), 1):
        text = line.casefold()
        scenario_loaded |= "starting new playthrough" in text and expected_scenario in text
        if not world_loaded and (world := WORLD_LOAD.search(line)):
            world_loaded = world[1].casefold() == expected_world
        pregame |= "gc_lobbygamemode" in text and "state=pregame" in text
        if RESOURCE_FAILURE.search(line):
            for resource in case.get("watch", []):
                guid = resource.split("}", 1)[0].casefold() + "}"
                if guid in text:
                    missing.setdefault(resource, number)
    return {
        "online": ONLINE in log,
        "scenarioLoaded": scenario_loaded,
        "worldLoaded": world_loaded,
        "lobbyPregame": pregame,
        "missingWatchedReferences": missing,
    }


def run(executable, base, profile, case, timeout):
    profile.mkdir()
    args = [
        str(executable), "-gproj", str(base), "-profile", str(profile),
        "-addonsDir", str(Path(case["providerDirectory"]).resolve()),
        "-addonDownloadDir", str(profile / "workshop"),
        "-addons", ",".join(case["addons"]), "-server", case["scenario"], "-maxFPS", "30",
    ]
    console = None
    log = ""
    with (profile / "process.log").open("wb") as output:
        process = subprocess.Popen(args, cwd=executable.parent, stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            deadline = time.monotonic() + timeout
            state = "timeout"
            while time.monotonic() < deadline:
                if console is None:
                    console = next(profile.glob("logs/*/console.log"), None)
                if console is not None:
                    log = console.read_text(encoding="utf-8", errors="replace")
                    if ONLINE in log:
                        state = "online"
                        break
                    if STARTUP_FAILURE.search(log):
                        state = "startup-error"
                        break
                if process.poll() is not None:
                    state = "exited"
                    break
                time.sleep(0.5)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    return {"state": state, "console": str(console) if console else None, **inspect_log(log, case)}


def validate(cases):
    if not cases:
        raise ValueError("the batch must contain at least one case")
    names = set()
    for case in cases:
        name = case["name"]
        if not ADDON.fullmatch(name) or name in {".", ".."} or name.casefold() in names:
            raise ValueError(f"invalid or duplicate case name: {name}")
        names.add(name.casefold())
        scenario = REFERENCE.fullmatch(case["scenario"])
        if not scenario or not scenario[2].startswith("Missions/") or not scenario[2].endswith(".conf"):
            raise ValueError(f"invalid scenario for {name}")
        world = PurePosixPath(case["world"])
        if world.is_absolute() or ".." in world.parts or world.suffix != ".ent":
            raise ValueError(f"invalid world for {name}")
        addons = case["addons"]
        if not addons or not all(ADDON.fullmatch(addon) and addon not in {".", ".."} for addon in addons):
            raise ValueError(f"invalid addon list for {name}")
        root = Path(case["providerDirectory"])
        if not root.is_dir() or not list(root.glob("*/*.gproj")):
            raise ValueError(f"no staged addon projects for {name}: {root}")
        if any(not REFERENCE.fullmatch(resource) for resource in case.get("watch", [])):
            raise ValueError(f"invalid watched resource for {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--server", required=True, type=Path)
    parser.add_argument("--base-project", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="new directory for profiles and the JSON report")
    parser.add_argument("--timeout", type=float, default=60, help="maximum startup seconds per case")
    args = parser.parse_args()
    try:
        cases = json.loads(args.cases.read_text(encoding="utf-8-sig"))
        validate(cases)
        executable = args.server.resolve(strict=True)
        base = args.base_project.resolve(strict=True)
        if not executable.is_file() or not base.is_file() or not 0 < args.timeout <= 600:
            raise ValueError("server and base project files and a timeout of up to 600 seconds are required")
        output = args.output.resolve()
        output.mkdir()
    except (KeyError, OSError, TypeError, ValueError) as error:
        parser.error(str(error))

    results = {}
    failed = False
    for case in cases:
        name = case["name"]
        try:
            result = run(executable, base, output / name, case, args.timeout)
        except OSError as error:
            result = {"state": "harness-error", "error": str(error)}
        passed = bool(result.get("online") and result.get("scenarioLoaded") and result.get("worldLoaded")
                      and (not case.get("expectLobby") or result.get("lobbyPregame"))
                      and not result.get("missingWatchedReferences"))
        results[name] = {**result, "passed": passed}
        temporary = output / "results.tmp"
        temporary.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output / "results.json")
        failed |= not passed
        print(f"{name} {result['state']} ({'pass' if passed else 'review'})", flush=True)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
