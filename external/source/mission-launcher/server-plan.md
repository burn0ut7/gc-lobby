# Dedicated server launch plan

`server_plan.py` resolves the website's immutable, authenticated queue assignment
against an operator-controlled deployment and explicit installed projects. It
does not claim that authoring metadata covers dynamic script references. The
existing `mission_launch.py` static scanner retains its separate, stricter
experimental contract.

The resolver includes the configured Core policy, mission owner, metadata addon
GUIDs, deployment supplemental requirements and their native `.gproj` dependency
closure. It also reads exact `GUID=version` requirements from the hash-verified
native header's `m_aRequiredAddonVersions` field. Each pin adds the addon to
the closure and must match the selected project's Workshop version. It rejects
absent/ambiguous addons, authoring collections, mismatched
catalogue revisions, changed request metadata, changed headers and scenario
identity mismatches. Every included addon records its reasons.

Inputs are private JSON files. An assignment has `requestId`, `missionId`, and
the `manifest` returned by `/api/v1/catalog/launch-worker/claim`. A deployment has:

- `missionId`, `scenario`, and `source`: exact assignment values.
- `manifestSha256`: SHA-256 of `server_plan.canonical(assignment['manifest'])`.
- `missionAddonId`: GUID of the deployed addon containing the scenario.
- `headerSha256`: SHA-256 of the deployed scenario header's bytes.
- `supplementalAddonIds`: optional known dynamic/script dependencies.
- `worldFile`: required only when the world's `.meta` is unavailable in the
  selected source projects. The trusted deployment records its addon-qualified
  path, such as `$MissionAddon:Worlds/Mission.ent`. Available world metadata
  instead establishes this path by its GUID. The worker checks the engine's
  actual loaded world against it, in addition to checking the scenario header.

Deployment records must be created by the trusted deployment process, not copied
from an incoming request at execution time. These checks bind the header and
metadata; they do not establish that every loose asset in a mutable development
checkout is an immutable published build.

Example:

```powershell
python server_plan.py --assignment request.json --deployment deployment.json `
  --template private-server.json --project C:\Mods\Core\addon.gproj `
  --project C:\Mods\Mission\addon.gproj --core-mod 623555110E2B2CA1 `
  --mode local --output private-plan.json
```

Supply all candidate installed projects explicitly; only the selected closure
enters the plan. Output creation refuses overwrite. The output includes the
private template and may contain passwords; do not publish production plans.

`workshop` mode requires installed `ServerData.json` version metadata for every
selected addon and emits those exact versions in `game.mods`. `.gproj`, not
ServerData, remains dependency authority. `local` mode emits an empty
`game.mods` and records exact project paths/identities/hashes in `mods`.
`server_worker.py` mounts only these projects through a fresh junction directory
and revalidates their project hashes and mission version pins before launch.
Workshop jobs with mission pins download into a job-specific directory and
verify the installed versions before acknowledging startup. Both modes retain
the requested scenario.

This module creates a plan only. The [worker](server-worker.md) consumes the
queue, starts the server and verifies its acknowledgement. Do not report a queue
request as completed on plan generation or process creation alone.

Validation: `python -m unittest -v test_server_plan test_mission_launch`.
The real RHS fixture resolved 32 selected addons from 165 installed candidates,
including GC Core/ACE/CSI, RHS/content packs and the independent nested-item
provider. That result proves selection, not server startup.
