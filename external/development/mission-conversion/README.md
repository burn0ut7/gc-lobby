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

The Blood and Smoke uses the expanded notes newly added by legacy commit `9a681ab9`. Its parent contains the shorter notes. The converter resolves only the exact conflicted blob. The launcher staging command resolves the shared Topdown and San Serriffe Busters world identity.

## Preparing launcher resources for assembly

```powershell
python external/development/mission-conversion/launchers.py --missions ../gc-reforger-missions --source origin/master --dev origin/dev --stage "$env:TEMP/gc-launchers" Missions/Delfer/Topdown/Topdown.conf
```

Supply the source header paths for the batch, or omit them to prepare the whole catalog. Each output contains the launcher header, its metadata, and the metadata for the world it loads. Keep these files in staging until the converted world and its layers are ready. The comparison records the resulting reference in `destinationWorld`.

Five headers have outdated paths and Operation Steel has a case difference. Topdown and San Serriffe Busters share the legacy GUID and both source metadata files name SerrifBusters. Busters keeps `321510A1C6838890` and Topdown receives `052737705D787FC7`. Loose Ends regains its historical launcher identity `2B73EAFD3FFE9D0B` from the metadata preceding its removal in commit `5489ae66`. Checks on the staged catalog resolved all 277 headers and found 554 distinct header and world identities. Map previews, external references and in-engine registration need verification with the assembled missions.

## Remaining behavior work

| Source behavior | GC Lobby comparison and pending work |
| --- | --- |
| Phase and mode ownership | Replace the legacy root once per world, preserving terrain AI, perception, faction overrides, weather, chat and radio ownership. Check lobby/roles → briefing → match → after-action report on authority and clients. |
| Groups and roles | Resolve Units Dev parents by GUID. Compare each placed group's playable members, descriptions, assigned callsign indices, custom units and original positions. A standalone playable character requires an explicit slot solution. |
| Icon markers and map shapes | Resolve inherited icon, highlight, quad, rotation, color, world size, label and phase/audience. Check visibility before slotting and as each side. Legacy outline widths are measured in metres while GC passive shape widths are in pixels. |
| Briefing audience | Legacy independent unaffiliated visibility can differ from visibility for all assigned factions. Record unsupported layouts, nested ownership and audience combinations per world. Verify ordering and content on the live UI. |
| Tickets and respawn | Audit finite faction budgets, per-death delays, waves, per-character replacement lists and named destinations separately. Legacy PS schedules its serialized interval directly on the call queue. Do not infer units from the editor label or translate per-death timers into waves. |
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

## Faction respawn tickets

The converter reads each placed mode�s faction table and records the source count, call-queue interval in milliseconds, wave setting and named destination. For supported positive budgets it adds a GC match-start event to the mission settings prefab. It accepts an existing briefing-only preset for this upgrade only when its text matches a fresh rendering. The event sets each faction�s opening balance once. Zero already matches the GC default. A missing faction key or out-of-range count prevents generation for the affected table.

Missions Dev commit `5e571911` carries 33 faction budgets in 28 mission presets. The comparison includes all 40 directly authored entries across 34 worlds. One entry has no faction key. One mission requests one billion tickets, above the GC action maximum of one million. The generated amounts and faction keys were compared with source values, and existing briefing content was checked before publication. Match-start execution in the owning worlds and automatic replacement behavior still require runtime checks.

Legacy PS captures a named destination�s transform when scheduling a respawn, falling back to the original spawn transform if lookup fails. GC named destinations resolve when the wave runs. PS deducts finite tickets before attempting the replacement. GC ticketed waves refund failures. A PS count of -1 switches to the character�s ordered replacement-prefab list, which can run out. The report records outstanding timing, destination and replacement work per world.

## Race Day – Ruha

Pass `--include-ruha` to stage the Ruha header, terrain subscene and seven layers. The two race recipes share their gate and finish rules. Ruha keeps its own 07:30 start time, course props, bike and civilian group placements, restriction zone, 44 named gate sections, transfer interaction and two faction-filtered insertion markers. Its filtered roadblock cleanup runs at match start. The source cleanup ran during world initialization. The port uses Ruha’s original finish position and keeps the 15-second reminder and administrator-controlled ending.

The source course has 123 polygon vertices. Edges 36 and 72 (zero-based) cross, which the GC AO validator rejects. The recipe retains every original vertex, reverses traversal of one lobe and adds four vertices within five centimetres of the crossing. This connects the two permitted lobes. The 127-point polygon passed a world-coordinate intersection check at engine float precision. Legacy and converted membership matched at 14,640 sampled positions. The course keeps the original 20-second grace period and vehicle exemption. Native map rendering, boundary behavior at the crossing, the transfer action, player reconnect and a driven finish require in-game comparison.

A local Workbench run from Missions Dev commit `a817ab02` registered three groups, 60 civilian slots, one briefing, two GC map markers and four event definitions. The AO accepted all 127 vertices. A player claimed a civilian slot during role selection, and the authenticated local admin advanced through briefing, match and after-action report. The gate was present before the timer and absent after 64 seconds. In a separate run the test moved the claimed character to terrain level within the finish area; the rule count rose from one to three after the finish and reminder interval. This exercised the finish logic independently of the motorbike. The server-side transfer action, both clients’ map views, actual roadblock removal, and driving still need separate checks. The loaded addon set also emitted missing legacy editable-prefab and vehicle-provider diagnostics.

## Timed framework notifications

The converter recognizes one-shot match events whose condition is an inverted, unnamed legacy literal and whose instructions are all notifications. It preserves the source title, content, audience and each instruction’s delay from the original trigger. Instructions sharing a delay stay together in source order. An omitted delay is five seconds. The GC preset schedules one unconditional match-start event per distinct delay. Existing opening ticket actions and mission briefings remain in the same settings preset.

The per-world `frameworkEvents` entries identify each source event by file and line. Unsupported conditions, pregame execution, mixed action lists, external event controls and combined faction/admin audiences need separate translation. GC popup stacking and lifetime, match-end cancellation and remote-client delivery still require comparison in play. Legacy source contracts are defined by TilW Mission Framework’s `TILW_MissionEvent`, `TILW_LiteralTerm` and `TILW_SendMessageInstruction`.

The local batch contains 174 notifications from 80 events in 76 worlds. A separate source/output comparison checked title, body, delivery restriction, faction list, delay and ordering within each scheduled group for every entry. Repeating generation against the local Dev commit verified the staged outputs.

## Play-area boundaries

The converter emits native GC AO entities for supported placements of the common legacy AO prefab. It preserves their names, transforms, vertices, faction and vehicle filters, grace periods, and supported map audiences and styles. It checks point counts, collapsed edges, backtracking, crossings and polygon area against the GC polygon limits. Generated sidecar layers end in `_GC_Boundaries.layer`. During world assembly, consume the source instances listed in `boundaryLayers` exactly once. The recorded line numbers are one-based and inclusive; for grouped entities they describe individual instances inside the group.

The local batch contains 166 AO placements in 140 layers across 136 worlds. Source/output checks compared all 2,433 authored vertices and point IDs, entity names, transforms and timer overrides. The comparison lists unsupported placements, including cover-map masks, zoom-dependent outlines, tilted shapes and nonrepresentable faction visibility. Remaining runtime checks include map style and pre-slot visibility, enrollment after possession or faction changes, vehicle exemptions, and simultaneous violations. Legacy references are TilW Mission Framework’s `TILW_AOLimitComponent` and `TILW_MapShapeComponent`. Native contracts are `GC_AOLimitEntity`, `GC_MapPolygon` and `GC_MapShapeComponent`.

Kunar's course layer also contained two TilW Mission Framework `PK_EntityDeleter` placements. The converter now replaces them with named centers and GC filtered deletion actions at match start. Source/output checks verified both 10-metre radii, exact positions, the metal gate and bar gate prefab filters, and all surrounding course placements. The earlier Kunar playtest preceded this change. The timing change from world initialization and the effects on both gates and adjacent objects still require a playtest. In Ruha's November 20 Workbench run the vehicle log reported a duplicate powertrain part name, an invalid differential configuration and a failed wheeled simulation on the placed race motorbikes.

## Hnevice playtest world

Pass `--include-hnevice-candidate` with `--units` and an external `--stage` directory. This assembles the launcher, Bystrany subscene, GC Lobby mode, briefing preset, groups and supported AO. The terrain comes directly from the Bystrany dependency because Core Dev lacks the legacy terrain wrapper. The staged map entity carries over the wrapper's two Bystrany map references. Hnevice's AI world, navmesh references, weather overrides and group transforms were checked against the source.

The group converter now translates an explicit legacy `m_bSetPlayable 1` to native `m_bPlayable 1` when the parent needs the override and the full roster passes the existing source and destination checks. Hnevice stages eight groups with 68 roster entries, 11 briefing pages and an eight-point AO. The candidate remains external. The comparison records the outstanding behavior and Workbench checks.

Hnevice now stages three native briefing-map icons. The objective and platoon prefab defaults come from the installed TilW Mission Framework assets. The Crash Site uses its inherited size of 50. The platoon icons use the inherited infantry quad and size of 95 with the authored stock outline imagesets. Both placed platoon icons inherit the public assigned-faction setting even though they have authored faction lists. The legacy PS_ManualMarker checks the public setting before its list, and checks unassigned visibility separately. GC Lobby now uses the existing replicated audience to express that combination. The crash icon includes unassigned viewers. Map rendering and the transition from unassigned to assigned still need Workbench comparison.

Hnevice stages both 80 percent casualty notifications. The opt-in GC population component uses native player spawn and deletion notifications and coalesces them into a recount after five seconds. It compares each faction's current living player count with its highest sampled count. The legacy reference is TilW Mission Framework's TILW_FactionPlayersKilledFlag. A one-shot GC trigger records threshold crossing. A separate event sends the authored popup five seconds after that trigger even if the count recovers. GC checks the sampled condition on its one-second script schedule and cancels pending messages when the match ends. The serialized conditions, actions and delays were checked against the source. In-engine compilation, spawn and deletion callbacks, live threshold timing and message delivery still need verification.

Hnevice's 120,000 ms PS_GameModeCoop freeze setting now stages a 120-second HUD countdown. The authored world and its legacy Core terrain wrapper contain no restriction zones. The GC display reads the replicated Match phase clock and leaves AO enforcement active. Admin staging and setup zones retain their existing controls. The first display tick follows the GC clock. Legacy PS decremented one second before broadcasting. In-game HUD timing, reconnect, early match end, and the legacy end-of-freeze chat and elapsed mission-time behavior still need comparison.

Hnevice now stages the passive four-point mask inherited from the legacy AO prefab. The enabled map component draws a 75 percent black fill outside the polygon, with no outline. The original transform and all four points are preserved. The GC map-shape audience sends it to unassigned viewers and the authored US and USSR faction keys. The placement has no AO enforcement. The comparison records the native candidate layer against its original placement. Workbench rendering and faction transitions still need a playtest.

Workbench 1.9.0.37 compiled and loaded the Hnevice world from local Missions Dev commit `79bb4264`. The native marker manager needs the map entity before the game mode in the default layer. With that order, the run registered eight groups, 68 slots, 11 briefing pages, four rules, three icons and two polygons. The local authenticated player claimed a slot and advanced through role selection, briefing, Match and after-action report. The countdown showed 112 seconds remaining roughly nine seconds into Match with staging boundary enforcement inactive. The run ended before the countdown expired and did not cross a casualty threshold. Workbench emitted missing inherited equipment and editable-prefab diagnostics. Map audience, mission objectives, actual equipment and multiplayer behavior still need comparison. Evidence is in Workbench session `logs_2026-11-20_11-57-35`.

Units Dev commit `541a3637` restores the missing resource metadata for the San Serriffe machine-gun group's existing parent prefab. The playable child resolves through that parent to its faction base. Workbench reopened Hnevice in session `logs_2026-11-20_12-02-20` and no longer reported the parent GUID or registration errors. The other missing item references in the prior run came from loaded PBK, QM and base catalogue configurations. The Argentina flag warning comes from a shared faction config referring to a legacy mission-owned asset. Actual issued equipment still needs an in-game comparison.

Units Dev commit `a7f14b11` restores the Argentina faction flag material from the legacy Top Malo House asset. It retains the material GUID and shader values. The faction config and editable filter use the existing Units Dev flag, base flag and icon textures and their canonical paths. The Units Dev texture blob differs from the legacy Missions blob. The material now uses the existing Dev texture under its original GUID. The conversion repeated successfully against the new Units commit, and the GUIDs and paths were checked against Units metadata and the installed stock resource registry. Visual comparison in Workbench is pending.
