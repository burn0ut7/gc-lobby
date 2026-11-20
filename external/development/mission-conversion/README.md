# GC Missions conversion

Run from the GC Lobby checkout with Python 3.9+ and Git. The converter reads committed objects from the Missions repository. Fetch the two branches first. Supply commit hashes to reproduce a particular comparison.

```powershell
git -C ../gc-reforger-missions fetch origin master dev
python external/development/mission-conversion/convert.py --missions ../gc-reforger-missions --source origin/master --dev origin/dev --report "$env:TEMP/gc-missions-comparison.json" --stage "$env:TEMP/gc-missions-presets"
```

Omit `--stage` to compare without generating files. Use a new staging directory when inputs change. Repeating the same generation verifies existing output. Copy generated resource/metadata pairs into a clean Missions Dev checkout at the same relative paths. The tool checks existing tracked Dev presets for differing content. Review those differences before replacing any edited preset.

## Coverage

The comparison indexes mission headers, including Scenario Framework subclasses, and every committed world. It follows source-local resource GUID references and each subscene's layer tree. The per-world record includes the source header, counts of serialized declarations, authored respawn and freeze overrides, source locations for briefing gaps, and path-level differences against the specified Dev commit. Declaration counts include grouped placement headers and inherited local resources. They are not runtime entity counts. Binary assets and external provider overrides, globally registered Enforce scripts and dynamically resolved names need separate inspection.

The first pass translates placed PS mission descriptions into mission-specific GC Lobby mode prefabs. Each preset inherits the GC Lobby template and holds the scenario's briefing definitions. The selected worlds still require actual mode placement and conversion of their other systems. A world with an unsupported description receives no preset. This prevents a partially parsed group or a missing audience restriction from being published as a complete briefing. Entries with duplicate or out-of-range display indices receive consecutive indices in stable source order after sorting by legacy order. This preserves relative ordering, but external references to legacy indices require review.

The Blood and Smoke uses the expanded notes newly added by legacy commit `9a681ab9`. Its parent contains the shorter notes. The converter resolves only the exact conflicted blob. Topdown and San Serriffe Busters share source world GUID `321510A1C6838890`. Their headers distinguish them by path. Retain both and resolve the GUID collision before registering both worlds together on Dev.

## Remaining behavior work

| Source behavior | GC Lobby comparison and pending work |
| --- | --- |
| Phase and mode ownership | Replace the legacy root once per world, preserving terrain AI, perception, faction overrides, weather, chat and radio ownership. Check lobby/roles → briefing → match → after-action report on authority and clients. |
| Groups and roles | Resolve Units Dev parents by GUID. Compare each placed group's playable members, descriptions, assigned callsign indices, custom units and original positions. A standalone playable character requires an explicit slot solution. |
| Icon markers and map shapes | Resolve inherited icon, highlight, quad, rotation, color, world size, label and phase/audience. Check visibility before slotting and as each side. Legacy outline widths are measured in metres while GC passive shape widths are in pixels. |
| Briefing audience | Legacy independent unaffiliated visibility can differ from visibility for all assigned factions. Record unsupported layouts, nested ownership and audience combinations per world. Verify ordering and content on the live UI. |
| Tickets and respawn | Audit finite and infinite budgets, per-death delays, waves, replacement prefabs and named destinations separately. Legacy PS schedules its serialized interval directly on the call queue. Do not infer units from the editor label or translate per-death timers into waves. |
| Framework events | Trace every flag producer, expression, action, delay and reactivation. Legacy per-instruction delays default to five seconds. GC event delays recheck conditions. Legacy player casualty flags use historical maximum concurrent living players and can clear; GC casualty conditions count deaths of the starting roster. |
| Victory and AAR | The legacy Core framework overlay sets `m_suppressGameEnd 1`. Resolve per-instance changes before translating a notification into an actual match ending. GC end actions start an adjustable countdown. |
| Special scenarios | Roulette and Commandos have mission-specific scripts and shared player-controller hooks on master. Port their actual behavior and isolate it to the intended mode before loading those scripts on Dev. Review the native Advance and Secure / Advance and Cooperate worlds independently. |

Sources for the legacy contracts include Reforger Lobby Community's `PS_MissionDescription`, `PS_MissionDescriptionHeadersListUI` and `PS_GameModeCoop`, and TilW Mission Framework's `TILW_Instructions.c`, `TILW_MissionFrameworkEntity.c` and `TILW_MapShapeComponent.c`. GC Lobby contracts are in `scripts/Game/Briefing`, `scripts/Game/Map` and `scripts/Game/MissionFramework`. The converter has no runtime dependency on the old framework.

A mission is ready for the launcher once its header resolves its converted world, assets and playable slots load, and the behavior checks above have recorded outcomes. Record unavailable features by mission. A preset or a successful resource load alone does not establish mission parity.

## Reproducing the Kunar pilot and marker review

Add `--include-race-day` to stage the Race Day – Kunar launcher header, world, and layers alongside its settings preset. The recipe checks the legacy rules blob before rewriting its events. The port uses the race motorbike from Units Dev commit `358ce612`. The playable world files are in Missions Dev commit `2a334cc5`.

Add `--stage-marker-candidates` for standalone icon layers. Inspect the `markerLayers` record per world. Each entry gives an icon count or the first unsupported placement, inherited prefab, or field. At the pinned source revision 53 layers containing 403 icons can be staged. These require resource, phase, faction, stacking, hover and visual checks on their owning world before publication. Other map shapes, nested markers and script-created markers require separate conversion.

## Workbench observations

On November 20, 2026, Workbench 1.9.0.37 loaded all 141 generated mode presets and read 1,413 GC Lobby briefing entries with unique display indices per preset. This checks resource structure. UI rendering and audience behavior still require testing in the owning missions.

Race Day – Kunar registered three civilian groups, 60 slots, one briefing page and three event rules. A local authenticated admin advanced from role selection through briefing and match to after-action report. The timed gate opened. A controlled teleport into the finish triggered the finish event and delayed reminder. No dedicated-server or remote-client run was recorded. A vehicle probe reported zero simulated wheels on the race motorbike. A driven finish, legacy countdown HUD, restriction zone behavior and player reconnect remain unverified. Workbench exited during cleanup of a separate vehicle probe. The race has no gameplay-parity signoff.

## Stock scenery and AI waypoints

Pass `--base-database "C:/Program Files (x86)/Steam/steamapps/common/Arma Reforger/addons/data/resourceDatabase.rdb"` to include stock placement layers in the comparison and staging output. Use the path under your game installation. The report records the database digest. Each prefab must match both its path and GUID in the installed game registry. The converter copies each eligible layer intact and skips layers with a source- or Dev-mission-owned prefab, unsupported declaration or field. Waypoint cycles retain their named targets, ordering, radii and positions. A different layer on Dev is recorded for review.

Missions Dev commit `b8615075` contains 46 such layers across 42 worlds with 211 scenery placements and 526 waypoints. Every published layer has the same Git blob ID as its source. World registration, AI group assignment, named targets in other layers, and effective mod overrides still need verification as each owning world is assembled.

## Playable group layers

Fetch `master` and `dev` in GC Units and pass `--units ../gc-reforger-units` alongside the Missions and stock database arguments. Pin `--units-source` and `--units-dev` to reproduce the recorded commits. The converter resolves source and destination resources by GUID, updates moved paths and confirms member playability through the legacy Units character ancestry. Complete supported group layers retain their instance names, transforms, formations, prefab slot ordering and append or replacement operators. Authored group descriptions follow the mapping used in Units Dev. Company, platoon and squad indices come from the placed component, its Units ancestors, and the PS component's zero defaults. PS_PlayableManager reads this component whenever present, including when the component has a serialized disabled flag.

Missions Dev commit `2314b0d4` contains 87 group layers across 74 worlds. The 462 placed groups account for 2,956 inherited and placed unit prefab entries. A separate comparison checked the generated group declarations, placement names, transforms, formations, list operators and ordered slot GUIDs against the original layers before publication. The staging rerun also compared the converted layers with the published Dev content. Runtime slot counts, equipment, display names and native faction callsign allocation still need checks in the owning worlds. External prefab overrides beyond the Units ancestor chain also require runtime verification. The per-world `groupLayers` records identify remaining cases including mixed layers, mission-owned characters, unknown playability and custom group components.
