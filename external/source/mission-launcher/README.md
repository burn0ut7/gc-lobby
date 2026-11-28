# Launch a GC mission on Windows

Copy [Start-GCMission.bat](../../Start-GCMission.bat) beside
`ArmaReforgerServer.exe` and your existing server JSON. Drop a mission header
from its addon folder, or its portable `.launch.json`, onto the batch file.
The batch file contains the launcher and native reader and runs with Windows
PowerShell 5.1. You can also call it from a terminal or scheduler:

```powershell
.\Start-GCMission.bat "C:\GC\missions\Operation.launch.json"
.\Start-GCMission.bat "C:\GC\MissionAddon\Missions\Operation.conf" -Config "C:\GC\private\event.json"
```

By default the launcher uses `configs\new-modpack.json` under the server
directory beside the batch file. If that file is absent, it looks for a single
JSON with a `game` object beside the batch file. An explicit `-Config` path
overrides either default. Keep your normal server settings
and always-on mods there. Pass a different mission path for the next launch.
Add `-PlanOnly` to preview the
scenario and merged mod list without downloading anything or starting a process.

**What to deploy.** On the authoring PC use **GC → Create Mission Data → Refresh
metadata only**. Generate portable metadata from the saved header on that PC:

```powershell
.\Start-GCMission.bat "C:\GC\MissionAddon\Missions\Operation.conf" -ExportMetadata "C:\GC\deploy\Operation.launch.json"
```

Copy that JSON to the server. It contains the scenario, world, required addon
IDs, coverage and header hash. Export again to the same path after a mission
update. You can also pass the native `.conf` directly when its adjacent
`.conf.meta` and owning `addon.gproj` are deployed in their original relative
layout. Windows PowerShell 5.1 reads native text with the included .NET helper.
The actual scenario, GC Lobby startup receipt code and content must be
in Workshop packages available to the server. Use metadata from that published
build. A mission with script-selected content can declare extra Workshop GUIDs
in its catalogue's `m_aRequiredAddonIds`. To require a specific release, add
`m_aRequiredAddonVersions` at the root of the native header with quoted
`GUID=version` entries. The portable export carries those entries in
`requiredAddonVersions`. Each entry also requires the addon by GUID. The
launcher rejects a conflicting server config pin and checks the selected local
or downloaded package version. Schema 1 captures fewer references
than schema 2. Inherited headers and missing snapshots require a self-contained
metadata export for this prototype.

**What happens on launch.** The script reads the base config afresh. It keeps
baseline mod entries, including version pins, and adds the mission owner,
author-declared requirements and captured references by GUID. An optional
baseline entry becomes required for that launch if the mission names it.
Unpinned versions follow Workshop resolution. Reforger receives `-config` with
one replaceable private runtime copy under `gc-server-profile` beside the base
config. The same profile and Workshop cache serve subsequent missions.
`-ServerExe`, `-ProfileDirectory` and `-AddonDownloadDirectory` override these
locations. Use a profile writable only by the server account because its runtime
config includes the server's private settings.

Workshop downloads the native dependencies. After startup the launcher checks
the nonce-bound GC receipt against the scenario, world and exact addon closure
read from the cache's downloaded projects. It checks configured version pins.
When the world's loose metadata is available in the mission addon, it also
checks that exact world owner. Otherwise it checks the world GUID and resource
path and requires a loaded world owner. Allow extra time for a cold download
with `-StartupTimeoutSeconds` (default 300). Keep the launcher running with
the server. Shut the server down normally before selecting the next mission.
After an interrupted launcher, check the server process before clearing an
orphaned `gc-launch.active` marker. Startup failures stop the child created by
this invocation.

This direct path runs independently of the website and Python. The existing
[website queue worker](server-worker.md) has its own deployment and configuration
contract. For an unpublished development fixture, use `-Local` with installed
`-Project` paths or `-AddonDirectory` roots. That mode uses `-server` and
`-addons` and applies only the base config's mod selection.

[Dedicated experiment and regression checks](../../development/rhs-load-only/README.md#direct-powershell-launch)

## Earlier source-scanning prototype

This is a **source-scanning experiment, not a production mission restart worker**.
It implements a recursive saved-resource graph, a mission metadata sidecar, a
guarded server-config generator, and an isolated Windows test launcher. It has
no MCP, Workbench process, GitHub, or website runtime dependency. Python 3.12 or
newer is required; no third-party Python packages are needed.

The target is the whole mission: parent world/terrain, every saved world layer,
prefab inheritance, nested objects, inventory, weapons, attachments, configured
random variants and recursively referenced resources. The prototype follows
GUID-qualified references in saved text assets; it does not just count placed
units or reuse the loaded-addon list. It includes the owning addons and their
native `.gproj` dependency closure, including resource references in project
configuration. Every selected mod has reasons and every
resource edge records its origin.

## Current coverage and limits

**Real GC missions are not ready to launch through this prototype.** The Arland
experiment was rejected, correctly, because base-game resources are packed and
GC Lobby has runtime scripts. No server was restarted or advertised during that
experiment. Successful automated launcher tests use a mocked executable; they
are not evidence of game startup or multiplayer compatibility.

- Source `.meta` files establish resource GUID ownership. GUIDs are preserved
  across moves; stale path hints are reported as warnings. Ambiguous definitions
  are blocked rather than assigning an arbitrary override winner.
- All `.layer` files under each visited world's `<world>_Layers` directory are
  traversed, including layers of a referenced parent world. All serialized
  variants are followed, not just whichever variant spawned in one test.
- Runtime script presence, custom script modules, packed addons, missing
  references, path-only references, and unsupported/binary resources make the
  report **incomplete**. There is no force-launch switch. Native implicit
  resource relationships, script defaults, class ownership and dynamic strings
  still need an engine-backed analysis/validation stage.
- `static-complete` means the supported saved-text traversal completed. It is
  **not** a guarantee that arbitrary scripted behavior or an engine-specific
  implicit reference has been discovered. The config/launch commands are only
  for controlled prototype fixtures with known coverage, not general missions.
- The scan is conservative, not a mathematical minimum: authored variants and
  native addon dependencies can include content unused during a particular run.
- Existing generated catalogue metadata is not dependency authority and is
  excluded from traversal. Existing author-declared `m_aRequiredAddonIds` adds
  requirements; it cannot subtract discovered or native dependencies.
- `.gproj` is dependency authority. Installed `ServerData.json` contributes the
  installed version only. Local evidence showed empty `revision.dependencies`
  even when the same addon's `.gproj` required other mods.
- The sidecar currently includes local source paths and SHA-256 checks. It is an
  authoring-machine experiment, not yet a portable published manifest. A server
  deployment needs a versioned build artifact with equivalent integrity checks
  against the published packages. It must not depend on the author's disk paths.

## Commands

System mods are an explicit baseline, independent of placed content. Repeat
`--core-mod GUID` on `scan` for each selected core system, and supply its project
with `--project` like other addons. For example, Adult Mortars is
`6318DCB19B389CC8` and GC Suppression is `684CE8AA3B1D6573`. The sidecar records
`coreAddonIds` and a `GC core system policy` reason; their native dependencies
are included even when no mission resource mentions them. Core selection does
not bypass missing-resource, script-coverage or stale-source checks.

The launch list is **explicit core systems + discovered mission content + the
native dependency closure of both**. See the [core-mod audit](core-mod-audit.md)
for the current collection's classification. Classification is an audit, not
an automatically enabled dependency list. `scan` has no implicit core defaults;
operators must pass the intended baseline explicitly in this prototype.

Run in PowerShell from this directory. Supply each relevant source project
explicitly, including terrain/equipment projects; duplicate addon identities
are rejected. Use a new output filename for each experiment. Files are never
silently replaced.

```powershell
python .\mission_launch.py scan `
  --project 'C:\Mods\Mission\addon.gproj' `
  --project 'C:\Mods\Terrain\addon.gproj' `
  --project 'C:\Mods\Equipment\addon.gproj' `
  --mission 'C:\Mods\Mission\Missions\Operation.conf' `
  --output 'C:\Mods\Mission\Missions\Operation.conf.launch.json'
```

The `.conf.launch.json` sidecar belongs beside its mission header and should
travel with the mission's source. It records scenario/world identity, candidate
mods and versions, resource edges, content hashes, file-set snapshots, warnings
and unresolved checks. Exit 0 means `static-complete`; 2 means the report was
written but is incomplete; 1 means an input/output error. The original header,
world, prefabs and project dependencies are never changed.

For a controlled, statically complete fixture with installed version metadata:

```powershell
python .\mission_launch.py config `
  --metadata 'C:\Mods\Mission\Missions\Operation.conf.launch.json' `
  --template 'C:\Server\base.json' --output 'C:\Server\experiment.json'
```

The template's server settings are retained. `game.scenarioId` and `game.mods`
are replaced, not appended; every selected mod is required and version-pinned.
Missing versions, incomplete scans, changed files, changed metadata sets, newly
added scripts and newly added/deleted world layers block config creation.
New packed archives also invalidate the saved scan.
Keep the template/output private if they contain server passwords.

The `launch` command takes those same arguments plus `--executable` and
`--profile`. It generates the config and starts that executable with separate
arguments, without a shell, using a **new** isolated profile. It waits for exit;
Ctrl+C terminates only its own child. Use a private server template and unused
ports for an experiment. The process is hidden on Windows and output goes to
`<profile>/launcher.log`; inspect the engine's own profile logs for actual
scenario readiness. Process creation alone is not success. This command does
not stop/restart an existing server, poll the website queue, or alter voting.

## GC Workshop

The standalone metadata-only collection is at
[gc-reforger-workshop](https://github.com/burn0ut7/gc-reforger-workshop) on `dev`, named **GC Workshop**. It retains
the prototype GUID `6B8E05204D634FA9`, uses GC Dev dependencies and is unpublished.
It is not a GC Lobby dependency. The scanner rejects both the old
GlobalConflictsWorkbench and new GlobalConflictsWorkshop identities as server
requirements, because a full authoring collection is not a minimal mission list.

The 2026-11-16 ownership migration moved equipment changes to Units and mission
content to Missions, leaving system integrations in Core. Core now selects ACE All in One Dev and
CSI instead of individual ACE entries. See
`../../development/dependency-migration` for preserved file hashes and validation,
and the collection README for native download/update behavior and release limits.

The experimental `collection` command can still generate a snapshot into a new
file from explicitly supplied projects; it does not modify the checked-in collection
or automatically choose which optional addons GC wants.

## Tests and experiment results

```powershell
python -m unittest -v test_mission_launch
```

Tests cover terrain and implicit layers, nested inventory/weapon/attachment
chains, inherited prefabs and random alternatives, unrelated addon exclusion,
transitive native requirements despite empty ServerData dependencies, cycles,
missing terrain/attachments, opaque and unqualified resources, overrides,
collection rejection, stale files/new layers/new scripts, template preservation,
and launcher argument/profile isolation.

On 2026-11-15, scanning GC Lobby's saved Arland scenario resolved 13 local
resources and produced 15 blocking checks: 13 references into packed base-game
data, one unsupported string-table format and one runtime-script coverage check.
Its candidate addon list contained
GC Lobby alone, but was **not certified complete**. No launch config was made.
The earlier generated Workbench collection contained 101 direct addon requirements (including the base game),
including the four GC Dev projects. The obsolete Server Admin Tools, Scenario
Reload Menu, Bacon Loadout Editor and Everything Arsenal dependencies have been
removed from Core Dev and the collection. Units/Missions declarations were unchanged at that time.
That historical GC Missions Dev native dependency closure contained 136 non-vanilla
addons, with all declarations found in local source/installed projects. That
confirms why launcher selection alone cannot yet shrink the current package.

Native research used the installed 1.9.0.37 Workbench declarations:
`ResourceDatabase.c`, `GameProject.c`, `BaseContainerTools.c`, and
`WorkbenchGameCommon/ResourceInfo.c`. Public ResourceDatabase search enumerates
resources; it does not expose a complete dependency-graph call. Container
introspection is a promising next experiment for packed prefabs, but worlds,
implicit resources, global script hooks and dynamic spawns must also be covered
before enabling unattended mission launches.
