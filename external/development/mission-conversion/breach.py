"""Restore the Breach and Clear mission-owned loadout variants."""
from catalog import REF
from briefings import block_end
from groups import ROOT, group_layer, inheritance, legacy_member, only_path, scalar
from launchers import META_NAME, replace_reference

WORLD = 'worlds/Corax/Breach and clear/Breach_and_Clear.ent'
FOLDER = 'worlds/Corax/Breach and clear/No ETool/'
PLAYERS = WORLD.removesuffix('.ent') + '_Layers/Players.layer'
REVIEWED = {
    'AAT': 'f4c7d29d87dec64268bce907242dbf17cfaf753e',
    'AR': '95b7bafb7bdcd47b276392ef8cefffec6874fc11',
    'AT': 'accc034c744990ca6698ecbf8c51911fbf605383',
    'Rifleman': '944a8b650dcfd34b7de4e4b0f39c746b870b4b25',
    'SL': '557c737a9027cd65a862c8770e3d449f8e06083a',
    'SR': '11434cd66ff13405a48ea86aa167bd5ad4273f8e',
}


def stage_loadouts(source, dev, source_units, dev_units, stock):
    files, audit = {}, {}
    for role, oid in REVIEWED.items():
        path = FOLDER + f'Character_RHS_RF_MSV_Flora_{role}_NoETool_P.et'
        if source.files.get(path) != oid:
            raise ValueError('Breach and Clear loadout changed: ' + path)
        meta = source.read(path + '.meta')
        guid = META_NAME.search(meta)[2].upper()
        if (source.resources.get(guid) != [path] or guid in dev_units.resources
                or any(other != path for other in dev.resources.get(guid, []))):
            raise ValueError('Breach and Clear loadout identity collision: ' + guid)
        text = source.read(path)
        parent = REF.search(text.splitlines()[0])[1]
        if not inheritance(source_units, parent) or not inheritance(dev_units, parent):
            raise ValueError('Breach and Clear loadout parent needs review: ' + path)
        def relocate(match):
            ref_guid, ref_path = match.groups()
            if ref_guid.upper() in dev_units.resources:
                ref_path = only_path(dev_units, ref_guid)
            elif not stock.contains(ref_guid, ref_path):
                raise ValueError('Breach and Clear loadout dependency needs review: ' + ref_path)
            return '"{' + ref_guid + '}' + ref_path + '"'
        files[path] = REF.sub(relocate, text)
        files[path + '.meta'] = replace_reference(meta, META_NAME, guid, path)
        audit[path] = {
            'guid': guid, 'unitsParent': only_path(dev_units, parent),
            'legacyPlayableWithGroupOverride': legacy_member(source_units, parent, True),
        }
    return files, audit


def stage_roster(source, source_units, dev_units, stock, locally_owned, loadouts):
    if source.files.get(PLAYERS) != '1f7fdf9cbdcc2aad19c50f646c517d00e6c78511':
        raise ValueError('Breach and Clear roster changed')
    playable = {item['guid']: path for path, item in loadouts.items() if item['legacyPlayableWithGroupOverride']}
    ai_path, = [path for path, item in loadouts.items() if not item['legacyPlayableWithGroupOverride']]
    ai_guid = loadouts[ai_path]['guid']
    lines = source.read(PLAYERS).splitlines()
    placements, support = [], []
    for i, line in enumerate(lines):
        group = ROOT.fullmatch(line)
        if not group:
            continue
        stop = block_end(lines, i)
        placement = lines[i:stop + 1]
        if any(ai_guid in item for item in placement):
            source_chain = inheritance(source_units, group['guid'])
            if scalar(source_chain, 'm_bSetPlayable') != 1:
                raise ValueError('Breach and Clear group playability override changed')
            ai_ref = next(match[0] for item in placement for match in REF.finditer(item) if match[1] == ai_guid)
            placement = [item.replace(ai_ref, '').rstrip() for item in placement]
            # Reuse the RHS base already inherited by the playable Units Dev parent.
            parent = REF.search(inheritance(dev_units, group['guid'])[0].splitlines()[0])
            if not parent or parent[1] != 'AE252B0A692DA762':
                raise ValueError('Breach and Clear RHS group ancestry changed')
            transform = [item for item in placement if item.startswith((' coords ', ' angleY '))]
            name = f'GC_BreachSeniorRifleman{len(support) + 1}'
            support.append('\n'.join([
                f'SCR_AIGroup {name} : {parent[0]} {{',
                ' components {', '  AIFormationComponent "{507E9DC12F541FE6}" {',
                '   Enabled 0', '  }', ' }', *transform, ' m_bPlayable 0',
                ' m_aUnitPrefabSlots {', f'  "{{{ai_guid}}}{ai_path}"', ' }', '}', '',
            ]))
        placements.extend(placement)
    body, groups, slots = group_layer('\n'.join(placements), source_units, dev_units, stock, locally_owned,
                                      reviewed_members=playable)
    if (groups, slots, len(support)) != (7, 48, 2):
        raise ValueError('Breach and Clear slot or support roster changed')
    return body + ''.join(support), {'playableGroups': groups, 'unitPrefabEntries': slots, 'supportAI': len(support)}
