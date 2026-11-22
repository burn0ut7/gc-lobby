"""Resolve catalogue launch requests against operator-controlled deployments.

This consumes authoring evidence, not the static scanner's completeness claim.
Actual engine readiness must be verified separately before reporting success.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import uuid

from mission_launch import VANILLA, digest, field, guid, reference, root, unquote

COLLECTIONS = {"GlobalConflictsWorkbench", "GlobalConflictsWorkshop"}
VERSION = r"[0-9]+(?:\.[0-9]+){1,3}"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def project_version(path, identity):
    server_data = path.with_name("ServerData.json")
    if not server_data.exists():
        return ""
    data = json.loads(server_data.read_text(encoding="utf-8-sig"))
    if guid(data["id"]) != identity:
        raise ValueError(f"Installed version identity mismatch: {path}")
    version = data["revision"]["version"]
    if not isinstance(version, str) or not re.fullmatch(VERSION, version):
        raise ValueError(f"Invalid installed version: {path}")
    return version


def required_addon_versions(header):
    pins = {}
    for token in field(header, "m_aRequiredAddonVersions", True):
        match = re.fullmatch(rf"([0-9A-Fa-f]{{16}})=({VERSION})", unquote(token))
        if not match:
            raise ValueError("Invalid required addon version")
        identity, version = match[1].upper(), match[2]
        if identity in VANILLA or identity in pins:
            raise ValueError(f"Duplicate or vanilla required addon version: {identity}")
        pins[identity] = version
    return pins


def projects(paths):
    """Index explicitly installed projects without scanning their resource trees."""
    result = {}
    names = set()
    for path in sorted({Path(p).resolve() for p in paths}):
        heading, body = root(path)
        if heading != ["GameProject"]:
            raise ValueError(f"Invalid project: {path}")
        identity = guid(field(body, "GUID"))
        name = field(body, "ID")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name):
            raise ValueError(f"Invalid addon name: {path}")
        if identity in result or name.casefold() in names:
            raise ValueError(f"Ambiguous installed addon: {identity}/{name}")
        names.add(name.casefold())
        version = project_version(path, identity)
        result[identity] = {"id": name, "version": version, "project": str(path), "projectSha256": digest(path),
                            "dependencies": [guid(unquote(v)) for v in field(body, "Dependencies", True)]}
    return result


def make_plan(assignment, deployment, installed, core_ids, template, mode):
    """No paths or executable arguments are accepted from a queue request.

    A deployment record binds an imported catalogue revision to a saved header.
    It is provisioned by the operator, alongside the deployed addon build.
    supplementalAddonIds covers known dynamic/script requirements.
    """
    request_id = str(uuid.UUID(assignment["requestId"]))
    mission_id = str(uuid.UUID(assignment["missionId"]))
    manifest = assignment["manifest"]
    if mode not in {"local", "workshop"}:
        raise ValueError("Explicit local or workshop launch mode required")
    if mission_id != deployment["missionId"] or manifest["source"] != deployment["source"]:
        raise ValueError("Mission deployment does not match the requested catalogue revision")
    if hashlib.sha256(canonical(manifest)).hexdigest() != deployment["manifestSha256"]:
        raise ValueError("Request metadata does not match deployment")
    scenario = manifest["scenario"]
    _, relative = reference(scenario)
    if scenario != deployment["scenario"] or not relative.lower().endswith(".conf"):
        raise ValueError("Scenario does not match deployment")
    coverage = manifest["dependencyResolution"]
    if coverage not in {"declared-only", "authored_container_graph_partial"}:
        raise ValueError("Unsupported dependency evidence")
    owner = guid(deployment["missionAddonId"])
    if owner in VANILLA or owner not in installed:
        raise ValueError("Mission addon is not installed")
    header = Path(installed[owner]["project"]).parent / relative
    header.resolve().relative_to(Path(installed[owner]["project"]).parent)
    if digest(header) != deployment["headerSha256"]:
        raise ValueError("Deployed mission header changed")
    _, meta = root(str(header) + ".meta")
    if field(meta, "Name") != scenario:
        raise ValueError("Deployed scenario identity changed")
    _, header_body = root(header)
    world = field(header_body, "World")
    reference(world)
    pins = required_addon_versions(header_body)

    core = sorted({guid(x) for x in core_ids})
    if not core or any(x in VANILLA for x in core):
        raise ValueError("An explicit non-vanilla Core policy is required")
    required = sorted({guid(x) for x in manifest["requiredAddonIds"]})
    supplemental = sorted({guid(x) for x in deployment.get("supplementalAddonIds", [])})
    selected = {}

    def add(identity, reason):
        if identity in VANILLA:
            return
        if identity in selected:
            if reason not in selected[identity]["reasons"]:
                selected[identity]["reasons"].append(reason)
            return
        if identity not in installed:
            raise ValueError(f"Required addon is not installed: {identity} ({reason})")
        project = installed[identity]
        if project["id"] in COLLECTIONS:
            raise ValueError("Authoring collections cannot be server requirements")
        selected[identity] = {"modId": identity, **project, "reasons": [reason]}
        for dependency in project["dependencies"]:
            add(dependency, f"dependency of {identity}")

    for identity in core:
        add(identity, "server Core policy")
    add(owner, "mission owner")
    for identity in required:
        add(identity, "mission metadata")
    for identity in supplemental:
        add(identity, "deployment supplemental requirement")
    for identity, version in pins.items():
        add(identity, "mission addon version requirement")
        if selected[identity]["version"] != version:
            raise ValueError(f"Installed addon version mismatch for {identity}={version}")
    world_guid, world_path = reference(world)
    world_files = []
    for mod in selected.values():
        meta_path = Path(mod["project"]).parent / (world_path + ".meta")
        if meta_path.is_file():
            _, world_meta = root(meta_path)
            if reference(field(world_meta, "Name"))[0] == world_guid:
                world_files.append(f"${mod['id']}:{world_path}")
    if len(world_files) > 1:
        raise ValueError("Ambiguous world ownership")
    world_file = world_files[0] if world_files else deployment.get("worldFile", "")
    allowed_world_files = {f"${m['id']}:{world_path}" for m in selected.values()} | {f"$ArmaReforger:{world_path}", f"$core:{world_path}"}
    if world_file not in allowed_world_files:
        raise ValueError("Deployment must identify the packed world's addon-qualified worldFile")
    config = copy.deepcopy(template)
    config.setdefault("game", {})["scenarioId"] = scenario
    if mode == "workshop" and any(not mod["version"] for mod in selected.values()):
        raise ValueError("Workshop launch requires installed versions for every selected addon")
    config["game"]["mods"] = ([{"modId": identity, "version": selected[identity]["version"], "required": True}
                               for identity in sorted(selected)] if mode == "workshop" else [])
    plan = {"version": 1, "mode": mode, "requestId": request_id, "missionId": mission_id,
            "source": manifest["source"], "scenario": scenario, "world": world, "worldFile": world_file,
            "dependencyResolution": coverage, "headerSha256": deployment["headerSha256"],
            "mods": [selected[x] for x in sorted(selected)], "config": config}
    if pins:
        plan["requiredAddonVersions"] = pins
    plan["planSha256"] = hashlib.sha256(canonical(plan)).hexdigest()
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("assignment", "deployment", "template", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--project", action="append", required=True)
    parser.add_argument("--core-mod", action="append", required=True)
    parser.add_argument("--mode", choices=["local", "workshop"], required=True)
    args = parser.parse_args()
    read = lambda path: json.loads(path.read_text(encoding="utf-8-sig"))
    plan = make_plan(read(args.assignment), read(args.deployment), projects(args.project),
                     args.core_mod, read(args.template), args.mode)
    # May contain server passwords inherited from the private template.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(plan, stream, indent=2)
        stream.write("\n")
    print(f"Resolved {len(plan['mods'])} addons; plan {plan['planSha256']}")


if __name__ == "__main__":
    main()
