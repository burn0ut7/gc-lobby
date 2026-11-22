"""Generate an isolated Workbench prefab lookup from a migration plan."""
import argparse
import json
from pathlib import Path
import re

from briefings import guid


ADDON_ID = re.compile(r'[0-9A-Fa-f]{16}')
PREFAB = re.compile(r'\{[0-9A-Fa-f]{16}\}[^"\\|\x00-\x1f]+\.et')
BASE_GAME = '58D0FB3206B6F859'
ARLAND = '{A9806AF617972E97}worlds/Arland/Arland.ent'
WORLD = 'worlds/Lookup.ent'
PROBE_SCRIPT = """[ComponentEditorProps(category: "GC Testing")]
class GC_AssetLookupComponentClass : ScriptComponentClass
{
}

class GC_AssetLookupComponent : ScriptComponent
{
	//------------------------------------------------------------------------------------------------
	override void EOnInit(IEntity owner)
	{
		super.EOnInit(owner);
%s
	}

	//------------------------------------------------------------------------------------------------
	protected void Lookup(ResourceName authored)
	{
		Resource resource = Resource.Load(authored);
		if (!resource || !resource.IsValid())
		{
			PrintFormat("GC asset lookup | %%1 | INVALID", authored);
			return;
		}

		BaseContainer root = resource.GetResource().ToEntitySource();
		if (root)
			PrintFormat("GC asset lookup | %%1 | %%2", authored, root.GetResourceName());
	}

	//------------------------------------------------------------------------------------------------
	override void OnPostInit(IEntity owner)
	{
		super.OnPostInit(owner);
		SetEventMask(owner, EntityEvent.INIT);
	}
}
"""


def prepare(stage, references, addons):
    lobby = Path(__file__).resolve().parents[3]
    if stage.resolve().is_relative_to(lobby):
        raise ValueError('stage the test addon outside GC Lobby')
    dependencies = ' '.join(f'"{addon}"' for addon in dict.fromkeys([BASE_GAME, *addons]))
    project_id = guid(stage.resolve().as_posix() + '/project')
    world_id = guid(project_id + '/world')
    layer_id = guid(project_id + '/component')
    stage.mkdir()
    files = {
        'addon.gproj': f'''GameProject {{
 ID "GC_AssetLookup"
 GUID "{project_id}"
 TITLE "GC Asset Lookup"
 Dependencies {{
  {dependencies}
 }}
 Configurations {{
  GameProjectConfig PC {{
  }}
 }}
}}
''',
        WORLD: f'SubScene {{\n Parent "{ARLAND}"\n}}\n',
        WORLD + '.meta': f'''MetaFileClass {{
 Name "{{{world_id}}}{WORLD}"
 Configurations {{
  ENTResourceClass PC {{
  }}
 }}
}}
''',
        'worlds/Lookup_Layers/default.layer': f'''GenericEntity GC_AssetLookup {{
 components {{
  GC_AssetLookupComponent "{{{layer_id}}}" {{
  }}
 }}
 coords 600 20 600
}}
''',
        'Scripts/Game/GC_AssetLookupComponent.c': PROBE_SCRIPT % '\n'.join(
            f'\t\tLookup("{reference}");' for reference in references),
    }
    for name, contents in files.items():
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding='utf-8', newline='\n')


def read_results(console, references, *, diagnostics=False):
    observed = {reference: set() for reference in references}
    wrong_names = set()
    invalid_replication_ids = set()
    for line in console.read_text(encoding='utf-8-sig', errors='replace').splitlines():
        if match := re.search(r'\bSCRIPT\s+:\s+GC asset lookup \| (.+?) \| (.+)$', line):
            original, result = (part.strip() for part in match.groups())
            if original in observed and (result == 'INVALID' or PREFAB.fullmatch(result)):
                observed[original].add(result)
        elif diagnostics:
            if match := re.search(r'\bRESOURCES\s+\(E\): Wrong GUID/name for resource @"([^"]+)"', line):
                wrong_names.add(match[1])
            elif match := re.search(r'\bRPL\s+\(E\): Attempting to insert an entity with invalid '
                                    r'prefab resource GUID @"\{([\da-fA-F]{16})\}"', line):
                invalid_replication_ids.add(match[1].upper())
    incomplete = [reference for reference, results in observed.items() if len(results) != 1]
    if incomplete:
        raise ValueError('missing or inconsistent Workbench results: ' + ', '.join(incomplete))
    resolved = {reference: next(iter(results)) for reference, results in observed.items()}
    if not diagnostics:
        return resolved
    return {reference: {
        'resolved': result,
        'wrongGuidOrName': reference in wrong_names or result in wrong_names,
        'invalidReplicationGuid': any(key[1:17].upper() in invalid_replication_ids
                                      for key in (reference, result) if PREFAB.fullmatch(key)),
    } for reference, result in resolved.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    command = parser.add_mutually_exclusive_group(required=True)
    command.add_argument('--stage', type=Path, help='new test addon directory')
    command.add_argument('--console', type=Path, help='console.log from the probe session')
    parser.add_argument('--diagnostics', action='store_true',
                        help='include resource identity and replication errors observed in the console')
    parser.add_argument('--addon', type=str, action='append', default=[],
                        help='additional installed dependency GUID for the staged project')
    args = parser.parse_args()
    if any(not ADDON_ID.fullmatch(addon) for addon in args.addon):
        parser.error('additional dependencies must be 16-digit addon GUIDs')
    if args.diagnostics and not args.console:
        parser.error('--diagnostics requires --console')
    try:
        plan = json.loads(args.plan.read_text(encoding='utf-8-sig'))
        references = sorted({record['reference'] for record in plan['resourceRecoveryQueue']
                             if PREFAB.fullmatch(record['reference'])})
        if not references:
            raise ValueError('the migration plan contains no unresolved prefab references')
        if args.stage:
            prepare(args.stage, references, [addon.upper() for addon in args.addon])
            print(f'Staged {len(references)} prefab lookups in {args.stage / WORLD}')
        else:
            print(json.dumps(read_results(args.console, references, diagnostics=args.diagnostics), indent=2))
    except (OSError, KeyError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
