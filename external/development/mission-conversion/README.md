# GC Missions conversion

Run from the GC Lobby checkout with Python 3.9+ and Git. The converter reads committed objects from the Missions repository. Fetch the two branches first. Supply commit hashes to reproduce a particular comparison.

```powershell
git -C ../gc-reforger-missions fetch origin master dev
python external/development/mission-conversion/convert.py --missions ../gc-reforger-missions --source origin/master --dev origin/dev --report "$env:TEMP/gc-missions-comparison.json" --stage "$env:TEMP/gc-missions-presets"
```

Omit `--stage` to compare without generating files. Use a new staging directory when inputs change. Repeating the same generation verifies existing output. Copy generated resource/metadata pairs into a clean Missions Dev checkout at the same relative paths. The tool checks existing tracked Dev presets for differing content. Review those differences before replacing any edited preset.


## Batch migration and file review

Use the batch workflow for the catalog. The same rules process every legacy launcher. They assemble complete drafts when all placements and behavior in a world match supported rules. Unrecognized constructs stay in the inventory with their file, line where available and conversion rule. The inventory groups blockers by the number of worlds affected, so a shared rule can unblock several missions on the next run.

Run the planner from GC Lobby with a clean Missions Dev checkout. Use a fresh output directory when the commits or conversion rules change.

    $repo = '../gc-reforger-missions'
    $tools = 'external/development/mission-conversion'
    $output = "$env:TEMP/gc-missions-plan"
    $base = 'C:/Program Files (x86)/Steam/steamapps/common/Arma Reforger/addons/data/resourceDatabase.rdb'
    $workshop = "$env:USERPROFILE/Documents/My Games/ArmaReforger/addons"
    python "$tools/plan_batch.py" --missions $repo --units ../gc-reforger-units --dependency ../gc-reforger-core --base-database $base --workshop-addons $workshop --output $output

Run the full file audit for every batch. Use the specific blocker table to choose shared conversion rules by how many worlds they can unblock. Install every draft that passes the roster, resource and committed-file checks, then push roughly ten missions together. After the batch lands on Dev, run one representative Workbench MCP scenario per changed runtime behavior. Use scenarios that can cover multiple changes together. Additional sampling should address a reported discrepancy or a distinct behavior gap.

Open `plan.md` for the suggested batch of up to ten worlds and the reasons any generated drafts were held. All launchers pointing at a drafted world travel together. `plan.json` gives source examples for each blocked conversion rule and ranks rules by the number of worlds where that rule is the only reported blocker, then by total worlds affected. Some downstream checks run only when a draft is complete. Add shared rules for those constructs and rerun the catalog.

`inventory.json` records conversion decisions and source locations. `review.md` and `review.json` compare every legacy launcher with the Dev snapshot overlaid with generated drafts. They report unchanged, changed, source-only and Dev-only files and locate declarations for modes, briefings, rosters, markers and boundaries, respawns, events, weather and vehicles. They also identify inconsistent launcher or resource references and name each file that uses an unresolved resource. Run `review.py` with repeatable `--mission` arguments and `--patch` for a focused diff. Omit `--draft-stage` to compare committed branches.

After reviewing a batch, pass its launcher paths to the installer. Use `--dry-run` to validate paths, hashes, collisions and resource references before installing. Repeat without `--dry-run` to install.

    $plan = Get-Content -Raw "$output/plan.json" | ConvertFrom-Json
    $selection = @($plan.suggestedBatch.launchers | ForEach-Object { '--mission'; $_ })
    if ($selection.Count -eq 0) { throw 'No suggested drafts to install' }
    python "$tools/install_batch.py" --missions $repo --inventory "$output/inventory.json" --stage "$output/drafts" --dependency . --dependency ../gc-reforger-units --dependency ../gc-reforger-core --base-database $base --workshop-addons $workshop @selection --dry-run

The installer leaves Git commits and pushes to the delivery batch. It refuses to overwrite differing files. The review resolves file identities and records supported conversions. It cannot establish runtime behavior for new framework features, historical terrain revisions or inherited behavior that the file rules cannot account for. Exercise a shared feature in Workbench when needed and apply that evidence to the relevant conversion rule across missions.

The AI reinforcement rule uses the existing `GC_AIGroupSpawn` action. It preserves the instruction delay, named placement, optional entity name and order of appended waypoints. The planner verifies the stock group, its group parents and its character roster against the installed registrations and checks for overrides in the Missions and Units snapshots. A completed world draft must also have one placed anchor and one placed AI waypoint for each referenced name. Custom group prefabs and stock groups with overridden roster members stay in the file review until their ancestry and loadouts are verified. The legacy behavior was reviewed in `TILW_Instructions.c` and `TILW_CrewConfig.c` from the published TilW Mission Framework package through the Reforger MCP archive tools.

The event converter uses the placed-vehicle crew rule for structured event spawns. It preserves ordered stations, named waypoints, delays and the legacy gunner options. Custom or unavailable vehicle and character references still require review. The event notification rule preserves headings when the legacy hint supplied only a title. It still reports instructions with neither a heading nor a body. The published TilW hint handler passes both fields independently to the base-game hint manager. Workbench MCP compiled the native title-only notification change with zero errors in session `logs_2026-11-20_20-05-55`. Client display and audience delivery are part of the representative runtime check.

The batch converter retains rotated and uniformly scaled map shapes and validates the polygon projected onto the world XZ plane. An auxiliary outline becomes a separate native map shape with the original colour, audience and line width in metres. Its width is evaluated as the larger of one pixel and line width times zoom. The legacy TilW AO, map shape and cover components all use `CoordToParent`. The projection follows the base-game `AnglesToMatrix` example. These rules apply to new drafts. Earlier converted cover layers with screen-width outlines and client rendering still need review.

The mode rule preserves verified legacy faction, radio, chat, preview and editor child settings using the corresponding native Lobby entities. The published legacy Lobby placement and base-game preview prefab provide the reference defaults. Framework transforms can be omitted when every event was converted because the legacy framework does not use its own transform. `m_safeStartTime` was inactive in the published GC Core character controller. Its only call to `HandleSafestart` is commented out, and the legacy mission scripts contain no callers. The mode keeps the independent staging countdown. Hall Boss and Hall Boss Mines retain 75 advertised players and all 47 authored slots each. Capacity approvals check the original header and both roster layer hashes.

The ten Roulette worlds share the authored `GC_R_Manager` and map-specific scenario choices. Their briefing and mode blockers cover a shared runtime migration. The legacy manager rerolls seeded scenarios, regenerates groups, spawns descriptions and map overlays, and builds capture, casualty and time-limit outcomes through the old framework. Keep its map-specific configuration and authored command descriptions with the runtime port. Verify that the shared generator publishes the roster before role selection, replaces only its own state on reroll, and resolves outcomes in Match. The old sphere queries and global player controller hook require supported replacements.

Startup entity cleanup remains a separate behavior gap across worlds with `PK_EntityDeleter`. The published TilW placement includes roots intersecting the volume, optional visible-only or prefab filtering and protection exclusions. The GC radius action uses entity origins, supports building interior expansion and protects its own anchor and game mode. Scouts Out is an unfiltered startup cleanup. Its exact removal set needs evidence before reuse of the current action.

When a legacy placement authors trench progress settings, the converter restores the published Lobby building manager settings and uses an opt-in construction outline registry. It scales the native build threshold for new layouts using the authored preparation duration and multipliers. Missing authored values use the published GC Core defaults of 300 seconds, 2 during preparation and 10 afterwards. The legacy mode and its threshold override were inspected in `GCC_PS_GameModeCoop_M.c` and `GCC_SCR_CampaignBuildingLayoutComponent_M.c`. GC Lobby uses the native outline registry interface. Workbench MCP compiled the subclass with zero errors in session `logs_2026-11-20_20-15-48`. Live inspection of Villeneuf found a second building manager from the inherited ACE Trenches base-mode overlay. The converter now uses the inherited manager and outline slot IDs. The corrected local Villeneuf layer loaded with one building manager in Workbench MCP session `logs_2026-11-20_20-33-16`. Placement, threshold changes and the implied defaults on missions without authored overrides still need review.

The batch weather rule preserves base-game weather and starting-time settings. It applies active legacy date settings with their original defaults and uses the existing mode-owned environment component for active fog, rain and wind overrides. It drops legacy values whose switches are off. Geographic coordinates, timezone overrides and simultaneous native and legacy fog or wind overrides remain held for review. The legacy lobby's implicit clock reset at Match start remains a separate behavior gap. These decisions follow the published TilW Mission Framework weather extension and the installed base-game handler, inspected through the Reforger MCP archive tools.

The briefing reader follows static child descriptions inside a standard description placement. The published legacy GC Lobby description registers each page independently with the description manager. The generated GC Lobby preset retains the authored text and audience for every page, including children. Pages under other entity types, prefab-owned pages, and scripts that manage briefing lifetimes require separate review. The batch flags any converted delete instruction that targets a named briefing.

The marker rule translates the legacy hidden-state list to GC Lobby phases. Simple thresholds retain their existing representation. Mixed lists use the marker's explicit hidden phases, which are carried in the replicated marker snapshot and checked alongside faction visibility. Workbench MCP compiled the change with zero errors in session `logs_2026-11-20_19-12-27`. File checks covered all 32 Lobby phase combinations, including both legacy numeric values observed in the catalog. Live client rendering and late-join behavior remain unverified. The source phase check was reviewed in the published PS Core manual marker, and the source state order in the published GC Lobby mode. Legacy cutscene and debriefing screens have no separate phase in this conversion.

The batch marker rule preserves the legacy draw order at rest and on hover. Legacy defaults are zero at rest and 10,000 on hover. When `m_bringToFront` is off, hovering keeps the resting order. The migration checks the authored integer range and carries both settings in the replicated marker snapshot. The published PS Core widget and the TilW Mission Framework widget extension supplied the reference behavior. Workbench MCP compiled the change with zero errors in session `logs_2026-11-20_19-16-13`. Rendered overlap and hover behavior still need a live client check.

The marker converter keeps child icons attached to their parent marker. It preserves each local transform, appearance and audience. The published PS Core marker and GC Lobby marker both read world position and yaw. Nested non-marker entities, including legacy map shapes, still require a conversion rule. Zavarak Farmland preserves seven top-level icons and two child icons and is the first candidate for a representative client check of marker nesting. Its BTR-70 references retain their resource IDs. The wreck path follows the base-game database, and the crewed variant has identical prefab content in the Units source and Dev snapshots.

For existing batch-converted worlds, the planner can stage a draw-order repair when the original conversion reproduces the entire Dev marker layer at the same path. The installer verifies the original layer hash and requires `--replace-reviewed`. Six layers matched and received 28 marker corrections in Missions Dev commit `4ff10132`. The dedicated conversion recipes retain their original output.

The batch mode rule preserves explicitly disabled JIP teleport and the legacy opening delay. Despite its name, `m_denyJIPTime` denied requests before the configured time. The rule uses GC Lobby's Match-relative opening time and retains the legacy 60-second floor. GC Lobby's existing connection eligibility, destination checks and single-offer policy still apply. The mode rule also accepts faction respawn tables containing only zero-ticket entries when the table parses cleanly. Funded tables still require scheduling, destination and replacement behavior review. The published GC Core JIP handler and legacy GC Lobby respawn configuration supplied the reference. Workbench MCP compiled the updated JIP window with zero errors in session `logs_2026-11-20_19-23-11`. Remote request timing and delivery remain unverified.

The group rule retains native movement settings, simple editable-group names, preview images and faction labels, member limits, danger range and empty-player deletion settings. It discards the standard legacy callsign assigner on verified AI-only groups. The published legacy GC Lobby uses that component only while registering a playable slot. The base-game Group prefab supplied the native movement and editable-group component identities. Custom UI variants, per-slot color and icon overrides and legacy role prefabs still require review. The batch run reduced group blockers from 359 to 338 and generated four additional standalone roster layers for review. The existing complete drafts remained byte-identical.

The batch reviewer and installer resolve resource references against the Dev addon's declared dependency graph. They follow project GUIDs transitively and prefer each supplied Git checkout over a Workshop copy of the same addon. The report lists unavailable dependency IDs and marks registrations from other installed addons as lookup leads. The archived platoon-leader and medic wrappers needed by two retired Motor Rifle roles currently resolve only in the legacy GC Lobby package. The active Dev provider check correctly rejects those wrappers and accepts the native platoon-leader prefab. The archived wrappers supply the native parent identities for the following recovery.

The Motor Rifle recovery stages the Deputy Platoon Leader and Medic from GC Units commit `cda6d969`, including the historical Platoon Leader parent of the deputy under a separate ID. Both playable roles keep their original GUIDs and equipment overrides. Their PS wrapper parents were checked against the published legacy GC Lobby package and replaced with the native stock parents. The installer checks every direct resource reference against the declared Units Dev graph. Run `restore_units_roles.py` with `--units`, `--base-database`, `--workshop-addons`, `--dependency` for the Lobby and Core checkouts, and an external `--stage`. The batch converter accepts the restored roles after reproducing and comparing all three committed prefab pairs.

Units Dev commit `8549aa91` supplies the restored roles. Missions Dev commit `7d4eb062` carries the 74-slot James mission. The file comparison preserved the order of its 47 explicit roster entries and the shared converter verified 27 inherited entries. The two vehicle paths and two vegetation paths changed to their current registered locations with the original GUIDs. The draft, registry check and committed files matched. Live slot selection and inherited stock equipment remain unverified.

The damage flag rule replaces supported placed legacy damage callbacks with one-shot GC mission flags conditioned on destruction of the main damage area. It verifies the native prefab identity, retains the original placements and health settings, and assigns a stable name to each unnamed target. Additional producers, script consumers, custom initial health, named damage areas and continuous state changes remain in review. The reference behavior comes from the published TilW damage flag component and the base-game destructible crate ancestry.

Makeup is committed to Missions Dev as `4727175e`. The draft preserved all 82 roster slots, both caches and all authored health and placement values. Both individual cache notices, the combined cache notice and the two casualty notices retain the source five-second delay. Resource checks passed against the Dev dependency graph. Live damage timing and staging behavior still need verification. Map cover framing follows native map opens, and the auxiliary outline uses screen width.

The batch rule now applies waypoint replacements after a positive one-shot capture, using the same named group and waypoint checks as Match-start assignments. A top-level blank positive literal becomes a disabled event with its action and delay preserved. The legacy framework ignores writes to the empty flag, so the original event could not fire. Blank inverted Match-start notifications keep their existing scheduling.

Missions Dev commit `d26e0433` ports Le Moule Approach Redux with 62 slots. The file review verified both capture spheres and the three five-second waypoint replacements to the sawmill. The 2,825-second timeout notice remains active. The unused casualty notice stays dormant because its source condition has no flag. Live capture timing and waypoint execution remain unverified.

The placed crew rule accepts verified RHS vehicle and crew identities and carries the legacy turret dismount setting into the existing GC gunner policy. It keeps compartment iteration and explicit crew order. It still holds custom crew groups, waypoint orders and unverified vehicle variants. The published TilW crew configuration supplied the original compartment checks and seating behavior. Workbench MCP compiled the change with zero errors in session `logs_2026-11-20_19-49-01`.

Missions Dev commit `7b650e52` ports JEFRMCWAC with 28 slots. File review verified the two original BRDM-2 placements, six crew entries, the stay-seated settings and the unchanged native fog entity. Live crew behavior and AI casualty timing remain unverified.

The batch spawner rule applies to placed spawners with verified prefab identities, an authored transform and Match-only behavior. It keeps the delay of each spawn and the waypoint order of supported AI groups. A positive one-shot capture or a verified mission flag can start the schedule. Each accepted trigger is remembered for its later spawns, as in the published TilW spawner. The rule checks that the generated or retained anchor, requested names and destination waypoints resolve uniquely. Pregame spawns, multiple named spawns whose names depend on spawn success, unverified prefab ancestry and complex crew configurations remain in review.

The catalog recognized 22 placed spawners across three worlds. The Workshop registration also verified the original M113 prefab in Landay Haul, clearing its placed-crew blocker. Its reinforcement spawners still need multiple crew stations and the separate gunner-group behavior. The authorized Development checkouts are the basis for these checks.

Ordered crew stations now preserve groups across multiple seat selections. Legacy waypoint crews can keep each turret gunner in a separate group and apply the ordered route after the configured delay to remaining crew. The placed spawner rule uses the same native framework as other GC crewed spawns. Workbench MCP compiled the feature with zero errors in session `logs_2026-11-20_19-58-02`.

Landay Haul is in Missions Dev commit `e4187ad9`. The conversion preserves the legacy launcher capacity of 60 and all 62 ordered role entries across nine groups. File review confirmed six crewed reinforcement spawns with two stations each, four AI squad spawns, their capture gates and delayed routes. Live seating, group membership and waypoint timing remain for the batch-level runtime check.

Desert Heat is the first installed batch draft in Missions Dev commit a07bcbdb. The installer verified every referenced GUID and path against the draft files, current addon checkouts and installed resource databases. Its existing boundary was reused and only the missing boundary was added. Two runs with different Python hash seeds reproduced the draft and inventory byte-for-byte. File review covers the full 277-launcher catalog.

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
| Briefing audience | Preserve unassigned, listed and all assigned audiences independently. Record unsupported layouts and nested ownership per world. Verify ordering, content and audience on the live UI. |
| Tickets and respawn | Audit finite faction budgets, per-death delays, waves, per-character replacement lists and named destinations separately. Legacy PS schedules its serialized interval directly on the call queue. Do not infer units from the editor label or translate per-death timers into waves. |
| Framework events | Trace every flag producer, expression, action, delay and reactivation. Legacy per-instruction delays default to five seconds. GC event delays recheck conditions. Legacy player casualty flags use historical maximum concurrent living players and can clear; GC casualty conditions count deaths of the starting roster. |
| Victory and AAR | The legacy Core framework overlay sets `m_suppressGameEnd 1`. Resolve per-instance changes before translating a notification into an actual match ending. GC end actions start an adjustable countdown. |
| Special scenarios | Roulette and Commandos have mission-specific scripts and shared player-controller hooks on master. Port their actual behavior and isolate it to the intended mode before loading those scripts on Dev. Review the native Advance and Secure / Advance and Cooperate worlds independently. |

Sources for the legacy contracts include Reforger Lobby Community's `PS_MissionDescription`, `PS_MissionDescriptionHeadersListUI` and `PS_GameModeCoop`, and TilW Mission Framework's `TILW_Instructions.c`, `TILW_MissionFrameworkEntity.c` and `TILW_MapShapeComponent.c`. GC Lobby contracts are in `scripts/Game/Briefing`, `scripts/Game/Map` and `scripts/Game/MissionFramework`. The converter has no runtime dependency on the old framework.

Review each launcher, world, roster and behavior against the legacy files before delivery. Record unsupported behavior by mission and use representative live checks for new shared runtime behavior.

Keep mission commits local while preparing and verifying the batch. Push substantial batches of roughly ten converted missions together with their required GC Lobby and Units changes. Count mission worlds in the batch and record the remaining behavior gaps for each one.

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

## Gwotline Bling mode placement

Pass `--include-gwotline-mode` with an external `--stage` directory to generate the reviewed mode placement for assembly. It links the existing briefing preset, retains the authored weather and position, and stages a 300-second display. Both 90 percent faction loss notifications use the shared peak population observer and preserve their original five-second delays. Hnevice's default layer remained byte-identical after extracting that conversion. The OPFOR layer now parses its serialized slot append operator and reports the RPG member's unresolved legacy playability. The 3,900-second event lacks a serialized condition and needs a legacy runtime comparison. Custom faction callsigns, staging behavior, both starting rosters and AO settings also remain open. The candidate is staged externally for assembly and has no Workbench signoff.

With `--units`, the Gwotline staging command also generates two roster layers. It stages seven BLUFOR groups with 39 prefab entries and two OPFOR groups with 14. All nine start transforms and the seven BLUFOR descriptions were checked. The Ranger HQ omits its trailing empty entry after Workbench registered it as an unusable slot. For reviewed groups with two PS callsign components, the converter uses the first serialized component and inherits its fields by component ID. The legacy Playable Manager uses the engine's first-component lookup, so the assigned callsigns still need a runtime comparison. The report lists the remaining floodlight placements and the OPFOR machine-gun group. Its two appended characters lack legacy playable components and require a mixed player and AI group port.

The Gwotline command now stages the five-point AO with its original grace period and faction key. The public outline uses the authored GC_MUC key for tint and falls back to black if the faction is unavailable. No GC_MUC faction definition was found in the inspected Missions and Units source snapshots or Core Dev. The key remains the enforcement filter, so the converter does not substitute GC_MUJ. Legacy TilW AO initialization returns before drawing or enforcement for the other two placements, which contain zero and two points. The report records both as inert. Checks compared the five emitted point IDs and positions, transform, filter and outline settings. Runtime audience and faction resolution still need confirmation.

Pass `--include-gwotline-candidate` with `--units`, `--base-database` and a new external `--stage` directory to assemble the Gwotline launcher and Kunar subscene. The output carries the original AI-world placement, seven mission layers and one GC mode. It preserves 62 stock scene placements with their authored light and physics overrides. Both start markers keep the original coordinates, quads, 60.5-pixel size and public audience. Source/output checks compared the full stock placement blocks and both markers. The empty vehicles layer needs no placements. The initial candidate lacked the mixed player and AI machine-gun group. Equipment and gameplay parity remain open.

Workbench 1.9.0.37 loaded the candidate now on local Missions Dev at `e4f6bc89`. The native roster registered nine groups and 53 slots after the trailing placeholder was removed. It loaded 12 briefing entries, four rules, two icons and one polygon. The local authenticated player claimed a slot and advanced through Role Selection, Briefing, Match and after-action report. The countdown showed 292 seconds at the Match probe and boundary enforcement was inactive. The last run used the current Kunar AI-world prefab GUID from the installed resource database, removing the earlier inherited-name diagnostic. Evidence is in `logs_2026-11-20_12-14-11`. The run exercised startup. It did not exercise triggered victory messages, original callsigns, dedicated or remote play, or the missing machine-gun group.

Local Missions Dev commit `d76d3e6d` adds the legacy RHS_USAF callsign table to Gwotline. The world placement overrides the inherited faction manager by its explicit entity ID and appends the faction record by its existing RHS container ID. This preserves the single native manager. Workbench session `logs_2026-11-20_12-20-12` confirmed the Charger and Spear names, HQ suffix 6, and the original group format. It also confirmed a slot claim and progression through after-action report. Seven RHS groups received native callsigns. Two Ranger groups displayed Charger 2 and Charger 3 from the first serialized callsign component while their descriptions remained Charger 1'2 and Charger 1'3. The effective component ordering in the legacy session still needs comparison.

Local Missions Dev commit `ee444dbd` restores Gwotline's four machine-gun group player slots and its two support AI. The four roles use the original playable parent and displayed callsign indices. The two other characters lacked a PS playable component. They now spawn in a separate native non-playable group with the same authored position, heading and disabled formation. Legacy PS created a separate player group linked to its original bots group, so the old command routing between those groups still needs comparison. Workbench session `logs_2026-11-20_12-26-44` registered ten playable groups and 57 slots. The support group spawned the two RPG roles as AI in Match and registered zero player slots. A local player claimed a slot and advanced through after-action report. AI formation offsets, linked orders and multiplayer parity remain open.

Missions Dev commit `2593cc48` stages the independent map fills for Breach and Clear and WetFeet. Three AO placements show the fill to unassigned viewers while the outline remains restricted to the authored faction. The converter now emits a passive fill and an AO outline for those placements. Each pair preserves the same transform, exact point IDs and positions, and faction list. The two changed layers were independently checked and generation repeated against the Dev commit. In-game map visibility and drawing still need comparison. For Breach and Clear the mission-owned No ETool character prefabs, remaining markers, restriction zone, fixtures, and the two loss conditions still need conversion or review.

## Breach and Clear loadouts

Pass `--include-breach-loadouts` with `--units`, `--base-database` and external staging. Local Missions Dev commit `fb125c6c` contains all six mission-owned No ETool variants and their metadata. The converter preserves the inventory override and ordered resource IDs in each prefab, including both field dressings. It resolves the playable character parent to its current Units Dev path and registers each variant at its actual Missions path. The Senior Rifleman parent has no PS playable component in its legacy Units chain, even with the source group override. The two roster entries using this variant therefore need separate review during group assembly. Workbench verification must establish issued equipment and ETool exclusion after inheritance and confirm the roles offered by the assembled world.

Local Missions Dev commit `471ffbb8` adds the Breach and Clear starting roster. The staging command verified seven playable groups with 48 prefab entries, including eight mission-owned No ETool slots. Both Senior Riflemen are staged as native AI at the respective source group starts with their authored loadout and formation setting. This follows the legacy component eligibility from Units master. Linked player and AI orders, actual formation offsets, item inheritance, and callsign allocation need comparison in the assembled world.

Pass `--include-breach-candidate` to assemble the Breach and Clear launcher and Zarichne world alongside its roster and settings. Local Missions Dev commit `97bec59d` preserves the fixture and vehicle placements, authored weather, and mission-owned smoke effect. It translates seven map icons and retains the native restriction-zone prefab. The GC timer and countdown use the source duration of 60 seconds. Workbench 1.9.0.37 registered seven groups, 48 slots, 11 briefing pages, one rule, seven icons and three polygons. A local player claimed a slot and reached Match. The native restriction manager had one zone at the first Match probe and zero after the release event. The named entity was gone and the player advanced to after-action report. The tested session is `logs_2026-11-20_12-40-59`.

The legacy Core asset sets `m_suppressGameEnd 1` on the framework prefab used by this mission. Its two End Game instructions thus produce notifications in the legacy Core setup. Their casualty conditions and announcements still need conversion. The local run did not compare the restriction warning HUD, both sides' boundary behavior, issued equipment, or multiplayer behavior.

Local Missions Dev commit `ce3dc8a1` restores the announcement for RHS_AFRF player losses. The threshold is 100 percent of the sampled peak. The rule latches the result and sends the global MISSION END notice ten seconds later while Match stays active. The GC popup uses an English faction victory line. The legacy hint assembled that line from the localized faction and result strings. The observer now samples five seconds after native Match start because GC Lobby can give players a prepared character without a spawn notification. It also schedules a sample after a confirmed player death in case the body is retained. The Workbench run in `logs_2026-11-20_12-45-57` crossed the threshold with no RHS_AFRF player present. A local player received the two-line notice in Match and advanced to after-action report manually. An occupied RHS_AFRF side, recovery, and remote delivery remain untested.

Local Missions Dev commit `f721dfd8` restores the 90 percent RHS_USAF AI loss announcement. A component placed in the Breach world samples the native living AI registry each second in Match and retains confirmed deaths in the denominator. The rule latches once and sends the global English faction victory notice ten seconds later. Workbench session `logs_2026-11-20_12-54-42` registered ten AI spawned during Match, then five living AI with zero deaths after a group despawn. After the remaining five AI died, the rule fired once and the player received the notice while Match was still active. The local player advanced to after-action report. The sampled current faction can differ from the legacy initialization-time faction if an AI changes sides. Possession changes, a mixed partial loss near 90 percent, localization and remote delivery still need comparison.

## Briefing audiences

The converter maps the legacy faction list, any-faction flag and unassigned visibility flag to GC Lobby's briefing audience. At the pinned source revision this increased the staging report from 141 to 215 briefing presets. It preserves all 12 WetFeet pages, including the Summary shown only before faction assignment. The generated preset remains in external staging pending the world conversion.

Workbench session `logs_2026-11-20_13-01-54` exercised the GC Lobby manager's briefing update path in Breach and Clear with unassigned, listed and other faction viewers. Unassigned-only, listed plus unassigned, assigned-only, hidden and both existing default cases passed. Toggling an unassigned-only briefing off and on preserved its audience. Converter checks covered all eight legacy flag and list combinations and repeated output. Remote replication, join in progress and WetFeet's in-game briefing still need checks.

## WetFeet capture and outcomes

Pass `--include-wetfeet-mode` to stage WetFeet's authored weather, timed capture area and victory rules. The hamlet uses a 93.2-metre spherical area and 360 seconds of exclusive British player presence. The first Match sample discovers the initial state without announcing it. An interruption removes one count interval and holds the remaining progress, matching the inspected legacy trigger code. The converter retains the three authored capture status messages and their global audience.

The rules use the authored 90 percent Separatist and 80 percent British player-loss thresholds. Each outcome schedules its two faction-specific messages and disables the other two triggers after five seconds. An outcome already triggered keeps its scheduled actions. The authored rules send notifications and leave the match active.

Workbench session `logs_2026-11-20_13-09-34` loaded the generated mode in an external fixture on WetFeet's Ruha terrain. It registered 12 briefings, a radius of 93.2 metres, and six compiled rules. A deterministic engine check applied ten seconds of capture, interrupted it, confirmed nine seconds remained through repeated empty checks, and completed the remaining 351 seconds. In a local match with a British fixture slot and an empty Separatist side, the Separatist loss rule fired and the British player received the original multi-line message before manually advancing to after-action report. Full live capture, concurrent outcomes, remote delivery, and the actual roster and scenery still need verification. The rules are staged externally pending assembly of the WetFeet world.

## WetFeet starting rosters

Pass `--include-wetfeet-rosters` with `--units` to stage the British and Separatist placement layers. They retain the original character resource GUIDs, group positions, headings, formations and callsign indices. The original `m_bSetPlayable` enables the legacy `PS_PlayableComponent` on spawned characters. The installed British Forces `Character_UK_Base.et` inherits the base character that receives that component from the legacy GC Lobby overlay. The Separatist drone operator also inherits that component and its group's force-playable setting. The Dev placement uses the existing native playable group prefabs and the same authored character variants.

Workbench session `logs_2026-11-20_13-15-08` registered all eight groups and 56 slots on the Ruha fixture. British groups registered 7, 8, 8 and 8. Separatist groups registered 6, 8, 8 and 3. The local player claimed a slot and advanced to Briefing. The legacy launcher advertises 57 players. The eight authored rosters total 56. The launcher count was corrected during WetFeet assembly. The report records the equipment-box resource references for separate verification of the stored items and their addon providers. Issued equipment, displayed callsign text and remote slotting still need comparison.

## WetFeet world assembly

Pass `--include-wetfeet-candidate` with `--stage`, `--units`, `--base-database` and `--workshop-addons` to reproduce the launcher and world in local Missions Dev commit `db7ec956`. The Workshop directory must contain the installed resource databases for the five providers listed in `sceneCandidate`. The Dev dependency chain already includes them. The port retains all original scenery and equipment placements. Two vegetation paths were updated to the current stock locations for the same GUIDs. The existing Railroad Reactions wire prefab is shared by reference, and its metadata now names the committed path. The launcher advertises the 56 slots verified in play.

Reforger MCP opened the assembled world in Workbench session `logs_2026-11-20_13-22-50`. It registered eight groups, 56 slots, 12 briefings, nine map icons, five shapes and seven rules. The named vehicles and both equipment boxes instantiated. The British and Separatist boxes had five and 14 occupied storage slots respectively. The British launcher configuration has no authored slot count. Exact contents and inventory interactions still need comparison. A local player claimed a position and advanced through Briefing, Match and after-action report. The tree-cleanup event fired in Match. Actual deletion results, nearby props and timing against the legacy world-init cleanup still need checking.

The BRDM remains placed by its crew's starting position. The legacy lobby offered a vehicle preview under BRDMCrew and removed locked vehicles at match start. Those lobby functions need a follow-up. Other open comparisons include live contested capture, equipment and vehicle operation, remote notifications, both sides' map audiences, and the legacy zoom-dependent overlay width. The native overlay uses a three-pixel outline.

## Outcome announcements and Industrial Metal

Pass `--include-outcomes` to audit legacy worlds containing end-game instructions and stage the supported rules as `_GC_OutcomeRules.inc` fragments for assembly. The source Core archive `GlobalConflictsCore_623555110E2B2CA0` overrides `Prefabs/MP/TILW_MissionFramework.et` with `m_suppressGameEnd 1`. The converter preserves independent instruction delays for unconditional Match-start and positive peak-player-loss events. It flags explicit real-end overrides, unresolved flags, compound expressions and event-control instructions for review. At the pinned source revision, 129 events across 61 worlds can be staged. The fragments are assembly inputs and remain outside the addon.

The native End Mission action can announce a single faction result while Match stays active. It resolves the faction name at delivery and uses GC Lobby's result wording. The legacy notice used a localized game-over screen label. This wording difference and delivery to remote clients still need comparison.

Pass `--include-industrial-candidate` with `--stage`, `--units`, `--base-database` and `--workshop-addons` to reproduce local Missions Dev commit `f793b765`. Industrial Metal retains both 90 percent player-loss rules, the source five-second notices and 15-second outcome announcements, and the Match-start instructions with delays of 2,885 and 2,895 seconds. It retains the authored weather and date, seven vehicle placements, both AO polygons and the 180-second staging countdown. Workbench session `logs_2026-11-20_13-30-02` loaded 13 groups, 70 slots, 11 briefing pages, two map icons, two polygons and nine rules. A local player claimed a slot, advanced through Briefing and received both CDF victory notices in Match with the Russian side empty. The player then advanced to after-action report. Long-duration timing, staging-zone release, AO enforcement, occupied-side casualties and vehicle operation remain unverified. The loaded addon set emitted inherited equipment diagnostics.

## Irish Checkpoint

Pass `--include-irish-candidate` with `--stage`, `--units` and `--base-database` to assemble the world in local Missions Dev commit `0b3b6977`. The recipe shares Industrial Metal's mode assembly. It preserves the Arland infantry and vehicle navmesh references, original checkpoint composition and vehicles, the IRA faction callsign table, and the 180-second staging timer. The IRA boundary had one crossing. The native polygon joins the two source lobes within 5 cm of the crossing and keeps all five authored points. The British polygon uses its five original points.

Workbench session `logs_2026-11-20_13-34-59` registered five groups, 30 slots, ten briefing pages, seven icons, two boundaries and nine event rules. A local player claimed a British slot and received the casualty message and British victory announcement in Match with the IRA side empty, then advanced to after-action report. The timer instructions remain scheduled at 1,985 and 1,995 seconds from Match start. Remote delivery, live occupied-side casualty thresholds, the long timers, vehicle and equipment behavior, callsign display, staging-zone release, and enforcement at the IRA boundary crossover remain open.

## Soumont Sawmill and Watery Grave

Pass `--include-arc-capture-candidates` with `--stage`, `--units`, `--base-database` and `--workshop-addons` to reproduce both worlds. Local Missions Dev commit `a40ce5a0` adds the launchers and world placements, and `14deb781` removes the legacy vehicle attachment field from Watery Grave. The converter records all seven original Watery Grave vehicle-to-group links and indexes 838 authored group links across 117 source worlds under `legacyVehicleGroupLinks`. These links supported the role-selection vehicle preview and locked-vehicle removal in legacy. Those lobby functions remain open.

Both missions retain 60 original roster positions, the two AO shapes, the restriction zone, weather, stock and Workshop vehicles, and four outcomes. Watery Grave's launcher now advertises 60 slots, matching the authored roster and the upper value in its title. Russian players must have exclusive presence in the 30-metre sawmill sphere for 120 seconds, or in the 15-metre command-post sphere for 300 seconds. The converters preserve discovery, one-tick rollback on interruption, the original status texts and the stop-after-first-change setting. The separate delayed outcome messages keep Match active.

In Workbench session `logs_2026-11-20_13-40-04`, Soumont Sawmill registered nine groups, 60 slots, ten briefing pages, two icons, two shapes and 12 rules. The authenticated local test moved its Russian player into the sawmill sphere. The capture trigger fired 124.7 wall-clock seconds after the move, including the time to start sampling and accumulated tick scheduling overhead. The player received both capture status messages, the original victory message and the delayed result announcement, then advanced to after-action report. Capture with an opposing player present, remote delivery, and actual vehicle behavior still need comparison.

Watery Grave registered eight groups, 60 slots, six briefing pages, two icons, two shapes and 12 rules. The terrain surface at the capture centre was 0.027 metres above the authored centre. The native teleport rejected that position, so the test moved the player to the surface inside the unchanged sphere. The capture triggered 314.8 wall-clock seconds after the move. The player received both status messages, the source victory message and the delayed result announcement before advancing to after-action report. The wall-clock difference reflects the observed sampling schedule. Compare it against a legacy run and test contested capture, interruption and remote delivery.

## Breach and Clear roster correction

Local Missions Dev commit `fcf587f5` restores both authored Senior Riflemen to their original playable groups and updates the launcher from 49 to 50 players. The legacy Senior Rifleman inherits the RHS Flora base loadout, which inherits the RHS faction base and `Character_Base`. The installed legacy GC Lobby overlay provides `PS_PlayableComponent` on that base character, and both source rifle squads set `m_bSetPlayable 1`. The earlier Units-only inspection stopped at the RHS boundary and incorrectly treated those two roles as AI. The source-versus-output check now matches the exact ordered roster GUIDs, including both Senior Riflemen, with no added AI groups.

Workbench session `logs_2026-11-20_13-40-04` registered seven groups and all 50 slots after the correction. The local player claimed a slot and advanced to Briefing. The run did not inspect individually issued Senior Rifleman gear.

## Capture timing and WetFeet date

Local Missions Dev commit `eed4e392` enables elapsed-time capture for Soumont Sawmill, Watery Grave and WetFeet. The legacy trigger adds the elapsed duration between checks. GC Lobby's default adds at most a single configured interval, which discarded the recurring fractional overrun in the original conversions. The opt-in uses elapsed mission time and still evaluates the area once per check. The legacy trigger used system ticks, so behavior with a paused or time-scaled world still needs comparison.

Workbench session `logs_2026-11-20_13-55-35` exercised a 300-second capture with 1.05-second sampling. The opt-in completed at the expected sample, while the default kept 286 seconds of progress. A separate check showed that the one-sample interruption deducted 1.5 seconds and held the remaining four seconds. In the live Soumont run, the trigger fired 120.3 seconds after a player entered the area, compared with 124.7 seconds on the prior port. The full Watery Grave and WetFeet timers still need a live rerun with this option.

WetFeet's source date fields came from the legacy weather overlay. The converter now writes the native date override and preserves the source rainy preset and random weather setting. Workbench read July 23, 2021 from WetFeet's active Time and Weather Manager.

## EFP Stragglers

The Arc capture command also assembles EFP Stragglers from local Missions Dev commit `c7fc807a`. It retains the three Ruha navmesh references, the world fog placement, both staging zones and their 300-second Match-start release, and all four source outcomes. The 30-metre hamlet sphere counts living players and AI, requires exclusive Separatist presence, and tracks later changes after the initial 60-second capture. The original `SEP_BTR` placement is intact. Its legacy preview and lock link to `SEP_HQ` is recorded for a follow-up.

Workbench session `logs_2026-11-20_14-03-48` registered eight groups, 60 slots, 11 briefings, two icons, one shape and 12 rules. Both staging zone entities were present. The active weather manager reported September 1, 2025, zero fog, fog-height density and rain, and wind at five metres per second. The date uses the legacy implicit day of one and the authored month and year. The mode-owned component applies the environmental settings through the native weather manager.

An authenticated Workbench test spawned a native Separatist AI group at the hamlet while the sole player was over one kilometre away. The capture fired 60.6 seconds after the spawn. The local player received the capture status updates and the Separatist victory message, then advanced to after-action report. The run did not cover a contested or reversed capture, release of both staging zones at 300 seconds, the hour-long timer, remote delivery or vehicle handling.

## Foreign Exchange Students

The Arc capture recipe preserves seven playable groups, 46 roles, five briefing pages, three map icons, two M1025 placements and their ladder inventory configuration. The launcher now advertises the actual roster of 46. Nine US roles use their original stock parent prefabs, including the rifleman with the same authored equipment variant. The recipe preserves the US callsign format and British faction color overrides. The original setup specifies Cloudy at 17:45 and releases both staging zones 180 seconds into Match.

The installed Longmoor 3.2.1 package no longer resolves the legacy Longmoor Extended resource. Workbench started without terrain and crashed when it initialized the spectator. The converted world uses the LongmoorTrainingArea subscene referenced by the current Workshop Game Master scenario. That terrain supplies the map, AI world and weather manager. GC Lobby supplies perception. Workbench loaded the terrain by path despite reporting a package GUID warning. The capture centre remained 0.037 metres above the terrain surface at the authored coordinates. Geographic parity with the historical package still needs comparison. Foreign Exchange Students II, BugOut and Longmoor Lashings also reference the old terrain path. [Longmoor Workshop changelog](https://reforger.armaplatform.com/workshop/61BD1072371B5470-Longmoor/changelog)

The 28-metre objective requires a US share of at least 75 percent among living players and AI inside the sphere for 120 elapsed seconds. It stops tracking after the first capture. The converter retains all four outcomes and their announcement delays. In Workbench session logs_2026-11-20_14-16-14, the native rule rejected an empty area and two of three US occupants, accepted three of four and four of four, and started Match after a local US squad leader claimed a slot. The session registered 12 mission rules.

The US capture fired 119.5 wall-clock seconds after the local player entered the objective. The player received both status messages, the original victory message and the delayed MISSION END notice while Match stayed active. The local admin then advanced to after-action report. The empty British side also triggered the casualty announcement at the initial population sample. Live contested capture, interruption, occupied-side casualties, remote delivery, weather and restriction-zone behavior, equipment and vehicle handling still need comparison. Missions Dev commit 9afc5539 is queued for the next push batch.

## Foreign Exchange Students II

Run the Arc capture recipe with the same installed Longmoor dependency as the first mission. The sequel preserves eight playable groups, 57 slots, ten briefing pages, four markers, the six-point AO boundary and the original Chinook wreck placement. The launcher now advertises the authored total of 57. The two additional legacy US playability wrappers for platoon leader and platoon sergeant use the same stock parents with native group playability. The wreck mesh resolves through ArmaTerrainCore, already loaded as a terrain dependency.

The shared legacy cover-map imageset and atlas are now registered at the path used by the markers. The original texture and its source image are byte-identical to the legacy assets. The asset also serves Foreign Exchange Students and Irish Checkpoint.

The source has separate 12.5-metre spheres for US and UK. Both count living players and AI, require exclusive local presence for 180 seconds, and latch after their first capture. The converter preserves both centres, the weather settings, two staging zones with a 120-second release and all five outcomes with their original delays.

Workbench session logs_2026-11-20_14-27-39 loaded the wreck and registered 15 rules. Both staging zones were present before Match and absent at the 123-second probe. The US player captured 180.1 seconds after entering the site. The UK rule was still false at the end of that hold. After the player left, a spawned British AI section captured the site in 180.9 seconds. Both sides delivered the source status and victory messages and the delayed MISSION END notice during Match, followed by an admin advance to after-action report. A second Play run loaded the 6x4 cover-map quad through the same ImageWidget call used by the briefing map. An Enforce probe confirmed that the legacy ratio expression evaluates three divided by four as 0.75.

The tests cover sequential uncontested captures in the converted world. Simultaneous presence, interruption, the casualty and timeout outcomes with both sides occupied, dedicated-server delivery, issued equipment and historical terrain parity still need comparison. Missions Dev commit 9027ee2d and the shared cover-map assets are queued for the next push batch.

## Bug Out

Add --include-bugout-candidate to the conversion command. It assembles the 28-slot mission with four British groups, seven briefing pages, two markers, the original scenery and vehicles, and the current Longmoor terrain parent. It retains the April 18, 1982 date, the Rainy preset and the 06:02 mode setting. The launcher also retains its authored starting hours field.

The legacy extraction code counts all eligible living players across the world in its denominator and UK players inside the 30-metre sphere in its numerator. Native Local faction share now has an opt-in Whole Mission setting that preserves this denominator. Bug Out uses it for the five-second 75 percent extraction and the separate 0.1-second, 10 percent arrival signal. The 700-metre pursuit triggers continue to count local living players and AI while disregarding GC_ARG. The port keeps the eight authored squad spawns, their delay anchors, transforms, assigned names and waypoint order. It preserves each flare spawn and deletion time, the first-pursuit removal of the current flare, and both original outcome messages. The legacy framework schedules all flare instructions at Match start and only deactivates events that have yet to run, so the later queued flares still fire.

Workbench session logs_2026-11-20_14-41-49 registered four groups, all 28 slots, seven briefings and both markers. The active calendar reported April 18, 1982. The extraction rule passed with three of four eligible players and failed with three of five or an empty population. The live player crossed the first pursuit area, which spawned three named squads and removed the current flare. The 30-metre arrival signal fired on entry and the victory event fired 5.2 seconds after the move. The local player received the original victory text. The second pursuit squad spawned as the player entered extraction. Both Match-start waves and both waves after arrival spawned on their authored delays, and the session reached after-action report.

A second Play run verified the final 25-rule version. The first pursuit trigger still removed the active flare, and the queued 80-second flare appeared. Historical terrain, issued equipment, full AI waypoint routes and remote delivery remain open.

## Fighting Retreat

The Arc capture recipe preserves the Zarichne subscene, 72 slots, 11 briefing pages, four markers, one AO boundary, both 120-second staging zones, and all four outcomes. It retains the US and Russian faction callsign tables, two Ural trucks, two Tigrs, and the original night-vision inventory. The version of the legacy extraction script that this mission used counts US players inside the 50-metre sphere against all eligible living players across the mission. The port applies the authored 90 percent threshold and triggers on the next sample. It preserves the 15-second delay to the extraction result notice.

Workbench session logs_2026-11-20_14-41-49 registered 72 slots, 11 groups, 11 briefing pages, four icons, one shape and 12 rules. The 4x4 cover-map quad loaded. The rule passed with nine of ten eligible players and rejected nine of eleven or an empty population. A local US player entered the sphere, received the original victory text and delayed result notice, and advanced to after-action report after both staging zones released. Occupied-side casualties, remote delivery and issued equipment remain open. Both extraction missions are committed to local Missions Dev in d9308b98.

## Mixed layers and moved marker assets

The batch converter now applies group conversion to each recognized block when a layer also contains other entity types. The report records the source line, group and slot totals for those blocks. It reports each roster failure at its placement and lists advertised launcher counts alongside the verified slots. Match-start notifications and outcome rules can share a framework when their source locations account for every event.

Ravine Ruckus was generated and committed to Missions Dev in 493f395e. File comparison verified 20 CDF and 22 Russian slots, ten briefing pages, two 300-second capture spheres, five outcomes and 120-second staging. The original 44-player listing was retained. The migrated map reuses the converted AO and its inverted fill. The legacy TilW AO prefab leaves its optional cover component disabled. The source placement overrides its width without enabling it, while the separate map-shape component is explicitly enabled. Explicitly enabled cover components continue to require a separate rule. Capture, roster and map parity were reviewed from files and registered resources.

The marker converter resolves moved image assets against a unique Dev GUID and matching resource metadata. Missions Dev commit 2545c429 fixes the stale cover-map paths in Foreign Exchange Students and Irish Checkpoint. The reviewer also accepts a dependency path alias when the game resource database explicitly registers the same GUID and path.

## Stock playable prefab wrappers

The shared roster converter recognizes 17 group wrappers and 72 character wrappers from the published legacy GC Lobby package 0315131312150201. The catalog records each original identity, archive content hash and stock parent. Its character entries have no changes beyond enabling legacy playability. Its group entries inherit a stock group and supply an ordered roster. The converter verifies the referenced paths and stock parents, applies the old placement's replacement and append operations, and writes the resulting roster for native lobby playability. Unsupported loadout overrides, uncertain inherited members and other custom group authoring still need separate rules.

The batch inventory verified 261 more slots across 12 worlds. Last Bastion was the first complete draft from these rules and is committed to Missions Dev in 4c203b29. The file comparison matched all eight group names, positions and ordered stock role identities. All 48 slots match the launcher count. The draft passed the resource check and reproduced byte-for-byte on a second run.

## Non-playable AI and compound outcomes

The batch converter can retain AI-only groups whose native prefab ancestry matches across Units revisions. It compares inherited group behavior by resource GUID, checks both playability settings and verifies the authored character slots. It preserves placements and named static waypoints. Unknown group fields and ambiguous player-versus-AI membership still stop the draft.

Batch outcome conversion now supports nested conjunctions and disjunctions over resolved casualty and area flags. The native condition tree stays live until the source expression becomes true. At that point the event latches and runs its original delayed instructions independently. Single-faction AI casualty authoring uses the native living-plus-confirmed-deaths observer. Legacy research references are the TilW Mission Framework character controller and casualty flag sources. The comparison still calls out possession, faction changes and recount timing.

Concrete Jungle is committed to Missions Dev in 3235061c. The file review matched its 60 playable slots, all eight CDF AI groups and their authored waypoint assignments. Russian victory retains both checks, complete British player loss and at least 80 percent CDF AI loss. The warnings and announcements at 4,800, 5,400 and 5,410 seconds are preserved. The draft passed the configured resource audit.

This pass removed 124 AI group placement blockers across 59 other worlds. The generated draft and report reproduced byte-for-byte, and previously staged conversions still match.


## Initial AI orders and silent captures

Batch conversion translates Match-start waypoint assignments into native actions. It preserves the named group, ordered waypoints, append or replace setting, and delay. The legacy default is five seconds. Each target must resolve to a unique placed group or waypoint in the draft. Flag-gated orders and replacements that reference a waypoint already assigned on the group placement stay open. The source contracts are TilW Mission Framework's `TILW_AssignWaypointsInstruction` and `TILW_BaseInstruction` in `TILW_Instructions.c`.

The faction capture rule now preserves disabled status messages. `TILW_BaseTriggerEntity` defaults those messages to off. A silent capture still updates the objective flag after the authored hold and sends any message authored in the outcome event.

Pirate Radio is in Missions Dev commit `0357b05c`. File comparison verified 24 playable slots, seven appended AI waypoint assignments five seconds into Match, the 180-second staging release, two silent 60-second CDF captures and the three legacy outcome paths. Both captures must succeed for the objective victory notice. The draft passed the resource audit. AI execution and capture timing in this world have file-review coverage.

Privateering's draft also preserves 24 playable slots, six replacement waypoint assignments and both 180-second UK captures. The draft remains staged because the source's two RHIB transport placements and one RHIB gunboat placement reference missing prefab registrations. Restore verified equivalents for `90FB4A19890F9E5C` and `DDE009B6A29873EE` before publishing the world. Other worlds reference the same legacy boats and can reuse that asset rule.


The installed-provider audit now accepts registered resources in both v6 and v7 databases. It resolves the Irish Checkpoint tactical graphics through its installed v6 provider. The audit still flags six previously installed launchers. Bug Out and both Foreign Exchange Students missions used the configured Longmoor terrain in earlier Workbench runs, while the installed Longmoor 3.2.1 database marks the referenced subscene path as unregistered. The two races and Breach and Clear also have unresolved authored resource references. The file review lists each path and its owner for follow-up.


## Conditional notices and passive resources

The batch event compiler retains legacy labels containing spaces and punctuation. It translates message-only events driven by a supported casualty check, capture, or compound condition with the same one-shot timing, audience and per-instruction delay as outcome events. Messages already emitted by the Match-start notification rule are reused. If several events depend on one capture flag, the generated checks use distinct serialized identities and only one publishes capture progress messages.

The resource pass follows uniquely identified materials, compiled textures, image sets and the supported native light prefab format. It preserves the asset GUID and resolves stale paths to the canonical repository file when the placement or metadata establishes the identity. Resource references in materials and image sets are also included in the comparison and installation audits. Unknown prefab behavior and ambiguous GUIDs remain in the blocker report.

The latest inventory translated 240 previously unhandled notifications across 236 events and cleared the framework event blocker for 19 worlds. The remaining roster, boundary, mode and resource checks still gate those worlds. For example, Two Checkpoints has all four source framework events accounted for, including its three conditional notices, but the CDF roster contains duplicate serialized callsign fields with conflicting authored values. The batch still requires a reviewed resolution for those placements.


## Native group and mode placements

Group conversion carries over authored faction, radio frequency, static waypoints and static vehicle names. It also preserves an AI-only group's native formation override. These are base-game `SCR_AIGroup` fields and components. The batch checks that static links name unique placed vehicles or waypoints and preserves the original order of those lists. Other component overrides and links with dynamic or inherited targets still require review.

When the known GC legacy mode prefab has no placed coordinates, the converter uses its inherited origin. The published `PS_GameMode_Lobby.et` sets that origin to zero. Authored coordinates still take precedence. A default standalone Perception Manager without overrides is handled by the same native manager already provided by the Lobby mode prefab. Custom light curves and rotated manager placements still require review.

File comparison cleared 42 group placement blockers across 29 worlds, 15 omitted mode origins, and 30 default Perception Manager placements. The full staged regression reproduced all 974 prior files byte for byte and produced eight additional group layers. Tip of the Spear now retains its three NTA formations. Its placed vehicle crews and navmesh regeneration remain separate conversion gaps.


## Marker audiences and inherited presets

The marker converter keeps visibility for assigned factions independent of visibility for viewers without a faction. The native marker component can use the existing replicated allowlist even when the list is empty. This preserves listed factions plus unassigned viewers, unassigned-only markers and hidden markers. Existing authored markers retain their default audience behavior.

The five entries in `marker_presets.json` come from the installed TilW Mission Framework package `61F0E6AB2D06B1C6`. Each entry records the legacy GUID, path, archive content hash and supported fields. All five inherit the PS Core EditableMarker prefab. Placement fields override the archived defaults. The group imagesets resolve through the game database or the installed Soviet Tactical Graphics provider. The converted placements use the native GC Lobby marker component.

The file pass cleared marker-layer blockers in 56 worlds. Standalone staging added 32 layers with 179 icons. No previously staged icons were lost, and the 982 previously staged files reproduced byte for byte. The audience truth table matched all eight combinations of any faction, unassigned visibility and empty or nonempty faction lists. Workbench reloaded the shared Lobby scripts with zero compile errors in session `logs_2026-11-20_16-00-26`. In-world rendering, unusual phase masks and explicitly authored z-order remain open.


## Independent boundary audiences and RHS AI

When an AO outline and its map fill target different viewers, the batch writes the fill as a passive native polygon. It retains the original vertices, transform, color and audience. The enforcement polygon retains its own faction filters, outline and grace period. Previously staged Battle for Rasman output now also includes the missing pair of native shapes for its BLUFOR boundary. Its earlier OPFOR shape reproduced byte for byte.

With --workshop-addons the batch verifies the exact GUID and resource paths of six RHS Status Quo VKPO demi-season randomized roles and the matching crew prefab. This allowlist applies to AI-only groups with matching Units ancestry. Mission-owned and Units-owned overrides still need their normal checks. The inventory records the SHA-256 of the provider database used for these checks.

Trench Assault is in local Missions Dev commit `69edea1f`. File comparison verified 18 playable slots, both six-member AI sections and their ordered waypoints, random starting weather and a 90-second staging release. The Russian and CDF outcomes retain their casualty thresholds and the five- and fifteen-second announcements. Its two enforcement polygons retain their original geometry and grace periods. The CDF-only outline and public inverted fill are separate native shapes. The draft passed the resource audit, and a second batch run reproduced its files and inventory byte for byte. Occupied-side casualty timing and map rendering remain open for shared verification.


## Native event actions and staging countdowns

Batch event conversion preserves named entity cleanup, supply quantities, repairs, clock changes, weather presets and briefing-marker visibility edits. It retains source action order and the individual delays. Fuel servicing still requires a verified root fuel manager because the legacy handler requires one before changing attached tanks. Map edits require unique converted marker targets. Description edits and attempts to hide one faction from a legacy public marker remain open.

The catalog matched eight cleanup events against their exact source targets and delays. Pondering Thy Orb now has all authored framework events accounted for. Its 180-second display countdown is retained independently of the event that removes its staging boundaries. The same countdown rule cleared a mode blocker in one other world. Pondering Thy Orb still needs two missing character prefab identities and a reviewed inherited-roster conversion.

The focused file checks covered clock, weather, marker audience, supply and repair mappings and unsupported inputs. Two batch runs reproduced the inventory byte for byte. All 982 prior explicit migration outputs remained byte-identical.


## Verified aliases in inherited rosters

Batch roster comparison accepts a legacy playable wrapper and its native stock parent as the same slot when the path and GUID are verified and the position in the ordered roster matches. Unrecognized wrappers, ambiguous resources, changes in membership and reordering still stop conversion. The four additional US and Soviet rifleman wrappers include their reciprocal alternate variants. The archived GC Lobby and stock prefab files agree on those links after applying the reviewed resource identities.

The Bad Orb AI world now uses the existing Missions Dev path for its archived Core GUID. The current prefab matches the published legacy Core prefab and loads the same three navigation resources. The path rule applies to legacy references that use the old filename and preserves the world and placement identities.

Pondering Thy Orb is in local Missions Dev commit `d26a513f`. The file comparison verified all 48 ordered slots across eight groups, the ten briefing pages, both staging-boundary targets at 180 seconds, the 90 percent casualty outcomes and the three timeout messages and announcements. The existing Dev boundary layer was reused. The resource audit passed and all nine committed draft files match the reviewed manifest. Live staging release, map rendering and occupied-side casualty timing remain open. The shared roster rules cleared 21 group placement blockers across eight worlds.


## Static objective interactions

The batch converts a supported legacy action on a named, replicated world object to the native mission interaction component and action. It preserves the action context, hold duration, visibility range, text, faction audience and completion flag. The shared Lobby component has an opt-in single-use setting that consumes the action after a successful server-validated interaction and replicates availability. Mission flags persist each objective so the original conjunctions and delayed results still use every completed objective. Condition-gated actions, parent deletion, dynamically spawned objectives and duplicate producers still require separate handling.

The converter omits an otherwise unsupported legacy flag sampler only when its ordinary flag list has no consumers in the mission resource closure or committed mission scripts. Barn Busters XS has three such unused cache-selection flags. Its three placed caches remain independent objectives. The vehicle alias rule verifies the same GUID at the current provider path for two Tactical Flava TIGRs and one WCS AH-6M variant. Missions Dev already receives both providers through Units Dev.

Barn Busters XS and SOBR Up are in local Missions Dev commit `aa2e3a13`. File review verified 22 and 35 playable slots respectively. It matched all six objective placements, their 30- or 5-second hold durations, faction restrictions, single-use settings and flag consumers. The drafts passed the installed resource audit. The new shared script compiled with zero errors in Workbench session `logs_2026-11-20_16-29-13`. Live interaction completion and multiplayer availability replication remain open.


## Immediate staging release and neutral timeout

The native placement pass now retains Tank, Turret and SCR_DestructibleEntity placements with no legacy components. This cleared 64 tank, 45 turret and 223 destructible placement blockers in the catalog. Prefab identities still pass through the installed resource audit before publication.

The legacy PS GameMode timer subtracts up to 1,000 milliseconds before any wait. An explicit zero or subsecond value therefore removes restriction zones immediately when Match starts and displays no countdown. The batch preserves that behavior with a Match-start zone release. Fractional values above one second remain flagged for separate review.

Get to the Chop Tank is in local Missions Dev commit `baaf12a0`. The comparison verified 32 playable slots, the original locked M113, both 30-metre player-only capture zones and their 300-second holds, both named staging zones and four faction-result notices. The 1,800-second timeout popup stays in place. The legacy Core overlay suppresses automatic endings, and the factionless neutral instruction cannot construct its old faction-based popup. The port records that neutral result in the server log at 1,820 seconds and leaves Match active. The files passed the resource audit. Live capture timing and restriction-zone release remain open.

## Delayed extraction zones

The batch replaces supported Match-start extraction spawns with native spawn actions at unique named anchors. It preserves the delay, radius, hold duration, player share, faction and authored status messages. Each area starts counting after its spawn and first discovery. When multiple areas originally set one flag, a native condition accepts a hold at any one area. The converter requires a single positive, once-only consumer and holds drafts with ambiguous flag producers or dynamic anchors.

Erquy Evasion is in local Missions Dev commit `9fc57ad4`. The file comparison matched all 54 playable slots, three extraction spawns at 35:05, three 30-metre zones and the 120-second hold for at least 80 percent of eligible USSR players. It also verified the current Units registration for the helicopter against the archived prefab. The resource audit passed. Live multiplayer extraction timing and status messages still need shared feature coverage.

## Delayed framework flags

The batch translates legacy Set Flag instructions to native GC Lobby flags with a starting count of zero. The original instruction delay controls when the count becomes one. Positive literal tests and nested AND/OR conditions can observe that flag. Every authored reference must be accounted for in the event rules. A competing producer, clear action, inverted test or script reference keeps the flag out of the conversion. The draft also requires a successfully converted producer for each converted reader. The legacy source contracts are `TILW_SetFlagInstruction` and `AdjustMissionFlag` in TilW Mission Framework.

The catalog pass translated 11 flag actions and two dependent events across 12 worlds. These worlds still have other conversion blockers. Existing drafts and the earlier comparison inventory reproduced without changes. Simultaneous flag changes and their dependent events still need shared runtime verification.

Barn Busters remains blocked on its pregame selection and visibility chain. The legacy sampler picks one of three cache areas during world initialization. Its selected event spawns the interactive cache five seconds later, reveals an icon to the Separatists and reveals the matching briefing page to the same faction. GC Lobby has random event groups and marker visibility actions. The conversion still needs dynamic briefing visibility, verified pregame scheduling and support for the spawned interactive prefab. Preserve all three side effects from the same selection.

## Marker phase thresholds and inherited slots

The marker converter now maps all contiguous visibility thresholds onto GC Lobby's phase order. It recognizes legacy role selection as `SLOTTING` and projects away legacy phases with no separate Lobby equivalent. Noncontiguous visibility, such as hiding an icon only during Match, stays in the report.

For groups that explicitly force their characters playable, the roster converter now recognizes verified character ancestry through the legacy GC Lobby overlay on `Character_Base.et`. It retains each character's existing resource identity and order. Reforger MCP verified the published overlay in GC Lobby `0315131312150201` and the parent chain in published Core and RHS. Native `GameEntity` placements also carry over when they have no legacy authoring. Their references still go through the resource audit. The full pass reduced remaining group placement blockers from 693 to 586 and native entity placement blockers from 66 to 6.

Zero Arc Thirty is in Missions Dev commit `666c34e7`. The comparison matched all 40 slots across seven ordered rosters, the preview-only marker, the 120-second staging release, the 180-second capture and the three authored outcome notifications. Rocky Start is in commit `0a74ec54`. Both source and Dev have 40 slots across six groups. The original launcher still advertises 39 and was copied verbatim. The source history includes a later playability update for the two OPFOR groups. The resource audit passed for both worlds. Multiplayer map display, capture timing and casualty observation remain shared runtime checks.

## Callsign repairs and material overrides

The roster converter recognizes a duplicate legacy callsign component when the first copy supplies numeric values and the second copy with the same ID contains only the disabled default. The published Two Checkpoints layer and the source before callsign unification show squad indices 3 and 4 on those placements. The legacy playable manager reads the numeric values even when the component is disabled. Conflicting numeric values still require review.

The installer can apply this repair to an existing Dev layer when removing the redundant numeric triplets reproduces the generated layer. Use `--replace-reviewed` after reviewing the reported paths. The installer checks the original file hash and requires the target layer to be unmodified in Git. Two Checkpoints is in Missions Dev commit `1a38c897`. The file review matched its 73 slots, both squad labels and the conditional capture and casualty notices. Resource checks passed. Live display and notification timing remain shared runtime checks.

The asset converter also carries over simple mesh material overrides on destructible scenery and follows the referenced material by its registered GUID. Farm now generates all 73 slots and resolves the authored HESCO prefab and material. The installed base-game database registers willow prefab `23C0BE9D80929DBD` at a new path. The batch preserves the GUID and placement and uses the current path.

## Inherited interaction replication and faction labels

The batch now converts unnamed objective placements when their root replication is authored or inherited from a verified stock prefab. The Bunker Busters XL cache uses the stock destructible crate with an enabled inherited replication component. The conversion preserves the 60-second hold, RHS USAF restriction, single-use action and `US_CAP` outcome. The draft still references the retired wirecutters GUID `8554ED3F088BADA6`. Its launcher advertises 64 players and the current roster contains 56 entries. The resource identity and source roster count still need review before installation.

Legacy faction manager overrides may be unnamed. The converter still requires the known inherited manager ID and preserves the authoritative faction record. It keeps the presentation overrides on that record in GC Lobby. The playable roster pass now also recognizes the reviewed base FIA character ancestry.

We Hate Stairs Express is in local Missions Dev commit `47b2a66b`. The file review matched all 31 ordered slots, the Angry Miners faction label, the 10-second countdown, the time and date and the two casualty notices. The draft passed the installed resource audit. Live faction labels and casualty timings still need shared runtime coverage.

## Radius capture settings

The shared radius capture rule now uses the legacy defaults of a 25-metre radius and zero hold when those fields are omitted. It carries over both ratio directions, ignored factions, discovery policy, elapsed hold time, and the six optional status messages with their original audience. Authored names and placement transforms survive the conversion. Unknown comparison modes, duplicate area names, and capture scripts that can fire outside gameplay still require review. The source contracts are TilW Mission Framework's faction trigger, base trigger and published faction trigger prefab.

The catalog pass cleared faction-trigger blockers in 51 worlds and framework-event blockers in 45. Nested polygon triggers and custom trigger-side effects remain open. Live discovery, loss and status-message timing still need shared multiplayer coverage.

Crash Party and Ridge Rumble are in local Missions Dev commit `0abd5519`. The comparison matched 75 and 68 ordered slots respectively, both 25-metre capture areas, both 180-second holds, casualty thresholds and authored timeout notices. Ridge Rumble's original launcher advertises 66 and retains that value. Crash Party's world reference was normalized to the verified path with its original GUID. Both drafts passed the installed resource audit.

## Polygon captures and editor preview fields

The batch converts a closed native polyline with one supported unnamed legacy capture child into a named native area. It retains the parent's original points, transform and editor line color. Counts use the polygon boundary and preserve the child's faction, comparison and hold settings. The rule covers standalone and grouped polylines. Extra child entities, named child triggers, map-shape components, and unsupported geometry stay in the blocker report.

The legacy base trigger's sphere surface, outline and color settings are used only by its Workbench debug drawing. They no longer block conversion. In-game shape components and audiences still require conversion. The catalog identified eight supported polygon captures across three worlds. Faction-trigger blockers fell from 92 to 81 worlds and framework-event blockers from 168 to 157 worlds. Counter the Counter's six grouped areas retained every authored vertex and transform. Shared runtime coverage is still open for polygon boundary membership, capture timing and status delivery.

## Map covers and passive boundary fills

The batch converts a supported legacy cover into a native inverted map fill. The visible rectangle uses the legacy world-space centroid, three-dimensional radius and authored width ratio. GC Lobby frames the rectangle on the first eligible map opening. The auxiliary polygon retains its original points and audience. Its outline uses GC Lobby's screen width in place of the old zoom-dependent metre width. Live layer order and framing still need shared multiplayer review.

A disabled enforcement component with an active map fill now converts to a native visual-only polygon. The pass can reuse an existing Dev boundary layer and place only the missing shape in a separate layer.

Blackhawk Brawl and Heely Co Peter Raid Part 1 are in local Missions Dev commit `2c9c295a`. The file audits matched 77 and 30 slots respectively and validated the authored capture checks. Blackhawk preserves the passive US/USSR fill and uses the current Bystrany topology referenced by the provider's own Map Entity. The historic contour binary was unavailable for comparison. Heely retains the authored 1.1 cover ratio and uses the same verified Kunar AI World registration already used by the Gwotline port. Both drafts passed the resource audit. The shared shape pass reduced boundary blockers from 122 to 114 worlds before the terrain aliases were applied.

## Mission-owned player loadouts

The converter retains mission-owned playable character prefabs under their original GUIDs and paths. It removes the standard Reforger Lobby playability marker after verifying the root character and supported parent ancestry. A native playable group registers the slots. The authored equipment, character presentation and faction affiliation remain on the character prefab. Parent and equipment paths change only where the existing asset identity is verified. Unknown inheritance, custom legacy components and differing Dev resources still stop the draft.

The stock wrapper ledger now includes the archived Soviet rifle squad wrapper, verified through the published GC Lobby package. Its native group can retain explicitly authored mission-owned slots. The legacy FIA base overlay from published Core also matches the Units Dev copy byte for byte. This allows the Syrian Checkpoint HTS and SAA character prefabs to keep their authored gear while selecting the canonical Units parents and radio.

Syrian Checkpoint now stages all 17 custom characters and all 60 ordered roster slots. The resource audit still holds the draft on the legacy jacket, trousers and baseball cap GUIDs `F102F28DCAC86628`, `5C8C69E719F68617` and `5DF4933859A5CF08`. The archived index and installed registries did not resolve these assets. Live equipment and faction presentation remain open.

## Timed prefab spawns

The batch can translate plain legacy prefab spawns to GC Lobby's native spawn action. The conversion keeps the delay, exact anchor, prefab identity and any assigned name. The resolver accepts registered stock resources, unchanged Units prefabs, and supported passive mission-owned resources. It verifies that the anchor is unique in the draft and that the spawned name cannot collide. Pregame events, legacy-scripted spawn resources, and separately authored group or crew configuration still require their own conversion rules.

When another instruction or condition blocks the full event, the report records each plain spawn that passed the instruction and resource checks. The catalog found 29 such actions across five worlds. Arty Arty Arty accounts for 19 and still needs its separately named driver groups and later orders. A complete event built from a registered stock resource verified delay, anchor and name serialization. Live placement and timing remain shared runtime checks.


## Standard mode prefabs and stock AI groups

The mode converter recognizes both standard published lobby prefab registrations, including the direct parent used by older scenarios. It still reports unknown fields. Seven worlds author legacy construction progress overrides. The published Core script applies separate required-progress multipliers before and after a timer that starts in Match, with defaults of 300 seconds and multipliers of 2 and 10. The legacy mode receives its building manager from ACE Trenches Dev, which is absent from the current Missions Dev dependency graph. Restoring that manager and the authored progress settings is a future compatibility slice. The report retains each field and value, including Villeneuf Punchup Redux's two multipliers of 1000.

The batch also preserves verified native non-playable AI group prefabs and their supported placement fields. The stock group ledger records the prefab digests, base ancestry and roster resource identities reviewed from the installed Reforger archives through Reforger MCP. The published Core overlay on the stock group base only adds a disabled callsign component. The RHS overlay repeats the entity ID. The converter checks the registered GUID and path and holds placements with playability overrides, unreviewed members or mission or Units resource overrides. Spawned AI behavior still needs shared runtime coverage.

Across all 277 launchers, the pass cleared 31 world mode blockers and 60 AI-group layer blockers. Two runs using different Python hash seeds produced identical inventories, install plans and draft hashes. The existing conversion recipes produced the same comparison report. The four held drafts retain their resource or roster audit findings.


## Navigation around placed compositions

The batch converts an empty legacy navmesh regeneration component under a native placement's component list. It preserves the component identity, parent prefab, transform and other placement data. Custom component fields remain in the blocker report. GC Lobby requests Reforger's native entity and child navigation rebuild after initialization on the server. The component is opt-in, tied to the active GC Lobby mode and cancels its deferred call if the owner is deleted. The reference implementation was reviewed in the published TilW package. The GC Lobby component calls the base game's AI World directly.

Provins Approach Redux is in local Missions Dev commit `46cd612b`. File review confirmed 68 slots against its launcher, all four checkpoint compositions at their original transforms, the 180-second staging release, and the 120-second Obj. Apollo capture. Resource validation passed. A clean Workbench script reload completed with zero errors in session `logs_2026-11-20_18-00-36`. AI routing around the compositions still needs a shared gameplay check.


## Inherited mission loadouts and resource paths

Mission-owned character prefabs can inherit playability through another mission prefab or through Units. The batch compares the Units ancestry against Dev after removing the exact legacy playability marker. It keeps the native character fields, slot order and prefab GUIDs. Changed native ancestor fields, unresolved parents and unrecognized playability overrides still hold a draft. The review cleared 23 mission-owned slot blockers across the 277-launcher catalog without changing the output for any previously approved character prefab.

Farm is in local Missions Dev commit `58f66cad`. File review matched 73 slots, the two 90 percent casualty checks and their delayed notices, and the original willow placement. Its HESCO material and all referenced resources passed validation. The base game supplies the willow under the same resource GUID. The historical asset was unavailable for a live collision comparison.

Gigantomachia now generates all 54 slots. The draft preserves the custom recon drone operator and commander prefab ancestry. The installed registries confirm the original FPV drone and buckshot GUIDs at their current paths. The installed M870 has a different GUID from the mission's Remington 870 prefab, so the shotgun reference still holds the draft.


## Standalone map lines and shapes

The batch converts the standard standalone legacy map shape prefab, including open paths with outline only. It retains the original vertices, transform, audience, line color and authored width. GC Lobby's existing map overlay supports widths in metres for these placements. The client scales the width with map zoom and leaves open paths unclosed. The recorded faction perspective retains both settings. Open shapes with fills and unfamiliar component overrides remain in the report. The legacy shape defaults and zoom formula were verified in the published TilW prefab and script through Reforger MCP.

Joutus is in local Missions Dev commit `0c45ec92`. File review matched 33 playable slots and all 18 vertices of its open red LOA, with a five-metre line width and no fill. The legacy and Dev files retain the faction outcome triggers. The installer found no unresolved resources. The shared rule cleared map-shape blockers in 23 worlds. Workbench completed the final script reload with zero errors in session `logs_2026-11-20_18-11-42`. Live zoom and faction visibility still need shared checks, as do AI casualty recounts after possession or faction changes.


## Authored AI vehicle crews

The batch converts a standard placed vehicle crew with one group and one station that lists explicit character prefabs. It verifies the stock vehicle registration and the Units character ancestry, then preserves the seat type filter and ordered character GUIDs. The server uses the base game compartment order and native boarding and AI group APIs when Match starts. Pregame crews, default passengers, waypoint assignments, and named group merges still require dedicated rules. The old settings and native compartment API were verified through Reforger MCP. Live seat occupancy and group behavior still need shared gameplay coverage.

Retribution and Tip of the Spear are in Missions Dev commit `fccb0206`. File review matched 73 and 57 playable slots, respectively, and the original crew order and seat filters for one UAZ and two BTRs. The batch retained the GUIDs and placement data of Retribution's three wrecks while normalizing their paths. Installed resource checks passed for both worlds. Eastern Envelopment preserves its four BTR crews and 75 slots in the draft. Its legacy Mi-24 registration now points to a different loadout and remains a resource compatibility gap.

Two runs reproduced the roster comparison, file audit and draft files byte for byte. Workbench compiled the crew component with zero errors in session `logs_2026-11-20_18-18-31`.


## Resource registration audits and repairs

The planner groups unresolved resource references by GUID and authored path. The JSON reports list every affected launcher, the files that reference the asset, and alternate exact-GUID registrations found in the installed base-game and Workshop databases. A matching GUID supplies a path to investigate. Eastern Envelopment demonstrates why the content still needs review, since its legacy helicopter GUID now points to a different loadout.

Approved aliases also produce reference-only repairs for committed Dev layers. The installer requires the original layer hash and `--replace-reviewed`, and checks every reference in the repaired layers. The plan keeps unresolved references in other layers visible without holding an independent repair. An unresolved reference inside a repaired layer still blocks it.

Local Missions Dev commit `63be706d` repairs five references in Race Day Kunar and Race Day Ruha. The base fence and wall and Ruha's GAU-19 keep their GUIDs. Kunar uses the verified AI World resource already selected for the Gwotline port. The diff changed only the five reference tokens, and both installers and the explicit race recipes passed. The Kunar city door, Ruha M134 variant and Breach and Clear smoke prefab remain unresolved. Two full planning runs produced identical reports and drafts.


## Crewed reinforcements and one-shot capture checks

The batch converts event-driven vehicle spawns with explicit crew and supported seat filters using GC Lobby's existing crewed-vehicle action. It preserves the spawn delay, the named anchor, the ordered roster, and the separate delay before assigning waypoints. Resource validation checks the prefab registrations and requires unique anchor and waypoint placements. Delayed orders recheck the active Match and framework instance before touching the spawned AI group. Multiple stations, named group merges, default crew and separate idle gunner groups remain open.

An event that tests both captured and uncaptured objectives can use generated capture trackers for supported one-shot areas. The trackers sample the areas before the dependent events and each area emits its own capture messages. Every consumer of a tracked area reads the same latched result. A recurring or reversible capture still needs an independent conversion rule.

Return of the Wac is in local Missions Dev commit `58a5c9fe`. File review matched all 29 slots, four safehouse checks, and both reinforcement events. Each uses the original UAZ prefab and two FIA riflemen, waits five seconds to spawn, then waits another five seconds before assigning its waypoint. Resource validation passed, and two full catalog runs reproduced the drafts and reports. Workbench compiled the runtime change with zero errors in session `logs_2026-11-20_18-33-33`. Live crew seating, order timing and both safehouses changing on the same capture sample remain shared checks. Barn Busters still depends on pregame random placement and faction-specific map edits.


## Teleport insertion points

The batch converts supported native action-manager placements to the existing GC Lobby teleport component and user action. It retains the action label, context transform, visibility range, hold duration, faction audience and named destination. It verifies native replication on the owner and a unique destination in the assembled world. Empty inline actions with no context or destination cannot transfer a player. The legacy standalone prefab is mapped to the equivalent native prefab, including supported context overrides. Multiple active routes on the same entity and flag-gated routes remain in the review report.

Korengal Vacation is in local Missions Dev commit `35c3c703`. File review matched all 115 playable slots and six active three-second insertion routes, each with its original named destination and action context. The standalone interaction with no destination remains nonfunctional. The full catalog identified 23 supported routes across six worlds and two runs reproduced the reports and drafts byte for byte. Resource validation passed. Live teleport execution, remote availability, and cover framing still need shared gameplay checks. The auxiliary map cover outline uses screen-pixel width in place of the original world-metre width.


## Pregame objectives and briefing reveals

The batch converts one uniform startup draw when each flag belongs to exactly one pregame event and no other source file or script uses the flags. It uses the existing GC Lobby random event group during preview, role selection or briefing. The selected event schedules the original delayed actions. Alternatives from the same single draw can reuse a spawned entity name. The converter verifies unique anchors and holds unrelated name collisions, unsupported actions, repeated draws or ambiguous flag producers.

The asset pass converts supported objective interactions inside mission-owned spawn prefabs. It retains the prefab GUID, replication settings, action context, hold duration and faction restrictions. A briefing reveal can target a uniquely named legacy page that starts hidden. The conversion uses the rendered briefing index and updates the audience through the existing GC Lobby catalog. Independent marker edits keep their original names and audiences.

Barn Busters is in local Missions Dev commit `0fd4483a`. File review matched 60 playable slots, all three cache locations, one selected cache, a five-second setup delay, and the Separatist-only page and marker for each location. The cache preserves its 30-second US-only interaction and sets the original outcome flag. Installed resource checks passed. Two catalog runs reproduced the plan and draft files, and Workbench compiled the briefing action with zero errors in session `logs_2026-11-20_18-49-51`. Pregame timing, selected visibility for remote and joining players, and the live cache capture remain shared gameplay checks.


## Playable character ancestry and role labels

The batch can follow a reviewed legacy playable wrapper to its registered stock parent. For a Units role it compares the full resulting Dev ancestry, including any prefab shared through the stock GUID. The published Core US base overlay matches the Units Dev overlay byte for byte. Authored `m_sName` labels are migrated to the native editable-character name. The published Playable Component and its TilW extension have no `m_name` field. The converter removes that obsolete field from otherwise supported placements and retains their inherited native labels.

The rule approved 76 additional mission-owned loadouts and reproduced the 187 previously approved outputs unchanged. The full pass cleared 31 roster placement blockers. Direction Found now generates 39 playable slots, including its custom gunner, VDV squad leader and machine-gunner prefabs. The audit holds the draft on the M113A3 APC and violet RDG-2 smoke. The current APC GUID resolves to an M113A2 and the smoke GUID has no installed registration. The two catalog runs reproduced the same drafts and diagnostics.
