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
