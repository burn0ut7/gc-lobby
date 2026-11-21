# RHS load-only experiment

The fixture now includes production nested-reference collection, developed from the [attribute-reference experiment](#attribute-reference-extension). Current reproduction requires explicitly loading `GCAttributeContentTest` from the sibling `rhs-attribute-content` addon as well as RHS Core. The original seven-addon RHS-only run below is historical evidence from commit `3ebc9e9`; the extended run uses eight loaded addons. Neither content addon was added to the fixture's declared dependencies.

Passed on 2026-11-16 with a dedicated server and two separate local diagnostic clients launched by Workbench's stock Dedicated Server Tool. The server instantiated the placed RHS rifle, both clients resolved its prefab, both received player-controller ownership, and the server reported `Players connected: 2 / 2` and both `OnPlayerAuditSuccess` callbacks. Repeated samples retained two players for more than a minute. This fixture is development-only and excluded from Workshop releases with the rest of `external`.

## What was tested

`addon.gproj` declares only GC Lobby Dev (`6A115D5DFF7ED870`); GC Lobby declares only the base game (`58D0FB3206B6F859`). Neither declares RHS. The world nevertheless places the RHS M4A1 prefab `{5961913EE3FF9467}Prefabs/Weapons/Rifles/M4A1/Rifle_M4A1_BLOCK_0.et`. RHS is an explicit startup requirement for this mission, not an addon dependency of GC Lobby or the fixture.

Workbench was opened on this isolated project with `-addons RHS_Core`. Its loaded set contained exactly seven addons: core, base game, GC Lobby Dev, this fixture and the three RHS addons. No broad authoring collection was loaded. The production **GC → Create Mission Data → Refresh metadata only** action saved the world and updated [the native mission header](Configs/Missions/RhsLoadOnly.conf). Readback from disk showed:

1. The placed rifle's `m_aAddonIds` contains `RHS_Core`.
2. `m_aReferencedAddonIds` contains `RHS_Core`.
3. `m_aLoadedAddons` maps that name to GUID `595F2BF2F44836FB`.
4. Both RHS content packs are recorded as loaded but not directly referenced by a placed prefab. The installed RHS Core project declares them as dependencies, so they belong in the required closure.

| Addon ID | GUID | Selection reason |
|---|---|---|
| GCRhsLoadOnlyTest | 7E161126A01D0001 | Mission owner (not inferred from placed-prefab references) |
| GlobalConflictsLobbyDev | 6A115D5DFF7ED870 | Fixture dependency and placed content |
| RHS_Core | 595F2BF2F44836FB | Placed rifle source addon |
| RHS_Content_01 | 1337C0DE5DABBEEF | RHS Core dependency |
| RHS_Content_02 | BADC0DEDABBEDA5E | RHS Core dependency |

The stock dedicated tool forwards **all currently loaded non-vanilla addon IDs**, rather than reading our metadata. The observed startup selection on the server and both clients was:

```text
-addons GlobalConflictsLobbyDev,GCRhsLoadOnlyTest,RHS_Content_01,RHS_Content_02,RHS_Core
```

Thus this experiment verifies explicit load-only startup and the metadata association separately. It does not implement automatic metadata-to-server orchestration. A production selector must read the saved header, map referenced IDs through `m_aLoadedAddons`, add the mission owner and native dependency closure, and reject unresolved/incomplete inputs. Workshop server configuration identifies a mod by its addon GUID (`modId`); this diagnostic launch used addon names and already installed resources instead of a Workshop server JSON configuration. Do not confuse prefab GUID `5961913EE3FF9467` with mod GUID `595F2BF2F44836FB`.

## Reproduce

1. Install RHS Core and its two content packs. Open this `.gproj` with `-addons RHS_Core,GCAttributeContentTest`, and make the base game, installed addons, local addons and this `external/development` directory discoverable through addon directories. Do not add RHS or the attribute content addon to the fixture's project dependency list.
2. Open `Worlds/RhsLoadOnly.ent`. Refresh metadata with the production action above and inspect the saved header. Ordinary Ctrl+S does not refresh this metadata.
3. In Dedicated Server Tool, select `{7E161126A01D0002}Worlds/RhsLoadOnly.ent`, `ArmaReforgerServerDiag.exe`, and `ArmaReforgerSteamDiag.exe` for peers. Enable Run Peers and two separate peer entries with auto-join and separate profile suffixes. The tested tool settings used `-client`, reconnect and timeout-disable for local diagnostic clients.
4. Set `-gcRhsProbe <unique-run-id>` in server and both peer parameters. Use dedicated test profiles to separate logs. Append a quoted `-addonsDir "<comma-separated addon directories>"` to those parameters when the stock plugin's unquoted path list contains spaces; the test logs confirmed the final argument took effect.
5. Start through the tool's **Run** button. Verify server and both client logs, not merely running processes: eight expected addons for the current fixture, `rhsResource=1` on all three, `rhsEntity=1` on the server, two audit callbacks, two owned client controllers, and sustained `players=2`. Also check the attribute evidence below.
6. Stop through the same tool and verify all three owned processes exit. Restore previous settings and editing project.

The observer runs only with the explicit CLI flag, performs no spawning or ownership changes, samples at five-second intervals for three minutes, and removes its callback on deletion. Clients in this empty lobby are spectators near the origin. The distant rifle is absent from their name lookup (`rhsEntity=0`), so client entity visibility/streaming is **not** asserted. Each client explicitly resolves the RHS prefab (`rhsResource=1`), while the server checks the actual placed entity.

## Evidence and limits

[Process observations](evidence/processes.json) record server PID 43780 and client PIDs 50964 and 32124, exact startup arguments and separate profiles for run `rhs-local-02`. The server password is redacted. [Server](evidence/server.txt), [client 1](evidence/client1.txt) and [client 2](evidence/client2.txt) contain selected original console lines and line numbers. Full local logs remain in the recorded profile directories. The initial run `rhs-local-01` also connected both clients; it exposed the client name-lookup limitation, prompting the explicit resource-resolution check in the repeat run.

Workbench 1.9.0.37 required a versioned multiplayer native adapter repair in the separate Reforger MCP repository. Its callback tests passed for current and legacy profiles; real settings writes, nested peer settings, process observation, launch and stop were exercised. GC Lobby and this test addon do not depend on MCP at runtime.

The historical schema-1 metadata excluded other subscenes, runtime spawns and expanded inventory/random variants. Schema 2 adds nested references as described below. It is content evidence, not a complete dependency guarantee. The existing saved-source launcher remains fail-closed for unsupported packed/scripted resources; this test does not bypass that contract. No mission-transition pruning, remote players, clean-machine download, Workshop publication, production server JSON launch or rifle gameplay/visual test was performed. The clients logged an existing map icon mask error; Workbench reload logged a `PacejkaPlotTool` leaked-instance diagnostic. Neither prevented the observed joins, and neither is claimed fixed here.

## Attribute-reference extension

The original schema-1 exporter did not discover nested inventory/head dependencies. This paragraph describes the experiment at commit `ebbd6f3`, before the production integration below. Refreshing its native mission header with `AttributeCharacter` placed omitted `GCAttributeContentTest` from `m_aReferencedAddonIds`, although it lists the addon as loaded. The original generic prototype separately traversed those fields and wrote `attribute-evidence.conf`. The explicit probe now calls the production collector; automatic server selection is still separate. The full report is ignored because this one character expands to thousands of evidence rows. `verify_attributes.py` reads it back using the existing native-text parser and retains the target rows plus a full-report hash in `evidence/attributes.json`.

| Authored field path | Prototype coverage |
|---|---|
| `CharacterWeaponSlotComponent.WeaponTemplate` | RHS M4A1 owner found; real equipped inventory observed |
| `SCR_InventoryStorageManagerComponent.InitialInventoryItems[].PrefabsToSpawn[]` | Separate addon compass and bandage owners found; real inventory observed |
| `SCR_CharacterIdentityComponent.Identity.VisualIdentity.Head` | Separate addon head owner found; actual authored head observed on the factionless server character; resource resolution checked on all processes |
| `VisualIdentity.Body`, `BaseLoadoutManagerComponent.Slots[].Prefab`, nested attachment `Prefab`/`MagazineTemplate` | Same generic object/resource traversal; inherited references included, without field-name special cases |
| Resource-valued fields and arrays in linked `.et`/`.conf` files | Recursively expanded with a resource cache; repeated references share one row with the first origin |
| Models, textures, materials, sounds, texture-specialized fields and arbitrary script/string selectors | No complete ownership/expansion guarantee; not sufficient for a launchable manifest |

The compass and field dressing found in the installed RHS catalogue are base-game resources, not proof of RHS ownership. The separate `GCAttributeContentTest` addon supplies thin base-game-derived compass, bandage and head prefabs so their ownership is unambiguous. Its GUID is `7E161126A01D1001` and its only dependency is the base game. Neither it nor RHS is a dependency of the mission addon. The character root's own provenance does not contain this provider, which makes this a real attribute-only discovery test rather than a detection already satisfied by a placed prefab.

Run **Plugins → GC Tests → Probe attribute dependencies** explicitly after opening the fixture and reloading saved changes. The harness calls the addon-agnostic collector on `Prefabs/AttributeCharacter.et`, then saves its native report. The collector reads effective container values, walks inline objects/object arrays and prefab children, and follows resource names and GUID-prefixed strings into `.et`/`.conf` containers. It records source addons from inheritance and saves the live ID/GUID table. It never spawns game entities or modifies the scanned resources.

The probe now shares the production limits: 64 nested container levels, 1,000,000 visited containers, 50,000 distinct resources and 30 seconds. Exceeding a limit sets `m_sError` and leaves partial evidence. An empty error means only that traversal did not hit such a failure: `unresolved` and `non_container_not_expanded` rows still mean incomplete coverage. Do not promote this report to a complete launch manifest. Native `.ct`/other resource-container formats, specialized texture fields, dynamic faction identity choices, script-spawned resources and plain string selectors need further coverage. Broad traversal can also include editor previews/catalogue alternatives that are not used at runtime; it is conservative evidence, not a minimal mod set.

The first extended run `attributes-local-01` is retained in `evidence/attributes-first-*.txt`. Inventory objects existed on the server and both clients initially passed audit, but both then disconnected with `REPLICATION/SYSTEM_FAILURE`: the hand-authored test prefabs lacked stable entity `ID` fields. Explicit stable IDs were added before the repeat. The same run showed a faction-generated head replacing the authored one. The repeat uses a factionless test character to isolate the authored `VisualIdentity.Head`; this changes only the test prefab, not faction ownership rules or production behavior.

For the final repeat, run `verify_attributes.py --server <console.log> --clients <client1-console.log> <client2-console.log> --run attributes-local-02 --output evidence/attributes.json`. It requires four exact field/owner associations, ID-to-GUID mappings, no declared content dependency, all three resource loads, actual server inventory and authored head, two client controllers/audits, at least twelve five-second two-player samples in every process, and no replication-error or kick messages. Resource availability on a client does not by itself prove inventory/head visibility there; the server's actual entities are checked separately.

The repeat **passed** on 2026-11-16. [Saved ownership and verification evidence](evidence/attributes.json) records all four associations and hashes the final stopped logs. [Process observations](evidence/attribute-processes.json) identify dedicated server 36784 and separate clients 51140 and 22116. [Server](evidence/attributes-server.txt), [client 1](evidence/attributes-client1.txt) and [client 2](evidence/attributes-client2.txt) retain original line-numbered excerpts through sample 36. The stock tool stopped all three processes afterward with no survivors. These results establish the tested generic field traversal and load-only multiplayer path, not universal support for every resource type or dynamically selected mod.

## Production nested-reference collection

**Create Mission Data → Refresh metadata only** now saves schema-2 evidence in the mission header. `GC_MissionMetadataExport` uses `GC_MissionResourceReferences` to read each effective placed entity, its inline objects/arrays, and linked `.et`/`.conf` resources. This includes inherited equipment, world-instance overrides, playable-group prefab slots, inventory arrays, identity head/body fields, and resource fields in referenced configs. There are no RHS IDs or equipment field names in the collector. Existing placed-prefab grouping remains separate.

The referenced-addon union includes nested owners. `m_aLoadedAddons` maps native IDs to GUIDs. `m_bReferencedByPlacedPrefab` retains its original meaning; `m_bReferencedByResource` identifies the added evidence. `m_aResourceReferences` retains one sorted row per distinct addon-related or unresolved container, with its first field origin and source addons. Expanded vanilla-only containers and unexpanded non-container resources are counted in `m_iOmittedVanillaResourceCount` and `m_iUnexpandedResourceCount`. Ordinary base-game models/textures no longer fill the saved evidence rows, but they are not presented as resolved.

A per-scan cache breaks linked-resource cycles and avoids repeated expansion. Limits are 64 inline levels, 128 inheritance levels, 1,000,000 visited containers, 50,000 distinct resources and a shared 30-second capture deadline. The initial 100,000-container prototype limit was too small for the fixture's faction/catalogue graph and correctly refused to replace its previous header. Failed traversal produces no snapshot. Missing `.et`/`.conf` containers remain explicit `unresolved` rows. Saved `m_GC_GeneratedMetadata` is excluded so old evidence cannot become a new dependency.

Coverage is explicitly `authored_container_graph_partial`: script-selected assets, specialized texture fields, binaries, other subscenes and unsupported container formats are not universally resolved. Traversal may include unused catalogue/editor alternatives. This is conservative authoring evidence, not a complete or minimal launch manifest. The existing fail-closed launcher remains separate.

The world adds `AttributeInstanceCompass.et` only through an instance inventory override. `verify_nested_metadata.py` reads the saved header and requires its exact origin, inherited compass/bandage/head references, provider GUID, nested-owner union, preserved placed-prefab flags and unique rows. **GC Tests → Test nested references** exercises a two-config cycle, duplicates, a missing config, exclusion of generated evidence and an expired deadline. Helpers remain external. The earlier independent scanner was removed; the explicit attribute probe uses the production collector too.

Production validation passed: [native header checks](evidence/nested-metadata.json) and [Workbench test output](evidence/nested-workbench.txt). The saved fixture header contains 680 addon-related rows instead of 4,591 full graph rows (about 301 KB instead of 1.9 MB), with 1,107 expanded vanilla-only and 2,804 unexpanded resources counted separately. After script/resource reload, the engine read schema 2 and all four target references from the saved header. The direct automation world-open call crashed during setup; opening the world through stock editor menus succeeded. This editor-only metadata change does not repeat the earlier dedicated-server/client gameplay run.
# Metadata-driven dedicated launch

The 2026-11-16 queue experiment passed using the release server and two separate
release clients on this PC. The website's real importer accepted this saved
schema-2 header into a disposable database. An authenticated HTTP mission
request was claimed by the standalone GC worker, which selected GC Core plus
the mission's required addons and native dependency closure: **32 non-vanilla
addons**, including RHS and the independent attribute provider. This fixture's
`.gproj` still declares only GC Lobby, not RHS or the attribute provider.

The server acknowledged the requested scenario GUID, header world GUID, actual
loaded world and exact addon GUID set. The website recorded completion with the
same request/claim and computed plan hash. The stock dedicated tool's **Start
only clients** button launched both clients; both received player controllers
and passed audit. Each produced 36 consecutive two-player observations at its
configured five-second cadence without a checksum error or disconnect during
the captured interval.

See [verification](evidence/queue-launch.json), [database outcomes](evidence/queue-launch-outcomes.json),
[plan](evidence/queue-launch-plan.json), [receipt](evidence/queue-launch-receipt.json),
[process observations](evidence/queue-launch-processes.json), and the
[server](evidence/queue-launch-server.txt), [client 1](evidence/queue-launch-client1.txt)
and [client 2](evidence/queue-launch-client2.txt) log excerpts. Log hashes/byte
counts describe the observed prefix before deliberate test shutdown.
`verify_queue_launch.py` recomputes the plan hash and verifies these connections
against the live-run logs; its input paths are recorded in the evidence.

Limits: these are installed local addons, not Workshop download/remote-join
coverage. Client named-entity checks were outside streaming range and returned
no weapon/character; this run does not certify equipment visibility there.
Earlier dedicated attribute tests remain separate evidence. Diagnostic server
and client builds with Core produced a [script-checksum rejection](evidence/queue-launch-diagnostic-rejection.txt);
the successful pair used `ArmaReforgerServer.exe` and `ArmaReforgerSteam.exe`.
Script-checksum validation was not disabled to obtain the release result. The
dedicated tool retained its existing `-rpl-timeout-disable` and `-rpl-reconnect`
client settings; the observations do not test normal network-timeout handling.

## Direct PowerShell launch

On 2026-11-21 Windows PowerShell 5.1 launched two missions in succession using
one profile, the release dedicated server 1.9.0.37, and the same baseline JSON
containing GC Core Dev. The RHS fixture acknowledged 32 non-vanilla addons
(launch `85360472-ee1a-4753-910c-d800f610f9ba`). Arland Lobby acknowledged 27
(launch `2369c72b-06de-4416-80c5-9bc4d5cd1f0c`). Both passed scenario, world,
owner and exact addon-set verification. The second mission used schema 1.
Both local processes were stopped after their receipts. No clients joined.

An initial run loaded the fixture world from the Lobby checkout's nested
`external` directory. The launcher rejected the wrong world owner despite a
matching GUID set. For the passing runs we staged Lobby without `external` and
rebuilt the two standalone fixture resource databases using the stock
`-rebuild-database-only` tool.

A separate loopback-bound, password-protected Workshop experiment supplied only
Scenario Reload Menu 1.0.17 in `game.mods`. The release server downloaded it and
its native dependency Server Admin Tools 1.7.1 into the selected
`-addonDownloadDir` cache, mounted both and entered the online game state on a
stock scenario. A separate process given both `-config` and `-addons` exited
with the fatal diagnostic `-config cannot be used together with addons!`.
A published GC mission completing the receipt over Workshop and a remote join
remain unverified.

Run `powershell.exe -NoProfile -File Test-DirectLaunch.ps1` here for the
repeatable config-merge regression test. It uses the two real mission headers
and a stub executable to exercise Workshop arguments, receipt checking, version
pins, optional/required overlap, transitive dependencies and reuse of the same
generated config. It verifies the shared server JSON stays byte-identical.
It also copies the single batch file beside a test server executable and an
arbitrarily named server config, calls it from a different working directory
with the header as the first argument, and checks ambiguous config rejection
and explicit selection. The bundled entry point separately launched the real
RHS fixture on the release server with 32 verified addons.
