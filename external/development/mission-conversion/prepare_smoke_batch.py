"""Stage held conversion drafts and generate paired dedicated-server cases."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess

from catalog import Snapshot
from dependencies import database_project, installed_version
from launchers import META_NAME, WORLD_REF, required_addon_versions
from smoke_batch import validate


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def safe_path(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError(f"draft path escapes its directory: {relative}")
    return path


def scenario(header, contents, *, allow_stale_path=False):
    name = META_NAME.search(contents)
    if not name or (name[3] != header and not allow_stale_path):
        raise ValueError(f"unexpected launcher registration for {header}")
    return f"{{{name[2]}}}{header}"


def world_path(header, contents):
    world = WORLD_REF.search(contents)
    if not world:
        raise ValueError(f"no world reference for {header}")
    return world[3]


def verify_pins(directory, pins):
    if not pins:
        return
    wanted = dict(pins)
    versions = {}
    for database in directory.glob("*/resourceDatabase.rdb"):
        provider = database_project(database)
        if provider and provider[0] in wanted:
            addon = provider[0]
            if addon in versions:
                raise ValueError(f"duplicate pinned addon {addon} in {directory}")
            versions[addon] = installed_version(database, addon)
    if versions != wanted:
        raise ValueError(f"version pins mismatch in {directory}: expected {wanted}, found {versions}")


def prepare(repo, audit, drafts, output, graphs):
    plan = read(audit / "plan.json")
    review = read(audit / "review.json")
    inventory_path = audit / "inventory.json"
    inventory = read(inventory_path)
    if sha256(inventory_path.read_bytes()).hexdigest() != plan["inventorySha256"]:
        raise ValueError("plan and inventory came from different audits")
    if any((data["source"], data["target"]) != (plan["source"], plan["target"])
           for data in (inventory, review)) or not review["includesDraft"]:
        raise ValueError("the audit must include drafts and matching source and Dev revisions")
    source = Snapshot(repo, plan["source"])
    dev = Snapshot(repo, plan["target"])
    batch = inventory["batchMigration"]
    headers = plan["remainingLaunchers"]
    if not headers:
        raise ValueError("the plan has no held launchers")

    files = {path for path in dev.files if path == "addon.gproj" or path.startswith("Scripts/")}
    overlay = {}
    cases = []
    checked = set()
    for header, entry in headers.items():
        mission = review["missions"][header]
        for kind in ("same", "changed", "devOnly"):
            files.update(path for path in mission["files"][kind] if path in dev.files)
        for path in batch["worlds"][entry["world"]]["files"]:
            body = safe_path(drafts, path).read_bytes()
            if sha256(body).hexdigest() != batch["sha256"][path]:
                raise ValueError(f"draft changed since generation: {path}")
            overlay[path] = body

        converted = overlay[header].decode("utf-8-sig")
        pins = required_addon_versions(converted)
        pin_key = ",".join(f"{guid}={version}" for guid, version in pins)
        graph_key = pin_key or "default"
        watched = [f"{{{ref['guid']}}}{ref['path']}" for ref in mission["unresolvedExternalReferences"]]
        for side, contents, meta in (
            ("dev", converted, overlay[header + ".meta"].decode("utf-8-sig")),
            ("legacy", source.read(header), source.read(header + ".meta")),
        ):
            graph = graphs[side][graph_key]
            directory = Path(graph["providerDirectory"]).resolve()
            if (directory, pins) not in checked:
                verify_pins(directory, pins)
                checked.add((directory, pins))
            cases.append({
                "name": side + "-" + header.removeprefix("Missions/").removesuffix(".conf").replace("/", "-"),
                # Some published registrations retain a stale path in the source .meta.
                "scenario": scenario(header, meta, allow_stale_path=side == "legacy"),
                "world": world_path(header, contents),
                "providerDirectory": str(directory),
                "addons": graph["addons"],
                "expectLobby": side == "dev",
                "watch": watched,
            })

    validate(cases)
    project = output / "missions"
    for path in files | overlay.keys():
        safe_path(project, path)
    output.mkdir()
    for path in sorted(files | overlay.keys()):
        destination = safe_path(project, path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(overlay[path] if path in overlay else dev.read_raw(path))
    (output / "cases.json").write_text(json.dumps(cases, indent=2) + "\n", encoding="utf-8")
    print(f"Staged {len(headers)} launchers and {len(overlay)} audited draft files for {len(cases)} smoke cases")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--missions", required=True, type=Path)
    parser.add_argument("--audit", required=True, type=Path, help="directory containing plan, review and inventory JSON")
    parser.add_argument("--drafts", required=True, type=Path)
    parser.add_argument("--graphs", required=True, type=Path, help="legacy and Dev addon graphs, keyed by version pins")
    parser.add_argument("--output", required=True, type=Path, help="new directory outside the Missions checkout")
    args = parser.parse_args()
    try:
        repo = args.missions.resolve(strict=True)
        output = args.output.resolve()
        lobby = Path(__file__).resolve().parents[3]
        if any(output.is_relative_to(checkout) for checkout in (repo, lobby)):
            raise ValueError("stage smoke tests outside the Missions and Lobby checkouts")
        prepare(repo, args.audit, args.drafts.resolve(strict=True), output, read(args.graphs))
    except (KeyError, OSError, TypeError, ValueError, subprocess.CalledProcessError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
