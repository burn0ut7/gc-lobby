"""Stage previously shipped Units loadouts with their original equipment and native parents."""
import argparse
from hashlib import sha256
from pathlib import Path
import re
from subprocess import CalledProcessError

from briefings import guid
from catalog import REF, Snapshot
from groups import SLOTS, only_path, stock_character
from install_batch import resource_databases, unresolved_resources
from launchers import META_NAME, replace_reference
from local_loadouts import CHARACTER, LEGACY, migrate_playability, unchanged_units_parent
from scenery import StockPrefabs


ARCHIVE = 'cda6d96940cd5fca0984087f7d2e58553d9bacb3'
MSV85_ARCHIVE = '08b79772e2f628b8d52dd6c1b5a0321e2d9ef6cc'
MSV85_DIRECTORY = 'Prefabs/Characters/Factions/OPFOR/USSR_Army/MotorRifle_MSV85A/'
MSV85_FORCED_ROLES = {
    'ECB5E6520057FF89': 'PlatoonLeader', '4318B010D29F4E99': 'DeputyPL',
    '60F78BEFD940423D': 'Medic', '756A54A830E2B54A': 'CoyCO',
    '84BFC57ACE3F2322': 'SquadLeader', 'D19FFA691FCF0810': 'AR',
    '093793A86646164C': 'AT', '8CACCAFD1EDEF893': 'AAT',
    '7A79814F28466BA6': 'SeniorRifleman', 'D0B3CA0BB493B929': 'Rifleman',
}
DIRECTORY = 'Prefabs/Characters/Factions/OPFOR/USSR_Army/MotorRifle_MSV80/Legacy/'
DEPUTY = '07299990D00B0823'
MEDIC = 'D1BA3D1164545A8D'
OLD_LEADER = 'DF8E65CA90B55E3B'
MORTAR_FDC = '74D7F430C3E7D1E5'
MORTAR_CREW = '917178E57AB126D9'
MORTAR_ROLES = {
    MORTAR_FDC: 'MortarSL',
    MORTAR_CREW: 'MortarCrew',
    '5888F75B9942CE7D': 'RTO',
    'A491F9108F01E180': 'Rifleman',
}
RIFLE_ROLES = {
    '70AE8D0AF2E319D6': 'AR',
    '1A5BEECAF606ACFC': 'SR',
    '8558FB262DBF1D64': 'CoyL',
    'CD7EDF8D8DF805C8': 'AT',
    '566506FE40CF16E5': 'AAT',
    'A5F4D7FEBDB57183': 'SL_NoRadio',
    '308B7EF638B4FD35': '1SG',
    'E5C3468BDF19AC4F': 'MG',
    '7ED89FF8122EBF62': 'AMG',
    'A8A47F001DA13B74': 'Sharpshooter',
    '3C7561AD348B68EA': 'PO',
    '2E93FDC7C413B281': 'FASL',
    'E45601E61BEC3F6F': 'TO',
}
ARCHIVED_ROLES = MORTAR_ROLES | RIFLE_ROLES
AUTHORED_DIRECTORY = 'Prefabs/Characters/Factions/OPFOR/USSR_Army/MotorRifle_1980/'
AUTHORED_PATHS = {
    OLD_LEADER: AUTHORED_DIRECTORY + 'Character_USSR_MSV80_PL_P.et',
    DEPUTY: AUTHORED_DIRECTORY + 'Character_USSR_MSV80_DPL_P.et',
    MEDIC: AUTHORED_DIRECTORY + 'Character_USSR_MSV80_Medic_P.et',
    **{role: AUTHORED_DIRECTORY + f'Character_USSR_MSV80_{name}_P.et'
       for role, name in ARCHIVED_ROLES.items()},
}
RESTORED = {
    OLD_LEADER: (guid('Units/legacy/' + OLD_LEADER), DIRECTORY + 'Character_USSR_Legacy_MSV80_PL.et'),
    DEPUTY: (DEPUTY, DIRECTORY + 'Character_USSR_Legacy_MSV80_DPL.et'),
    MEDIC: (MEDIC, DIRECTORY + 'Character_USSR_Legacy_MSV80_Medic.et'),
    **{role: (guid('Units/legacy/' + role), DIRECTORY + f'Character_USSR_Legacy_MSV80_{name}.et')
       for role, name in ARCHIVED_ROLES.items()},
}


def historical_prefabs(repo, stock, roles=RESTORED):
    archive = Snapshot(repo, ARCHIVE)
    files = {}
    for original in roles:
        resource, destination = RESTORED[original]
        path = only_path(archive, original)
        body = archive.read(path)
        header = body.partition('\n')[0]
        if not CHARACTER.fullmatch(header):
            raise ValueError('unsupported archived role ' + original)
        parent = REF.search(header)
        if AUTHORED_PATHS.get(parent[1].upper()) == parent[2]:
            native = RESTORED[parent[1].upper()]
        else:
            native = stock_character(parent[1], parent[2], stock)
        if not native:
            raise ValueError('unverified archived role parent ' + parent[2])
        updated, _ = migrate_playability(body + ('\n' if not body.endswith('\n') else ''), inherited_playable=True)
        if LEGACY.search(updated):
            raise ValueError('unsupported legacy role behavior ' + original)
        files[destination] = updated.replace(parent[0], f'"{{{native[0]}}}{native[1]}"', 1)
        files[destination + '.meta'] = replace_reference(archive.read(path + '.meta'), META_NAME, resource, destination)
    return files


def verified_roles(units, stock):
    if not stock:
        return {}
    verified = {}
    for roles in ((OLD_LEADER, DEPUTY, MEDIC), MORTAR_ROLES, RIFLE_ROLES):
        if any(units.resources.get(resource) != [path] for resource, path in (RESTORED[role] for role in roles)):
            continue
        for path, body in historical_prefabs(units.repo, stock, roles).items():
            if path not in units.files or units.read(path) != body:
                raise ValueError('archived loadout differs from the reviewed recovery: ' + path)
        verified.update({(original, path): RESTORED[original] for original, path in AUTHORED_PATHS.items()
                         if original in roles})
    return verified


def verified_group_rosters(source, units, stock, mission_owned, restored):
    """Recover a retired playable roster only for its exact authored group reference."""
    resource = '8D35DA8B89880FAF'
    authored = 'Prefabs/Groups/OPFOR/Russia/1980s/Motorised Rifles (BTR-Early)/P/Group_USSR_MSV80_MotRiflePHQ_P.et'
    canonical = 'Prefabs/Groups/OPFOR/Russia/1980s/MotorRifle_MSV80/Group_USSR_MSV80_MotRiflePHQ.et'
    base = ('8DE0C0830FE0C33D', 'Prefabs/Groups/OPFOR/Group_USSR_Base.et')
    if (not stock or not stock.contains(*base) or {resource, base[0]} & mission_owned
            or any(base[0] in snapshot.resources for snapshot in (source, units))):
        return {}
    for snapshot, digest in (
        (source, 'd80d98dcfa87eaecd297ecc824bf82b6cbb9e24a104388014d26eaec2f80948e'),
        (units, '6d789194d55d8dce2b0c4afe6b9994841c84dd2a49f1da160d59c0e4a66e6c22'),
    ):
        if (snapshot.resources.get(resource) != [canonical]
                or sha256(snapshot.read(canonical).strip().encode()).hexdigest() != digest):
            return {}
    archive = Snapshot(units.repo, ARCHIVE)
    roles = SLOTS.search(archive.read(only_path(archive, resource)))
    expected = [(role, AUTHORED_PATHS[role]) for role in (OLD_LEADER, DEPUTY, MEDIC)]
    if not roles or REF.findall(roles[1]) != expected or not all(role in restored for role in expected):
        return {}
    return {(resource, authored): expected}


def verified_aircrew_roles(source, units, stock, mission_owned):
    """Retain the legacy Lobby and Units combination observed in Workbench."""
    root = 'Prefabs/Characters/Factions/BLUFOR/US_Army/'
    pilot, crew = 'E9601333852C637F', '0812D60CBC9DD927'
    pilot_native = root + 'Deprecated/Character_US_HeliPilot_P.et'
    stock_pilot = ('42A502E3BB727CEB', root + 'Character_US_HeliPilot.et')
    stock_crew = ('15CD521098748195', root + 'Character_US_HeliCrew.et')
    radio = ('9B6B61BB3FE3DFB0', 'Prefabs/Items/Equipment/Radios/Radio_ANPRC77.et')
    radio_overlay = 'Prefabs/Items/Equipment/Radios/US/Radio_ANPRC77.et'
    stock_assets = (stock_pilot, stock_crew, radio)
    if (not stock or any(not stock.contains(*asset) for asset in stock_assets)
            or {pilot, crew, *(guid for guid, _ in stock_assets)} & mission_owned
            or any(guid in checkout.resources for checkout in (source, units)
                   for guid in (crew, stock_pilot[0], stock_crew[0]))
            or source.resources.get(pilot) != [pilot_native] or units.resources.get(pilot) != [pilot_native]
            or source.resources.get(radio[0]) != [radio_overlay] or units.resources.get(radio[0]) != [radio_overlay]):
        return {}
    body = units.read(pilot_native)
    if (source.read(pilot_native) != body or sha256(body.strip().encode()).hexdigest()
            != '756dca3a36bff9441f07762428a167041981a2bbb7ef44ce57811801676cb5b1'):
        return {}
    radio_body = units.read(radio_overlay)
    if (source.read(radio_overlay) != radio_body or sha256(radio_body.strip().encode()).hexdigest()
            != '42df5362f470cd116120441fa05b82b57759ae231f94336079f7e66903873de4'):
        return {}
    return {
        (pilot, root + 'Character_US_HeliPilot_P.et'): (pilot, pilot_native),
        (crew, root + 'Character_US_HeliCrew_P.et'): stock_crew,
    }


def verified_soviet_aircrew_roles(source, units, stock, mission_owned):
    root = 'Prefabs/Characters/Factions/OPFOR/USSR_Army/'
    pilot = 'FFE45AFCB29CD014'
    pilot_path = root + 'Character_USSR_HeliPilot_P.et'
    parent = ('A62FA97C4EC64F14', root + 'Character_USSR_HeliPilot.et')
    radio = ('54C68E438DD34265', 'Prefabs/Items/Equipment/Radios/Radio_R107M.et')
    radio_path = 'Prefabs/Items/Equipment/Radios/RU/Radio_R107M.et'
    if (not stock or not stock.contains(*parent) or not stock.contains(*radio)
            or {pilot, parent[0], radio[0]} & mission_owned):
        return {}
    for checkout in (source, units):
        if (parent[0] in checkout.resources or checkout.resources.get(pilot) != [pilot_path]
                or checkout.resources.get(radio[0]) != [radio_path]
                or sha256(checkout.read(pilot_path).strip().encode()).hexdigest()
                != '74054170b77c109e578d619395e4c0ff4ddbe7516be679c2e1085ace3586f990'
                or sha256(checkout.read(radio_path).strip().encode()).hexdigest()
                != '63859ab4eba2bad734bf42efb6adddf53504fb9bace7d62f2d7bc9128689f9e5'):
            return {}
    result = {(pilot, pilot_path): (pilot, pilot_path)}
    gunner = 'CF063A058ADD156E'
    gunner_path = root + 'Character_USSR_HeliGunner_P.et'
    if gunner not in mission_owned and all(
            checkout.resources.get(gunner) == [gunner_path]
            and sha256(checkout.read(gunner_path).strip().encode()).hexdigest()
            == '8977a2e7e7cb708165746dc8d22c337bf88ca948b1502151173035d570d8c95f'
            for checkout in (source, units)):
        result[(gunner, gunner_path)] = (gunner, gunner_path)
    return result


def verified_msv85_wrappers(source, units):
    archive = Snapshot(units.repo, MSV85_ARCHIVE)
    result = {}
    for resource, paths in archive.resources.items():
        if len(paths) != 1:
            continue
        path, = paths
        if not path.startswith(MSV85_DIRECTORY + 'P/') or not path.endswith('_P.et'):
            continue
        body = archive.read(path)
        header, _, fields = body.partition('\n')
        if not CHARACTER.fullmatch(header) or not re.fullmatch(
                r' ID "[\dA-F]{16}"\n components \{\n'
                r'  PS_PlayableComponent "\{5EFCC1946F475F15\}" \{\n'
                r'   m_bIsPlayable 1\n  \}\n \}\n\}', fields.strip('\n')):
            continue
        parent = REF.search(header)[1].upper()
        # The wrapper only opted the existing loadout into slot selection.
        # Require the archived, published Units and Dev loadout chains to agree.
        if not (unchanged_units_parent(archive, units, parent) and unchanged_units_parent(source, units, parent)):
            continue
        native = (parent, only_path(units, parent))
        meta = META_NAME.search(archive.read(path + '.meta'))
        if not meta or meta[2].upper() != resource:
            raise ValueError('unexpected archived role metadata: ' + path)
        for authored in (path, MSV85_DIRECTORY + Path(path).name, meta[3]):
            result[(resource, authored)] = native
    return result


def verified_msv85_forced_roles(source, units, owned):
    prefix = MSV85_DIRECTORY + 'Character_USSR_MSV85A_'
    result = {}
    for identity, role in MSV85_FORCED_ROLES.items():
        # The published loader resolves the stale SquadLeader_P path to the SVD GUID.
        effective = {'SquadLeader': 'SVD', 'CoyCO': 'CoyCO_P'}.get(role, role)
        if (identity not in owned and source.resources.get(identity) == [prefix + effective + '.et']
                and unchanged_units_parent(source, units, identity)):
            result[identity] = (prefix + role + '_P.et', only_path(units, identity))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--units', required=True, type=Path)
    parser.add_argument('--base-database', required=True, type=Path)
    parser.add_argument('--workshop-addons', required=True, type=Path)
    parser.add_argument('--dependency', action='append', type=Path, default=[])
    parser.add_argument('--stage', required=True, type=Path)
    args = parser.parse_args()
    try:
        units = Snapshot(args.units, 'HEAD')
        stock = StockPrefabs(args.base_database)
        output = {path: text.encode() for path, text in historical_prefabs(args.units, stock).items()}
        for resource, path in RESTORED.values():
            if units.resources.get(resource) not in (None, [path]):
                raise ValueError('Units already registers ' + resource + ' at another path')
        for path, body in output.items():
            if path in units.files and units.read(path).encode() != body:
                raise ValueError('Units already has a differing ' + path)
        checkouts = [Snapshot(path, 'HEAD') for path in args.dependency]
        missing = unresolved_resources(output, units, checkouts,
                                       resource_databases(args.base_database, args.workshop_addons))
        if missing:
            raise ValueError('unresolved archived loadout references: '
                             + ', '.join(f'{{{guid}}}{path}' for guid, path in sorted(missing)))
        stage = args.stage.resolve()
        if stage.is_relative_to(args.units.resolve()):
            raise ValueError('use a staging directory outside the Units addon')
        for path, body in output.items():
            target = stage / path
            if target.exists() and target.read_bytes() != body:
                raise ValueError('staging directory already contains a differing ' + path)
        for path, body in output.items():
            target = stage / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        print(f'Checked and staged {len(output) // 2} historical Units prefabs')
    except (OSError, ValueError, CalledProcessError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
