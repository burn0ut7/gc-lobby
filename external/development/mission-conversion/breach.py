"""Restore the Breach and Clear mission-owned loadout variants."""
from catalog import REF
from groups import inheritance, legacy_member, only_path
from launchers import META_NAME, replace_reference

WORLD = 'worlds/Corax/Breach and clear/Breach_and_Clear.ent'
FOLDER = 'worlds/Corax/Breach and clear/No ETool/'
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
