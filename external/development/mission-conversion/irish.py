"""Assemble Irish Checkpoint with the reviewed stock scene and outcome rules."""
import re

from boundaries import convert_boundaries
from briefings import block_end, guid
from catalog import REF
from industrial import outcome_mode
from launchers import launcher
from markers import convert_layer

WORLD = 'worlds/arc/IrishCheckpoint/IrishCheckpoint.ent'
HEADER = 'Missions/arc/IrishCheckpoint.conf'
LAYERS = WORLD.removesuffix('.ent') + '_Layers/'
DEFAULT = LAYERS + 'default.layer'
BOUNDARIES = LAYERS + 'default_GC_Boundaries.layer'
REVIEWED = {
    WORLD: '2022ce0dd10f9cdf2fbdf3c195a852aedcae47b3',
    DEFAULT: 'ab9729d52d2bde5e4955be25f94cec0f9d8eb40e',
    LAYERS + 'briefing.layer': 'a9a3cf46c96c9ac2e84671716498c9fee21e3c7e',
    LAYERS + 'checkpoint.layer': '6a7288edfb3acdf4fbed29303164040e48287ddf',
    LAYERS + 'vehicles.layer': '643e718a9f8541188717c3ca795768e4d30327a3',
}


def boundaries(text):
    start = text.index(' coords 2837.178 63.071 2054.343')
    lines = text[start:].splitlines()
    stop = block_end(lines, 1)
    points = ['\n'.join(lines[i:block_end(lines, i) + 1]) for i in range(2, stop)
              if re.fullmatch(r'   ShapePoint "\{[\dA-F]{16}\}" \{', lines[i])]
    if len(points) != 5:
        raise ValueError('Irish Checkpoint IRA polygon changed')

    # Two lobes cross between the first and fourth edges. Retain their
    # vertices and connect them at points 5 cm away from the crossover.
    def point(label, coords):
        return f'   ShapePoint "{{{guid(WORLD + "/ao/" + label)}}}" {{\n    Position {coords}\n   }}'

    corrected = [
        points[0],
        point('first-before', '-456.77346 0 -552.79579'),
        point('fourth-before', '-456.72036 0 -552.74960'),
        *reversed(points[1:4]),
        point('first-after', '-456.67373 0 -552.80319'),
        point('fourth-after', '-456.72683 0 -552.84939'),
        points[4],
    ]
    body = text[:start] + '\n'.join([*lines[:2], *corrected, *lines[stop:]])
    output, audit = convert_boundaries(body, allow_public_faction_color=True)
    if sorted(detail.get('vertices', 0) for detail in audit.values()) != [5, 9]:
        raise ValueError('Irish Checkpoint boundaries need review')
    return output, audit


def assemble(source, dev, stock, preset, rules, staged):
    for path, oid in REVIEWED.items():
        if source.files.get(path) != oid:
            raise ValueError('Irish Checkpoint source changed: ' + path)
    world, _, files = launcher(source, dev, HEADER)
    if world != WORLD:
        raise ValueError('Irish Checkpoint launcher resolves to a different world')
    files[world] = source.read(world)
    players = LAYERS + 'players.layer'
    files[players] = staged[players]
    for name in ('vehicles.layer', 'checkpoint.layer'):
        path = LAYERS + name
        files[path] = source.read(path)
        for resource, resource_path in REF.findall(files[path]):
            if not stock.contains(resource, resource_path):
                raise ValueError('Irish Checkpoint scene dependency changed: ' + resource_path)

    briefing = LAYERS + 'briefing.layer'
    text = source.read(briefing)
    files[briefing], icons = convert_layer(text[text.index('$grp PS_ManualMarker '):])
    text = source.read(DEFAULT)
    files[BOUNDARIES], audit = boundaries(text)
    lines = text.splitlines()
    start = lines.index('    SCR_Faction "{633D460D559977CA}" {')
    faction = '\n'.join(lines[start:block_end(lines, start) + 1])
    children = f''' {{
  SCR_FactionManager {{
   ID "6ADC9BB66394BE46"
   Factions + {{
{faction}
   }}
  }}
 }}
'''
    files[DEFAULT] = outcome_mode(WORLD, 'GC_IrishMode', preset, text, stock, rules,
                                  'GC_IrishStartingZone', children)
    return files, audit, icons
