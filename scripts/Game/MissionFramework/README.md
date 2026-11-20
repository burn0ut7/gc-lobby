# Mission framework source

Start with the authored definitions, then follow their compiled forms into the runtime.

| Folder | Responsibility |
| --- | --- |
| `Events` | Mission events, random event groups and event options. |
| `Conditions` | Authored checks and their settings, including area targets and capture requirements. |
| `Actions` | Authored operations and their settings, including waypoint orders and respawn destinations. |
| `Flags` | Remembered mission flags and their options. Flag checks belong in `Conditions`; flag actions belong in `Actions`. |
| `Areas` | Passive polygon and radius entities placed in the world. Their authored checks live in `Conditions`. |
| `Interactions` | World interaction component and its native player use action. This is distinct from a mission event action. |
| `Runtime` | Compiled rules, evaluation, shared observations/trackers and execution support. These are not authoring choices. |

`GC_MissionScript.c` is the shared extension contract used by both custom script conditions and actions.

## Porting a teleport interaction

Replace TilW Mission Framework's `TILW_TeleportInteraction.et` with
`Prefabs/Logic/Mission/TeleportInteraction.et` at the interaction point.
Copy these values from the old user action to the new teleport component.

| TilW action field | GC teleport component field |
| --- | --- |
| Action Name | Action Name |
| Location Name | Destination Name (the existing world entity name) |
| Faction Keys | Factions (empty allows every faction) |
| Condition Flag | Required Flag (define the corresponding flag on the GC Lobby Manager) |

During Match, eligible players see the action if their faction is allowed.
A required flag disables the action until set. An empty Required Flag allows
use even when mission events are disabled. The prefab defaults to a two-metre
action context and a one-second hold. Copy mission-specific radius and duration
overrides in the Actions Manager. The server resolves the destination's current
origin on each use. The base-game teleport also moves an occupied vehicle if
the player activates the action from inside it.

## Finding the execution path

For a contested objective, add **Area Control Condition** to an event, select
the named area and faction, and set **Capture Seconds**. No nested Requirements
are needed. Living players count by default: 11 selected-faction players against
10 others advances capture; 10 against 10 freezes progress. An empty area also
pauses. The condition starts neutral (false), passes after capture completes,
and keeps its captured result during ties. An opposing advantage reverses
unfinished progress or starts timed loss of an already captured result.
Other factions are counted together; ignore nonparticipants under Advanced.
This is one event condition's capture memory, not a new faction-ownership system.
Existing Area Condition count, extraction and custom percentage checks remain
available for their original purposes.

Area-reference fields use the string-compatible `GC_AreaName` type with a normal
`UIWidgets.Auto` attribute. The Workbench DLL registers that type once with
`GC_AreaChoices`; components do not configure their own dropdowns or provider
markers. Scalar fields receive identity-based selection, including unnamed areas.
The provider enumerates editor entities on opening/focus and uses the runtime's
shared shape predicate: radius areas and closed polygon subclasses, including
setup zones and AO limits. Browsing never renames areas; selecting an unnamed
area assigns its name and the reference in one undoable editor action.

- `Events/GC_Event.c` describes events and random groups.
- `Conditions/GC_ConditionBase.c` and `Actions/GC_ActionBase.c` define authored contracts.
- `Runtime/GC_CompiledMissionRule.c` and `Runtime/GC_RuleCondition.c` hold compiled representations.
- `Runtime/GC_MissionRuleEngine.c` evaluates and dispatches them.
- `Runtime/GC_FrameworkModeActions.c` implements manager-owned execution.

Some files contain several related types: `Actions/GC_FrameworkActions.c` holds flag, event-control, message, random-reset, end-mission and respawn-ticket actions; `Conditions/GC_NumberConditions.c` holds count comparisons. Search by class name when following a serialized type.

Place new code with its responsibility. Keep authored settings beside their condition/action and shared execution state in `Runtime`; avoid a generic helpers folder. Folder changes do not rename serialized classes, fields or callback contracts. Keep tests and development evidence outside this release addon.
