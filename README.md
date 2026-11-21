# GC Lobby

Lobby and role selection, briefing, match and after-action reporting for Arma Reforger.
Requires Arma Reforger. GC Lobby's mission framework is independent of TilW Mission Framework.

TilW is a research reference, not a runtime dependency or behavior specification.
GC Lobby uses its own mission logic and the base game's APIs; reference designs
must be evaluated against GC Lobby's requirements rather than reproduced by default.

Branding, editor categories and messages use **GC Lobby**. The Dev project
ID/mount is `GlobalConflictsLobbyDev`; its addon GUID remains `6A115D5DFF7ED870`.
Existing resource GUIDs, profile paths, database schema, `Lobby2` tool argument
keys and the `LOBBY2` compatibility define retain their existing contracts.

## Workbench authorization

Gray grants standing permission to use Reforger MCP and Arma Reforger Workbench for project development and verification. Proceed without asking again to launch or restart Workbench, open projects and worlds, reload scripts, start or stop Play, authorize development scripts, interact with the UI, capture screenshots, and run multiplayer checks. Preserve unsaved authored work before switching worlds or restarting.

## Addon contents

- `scripts/Game`, `scripts/GameCode`: gameplay and integrations with the game.
- `scripts/WorkbenchGame`: native mission-authoring menus and tools.
- `Configs`, `Prefabs`, `UI`, `Language`, `Sounds`: game resources; preserve GUIDs and metadata.
- `worlds/examples`: authoring examples, including the Arland scenario.
- `addon.gproj`: addon identity, dependencies and localization.

The [mission framework source guide](scripts/Game/MissionFramework/README.md)
maps events, conditions, actions, flags, areas, interactions and runtime execution.

`Prefabs/MP/Modes/GameMode_Lobby.et` is the single canonical game mode.
Save mission-specific settings and compositions as inherited prefabs under
`Prefabs/Templates` (or in the mission's own addon), rather than adding another
game mode. `GC_MissionSettings_US_USSR.et` supplies faction-specific parameters;
`GC_LobbyGameMode.et` supplies the supporting mission managers. Both
inherit the same game mode. Their resource GUIDs are preserved.

The game-mode entity exposes **GC Lobby Gameplay > Keep Weapon When Unconscious**
(`m_bKeepWeaponWhenUnconscious`), enabled by default. Set it on the mission's
game-mode instance before starting the world. It extends vanilla AI's weapon
stowing to non-AI characters while unconscious; live grenades or grenades being thrown still
drop. Disabling it preserves inherited handling. Other game modes are unaffected.
The separate Maeh-NoWeapDropUnCon addon is no longer required; loading it
independently can still impose its own behavior when this option is disabled.

Shared script hooks must preserve inherited behavior unless the active mode is
`GC_LobbyGameMode`. The formation source is a separate GC Lobby prefab, not an
override of the game's AI world. The bullet-base override adds only a telemetry
callback; recording requires the active GC Lobby session on the server.
Outside GC Lobby, the recovery observer only invalidates an existing GC Lobby
last-mission record so an intervening mission cannot restore an old map plan.

Open `addon.gproj` to work on the production addon. Create a mission in its own
addon, open a terrain or inherited mission, and use **Plugins > GC > Spawn GC Lobby Game Mode**.
Configure playable groups, briefing, mission events and a mission header before publishing.
Groups have an optional **GC Lobby > GC Description** field (`m_sGC_Description`)
for labels such as "Weapons team". Slotting cards show it below the callsign in
smaller text; blank descriptions are hidden. It does not replace the callsign.
Use **Plugins > GC > Open Wiki** to open the
[GC mission-making wiki](https://github.com/Global-Conflicts-ArmA/gc-reforger-missions/wiki)
in your default browser.

## Automatic admin login

Add player identity UIDs to the existing `game.admins` array in the main server
configuration (merge this fragment into its `game` object):

```json
"admins": ["00000000-0000-0000-0000-000000000001"]
```

Replace the example with real UIDs and restart the server after config changes.
GC Lobby checks the engine's `BackendApi.IsListedServerAdmin` after a successful
player identity audit and grants the native administrator role. The engine owns
list parsing and membership; its limit of 20 unique IDs applies. See
[Bohemia's server config documentation](https://community.bistudio.com/wiki/Arma_Reforger:Server_Config#admins).

No separate file or Server Admin Tools dependency is needed. `Lobby2Admins.json`
is no longer read; move any entries into `game.admins`. Other game modes do not
run this bootstrap. Admin actions still require the current native admin role;
logging out is respected until the next join, with no periodic re-promotion.
Promotion outcomes use the admin audit log.

## External components

`external/workbench/Preview UI.js` opens the shared GC tool-window design preview.
Its sample dependency, update and submission forms do not perform operations.
The UI is bundled with the existing Windows runtime; no separate installation is needed.

**Plugins > GC > GC Tools** combines **Submit mission**, **Update mods**, and
**Install external dependencies** in one window. **Open Wiki** remains separate.

For a source checkout, use **GC Tools > Install external dependencies**, or
open `external/workbench/Install.js` to view GC Tools. Lobby's `external` folder
contains the complete tool package and .NET runtime. Opening it does not require
GC Core, Units, Missions, Git, the dropdown DLL, or Reforger MCP. Each checkout
opens its own verified package; installed copies are not required for startup.
Do not start Workbench with `-noThrow`: it blocks protected script file/process
operations, including the GC Tools button. Remove that option and restart Workbench.
Save work and stop Play before installing the dropdown DLL.
Setup installs the dropdown DLL, profile Git helpers and Git for Windows.
Save and restart Workbench after installing; subsequent launches load it automatically.
Workbench NET API must be enabled in its saved preferences. Git installation needs internet access.
The archive includes its own .NET runtime; no developer tools are required.
Packed Workshop addons cannot launch these external Windows files.

`external/workbench/GCWorkbench.zip` is the packaged DLL and installer, with
a SHA-256 sidecar checked before extraction. Setup extracts to a unique Windows
temporary folder and installs under `%LOCALAPPDATA%/GCWorkbench`; it does not
overwrite a loaded DLL. To uninstall, extract the archive and run `Uninstall.bat`.
The package is generated from GC Lobby-owned sources in `external/source` using
`external/source/scripts/package-workbench-integration.ps1`. Reforger MCP is not
a build, install or runtime dependency.
Keep external Windows packages out of the published Workshop package.

Companion sources live in `external/development/lobby2` in this repository.
The [mission dependency prototype](external/source/mission-launcher/README.md)
experiments with recursive mission-resource metadata and isolated Windows server
launches. It blocks incomplete scans and is not a production restart worker.
The separate GC Workshop collection lives under
[its own repository](https://github.com/burn0ut7/gc-reforger-workshop) on `dev`,
installed beside Lobby as `gc-reforger-workshop` by GC Tools. It has
its own addon identity and contains only dependency metadata.
The standalone Workbench DLL installer and tools live in `external/source`:

- `tools`: Windows Git updates, mission branches and submission. The installer
  places these in the active Workbench profile under `GCWorkbench/tools`.
  Git operations require source checkouts; they cannot update packed Workshop addons.
- `integrations/StatsIngest` and `integrations/Contracts`: separately deployed
  statistics/history service and shared data contracts. Follow the service README
  for PostgreSQL setup, migrations and profile access; the mod does not host it.
- `integrations/MapDrawing`: separate optional addon requiring GC Lobby and
  Map Drawing (`656AC01634459D8D`); publish it independently if needed.
- `authoring`: detailed mission-authoring references.

Test projects, captures and historical development notes are not release inputs.
`resourceDatabase.rdb` is Workbench-generated local state, not authored content.
Publish through Workbench and inspect the produced package before release;
source-tree cleanup alone does not validate a Workshop package.

## Development content ownership

GC Tools targets `dev` for GC Core, GC Units and GC Missions. Missing Dev mods are selected by default on Update mods.
**Update** clones the selected repositories beside the GC Lobby checkout; existing
checkouts use the normal conflict-aware update flow. Incomplete existing folders
are preserved for review.

Use the local Dev addon checkouts (GlobalConflictsCoreDev, GlobalConflictsUnitsDev and
GlobalConflictsMissionsDev); release addons have different identities.

- GC Core Dev owns shared mod-pack functionality and changes.
- GC Units Dev owns unit/loadout prefabs and authored equipment. Load this addon
  in Workbench before saving; other addons' prefabs remain read-only inputs.
  New loadouts follow `Prefabs/Characters/Factions/<side>/<faction>/<team>/<name>.et`.
  In the save dialog, Faction selects `side/faction`, Team selects or creates the
  relative team folder (including existing nested folders), and Name selects the
  prefab filename. `(Root)` selects the faction folder itself. Folder categories
  preserve the source character's native faction affiliation.
  Character variants stay beside their parent. Customized weapons go into the
  team's `Weapons` subfolder, named `<loadout>_<gun>_<number>.et` (for example,
  `RU_Rifleman_M16A2_03.et`). Numbers start at `01`; existing matching versions
  are reused and changed versions get the next available number. Other customized
  gear goes into `Equipment`, using the same loadout/item/number convention. Existing `Prefabs/Loadouts` content remains
  accessible through `Loadouts/<faction>` and is not moved or assigned new GUIDs.
  The browser resolves one selected faction/team at a time; search narrows its
  loadouts while retaining inheritance context. Base templates and deprecated
  prefabs appear only when needed as ancestors or explicitly selected.
  New prefab GUIDs require stopping and restarting Workbench Play before loading
  them into the character. Saving retains current gear and never restarts Play.
- GC Missions Dev owns GC mission worlds, headers and mission-specific scripts
  and data. Save GC mission worlds there. Create Mission Data intentionally follows
  the selected world's owning addon, so other addons' worlds keep their own headers.

Core Dev owns system integrations and selects ACE through All in One Dev. Units Dev owns equipment
overrides and the shared gameplay baseline; Missions Dev owns mission/terrain data.
Units Dev depends on Core and Lobby Dev; Missions Dev depends on all three. The
GC Lobby framework stays separate. Its example worlds remain development examples,
not automatically moved into GC Missions. Submission updates contributors' forks
and requests approval into dev; it does not push directly to shared branches.
