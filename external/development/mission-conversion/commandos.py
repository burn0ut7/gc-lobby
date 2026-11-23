"""Read Commandos objective settings and authored references for the native port."""
import json
import re
from collections import Counter
from pathlib import Path, PurePosixPath

from briefings import QUOTED, block_end, guid, page, properties
from catalog import REF
from launchers import META_NAME, replace_reference


ROOT = 'worlds/arc/CommandosReforged/'
WORLD = ROOT + 'CommandosReforged.ent'
MODE_LAYER = ROOT + 'CommandosReforged_Layers/default.layer'
MODE_BLOB = 'a8a81a0ba9751f4758850cc3a04d50a3c5e51045'
OBJECTIVES_LAYER = ROOT + 'CommandosReforged_Layers/objectives.layer'
MANAGER_SCRIPT = 'scripts/game/arc/CommandosReforged/GC_CommandosManager.c'
MANAGER_BLOB = '6964da122cd0121fb80e9f3a8c7033c996bcf320'
MANAGER = '  GC_CommandosManager "{659C5DFB11CD048A}" {'
DEFECTOR_PAGE = ROOT + 'Prefabs/Defector_Brief.et'
DEFECTOR_PAGE_BLOB = 'a21735796600dc51e2bb01234ec53369667bca9c'
DEFECTOR_CONTROLLER = 'scripts/game/arc/CommandosReforged/GC_CommandosPlayerController.c'
DEFECTOR_CONTROLLER_BLOB = '47fb97f87f6d9ce42f635b697ef4fafd45bd5600'
CONTROLLER_PATH = ROOT + 'Prefabs/DefaultPlayerControllerMP_Coop_Commandos.et'
CONTROLLER_ID = '30319F56A7F20B33'
CONTROLLER_REF = f'"{{{CONTROLLER_ID}}}{CONTROLLER_PATH}"'
LEGACY_CONTROLLER = 'SCR_PlayerController : "{4E5DAC62A374950F}Prefabs/Characters/Core/DefaultPlayerControllerMP_Coop.et" {'
NATIVE_CONTROLLER = 'GC_LobbyPlayerController : "{FE3C731AAB2FB003}Prefabs/Characters/Core/LobbyPlayerController.et" {'
DOG_SCRIPT_BLOBS = {
    'GC_CommandosDogManager.c': 'c3ce395c2f621c565dad69f058a816351042b808',
    'GC_CommandosFootPrints.c': 'b3dce0ab9b95012a508345e18bb262a81a5cf280',
    'GC_SetInvisible.c': '440caa72266f02ad350b2ebaf9d6f169710ffd83',
}
TRACK_ASSET_BLOBS = {
    'Bootprint_Left.emat': '74507bc9f781d428b7dc8794d759401318e2423a',
    'Bootprint_Left.emat.meta': '2b32d28c10ddd9c77289d57ed626e4ee158c2782',
    'Bootprint_Right.emat': '500dd628cee4a02a7912f95776e3b7239c29d5c6',
    'Bootprint_Right.emat.meta': '3bfa03ec0b1f3fd02d5afcdd377bb2f404a3fe6b',
    'left.edds': '3e9d682220764c53b986e3930f5030f0b07d7f3b',
    'left.edds.meta': 'e1e977f8d6dd75fc9ecd4e3e020f1fff07cab8aa',
    'right.edds': 'b92f2c3f34b94b2ac1a739a13250633a721f7e0f',
    'right.edds.meta': 'b54e92c3dfc6be1065d5d715db37631af8f3afe7',
}
KINDS = {
    'GC_CommandosObj': 'interaction',
    'GC_CommandosDestroyed': 'destruction',
    'GC_CommandosTracker': 'tracker',
    'GC_CommandosDefectorDestroy': 'defector',
}
NATIVE_KINDS = {'interaction': 0, 'destruction': 1, 'tracker': 2, 'defector': 3}
TEXT_FIELDS = {'m_name', 'm_objectiveCompletedMsg', 'm_briefingDescription'}
OBJECTIVE_FIELDS = TEXT_FIELDS | {'m_markerPrefab', 'm_chance', 'm_blacklist'}


def objective_flags(name):
    key = guid('Commandos/objective/' + name)
    return f'Commandos_{key}_Active', f'Commandos_{key}_Complete'


def runtime_files(source):
    if (source.files.get(CONTROLLER_PATH) != '960f1a37192a7ce5a4efe77efded7d2520f46ba3'
            or source.files.get(CONTROLLER_PATH + '.meta') != '4c8f408c9df5e696f754c36e3a33caf91b3ec907'
            or any(source.files.get(MANAGER_SCRIPT.rsplit('/', 1)[0] + '/' + name) != blob
                   for name, blob in DOG_SCRIPT_BLOBS.items())):
        raise ValueError('Commandos controller or dog scripts changed')
    controller = source.read(CONTROLLER_PATH)
    if not controller.startswith(LEGACY_CONTROLLER + '\n'):
        raise ValueError('Commandos controller parent changed')
    result = {
        CONTROLLER_PATH: controller.replace(LEGACY_CONTROLLER, NATIVE_CONTROLLER, 1),
        CONTROLLER_PATH + '.meta': replace_reference(source.read(CONTROLLER_PATH + '.meta'),
                                                    META_NAME, CONTROLLER_ID, CONTROLLER_PATH),
    }
    for name, blob in TRACK_ASSET_BLOBS.items():
        path = ROOT + 'Assets/' + name
        if source.files.get(path) != blob:
            raise ValueError('Commandos track asset changed: ' + path)
        result[path] = source.read_raw(path) if path.endswith('.edds') else source.read(path)
    scripts = {*DOG_SCRIPT_BLOBS, 'GC_CommandosManager.c', 'GC_CommandosObjective.c', 'GC_CommandosObjRegister.c'}
    folder = Path(__file__).with_name('commandos-runtime')
    for name in sorted(scripts):
        result['Scripts/Game/arc/CommandosReforged/' + name] = (folder / name).read_text(encoding='utf-8')
    return result


def saved_text(value):
    if not re.fullmatch(QUOTED, value):
        raise ValueError('invalid Commandos objective text')
    return value[1:-1]


def render_manager(objectives, briefing):
    def field(name, value):
        return f'{name} {json.dumps(value, ensure_ascii=False)}'

    contents = briefing.body.replace('\n', '\n   ')
    lines = [MANAGER, '   ' + field('m_sDefectorPageTitle', saved_text(briefing.title)),
             '   m_sDefectorPageContent ' + contents, '   m_aObjectives {']
    for name, row in objectives.items():
        kind = row['kind']
        lines.extend([
            f'    GC_CommandosObjective "{{{row["identity"]}}}" {{',
            '     ' + field('m_sName', name),
            '     ' + field('m_sBriefing', row['briefing']),
            '     ' + field('m_sCompletionMessage', row['completionMessage']),
            '     ' + field('m_iCompletionDelay', row['completionDelaySeconds']),
            '     ' + field('m_eKind', NATIVE_KINDS[kind]),
            '     ' + field('m_sAvailabilityFlag', row['availabilityFlag']),
            '     ' + field('m_sCompletionFlag', row['completionFlag']),
        ])
        if kind == 'defector':
            lines.extend(['     ' + field('m_fChance', row['chance']), '     m_aExcludedRoles {',
                          *('      ' + json.dumps(role) for role in row['excludedRoles']), '     }'])
        lines.append('    }')
    return '\n'.join([*lines, '   }', '  }']) + '\n'


def definitions(source, mode):
    if (source.files.get(MODE_LAYER) != MODE_BLOB
            or source.files.get(MANAGER_SCRIPT) != MANAGER_BLOB):
        raise ValueError('Commandos objective authoring or script defaults changed')
    manager = mode.index(MANAGER)
    end = block_end(mode, manager)
    start = manager + 1
    if mode[start] != '   m_objectives {' or block_end(mode, start) != end - 1:
        raise ValueError('Commandos manager needs one authored objective list')
    rows = {}
    i = start + 1
    while i < end - 1:
        header = re.fullmatch(r'    (\w+) "\{([\dA-F]{16})\}" \{', mode[i])
        if not header or header[1] not in KINDS:
            raise ValueError('unknown Commandos objective type')
        stop = block_end(mode, i)
        fields = properties(mode[i + 1:stop], OBJECTIVE_FIELDS)
        name = saved_text(fields.get('m_name', ''))
        if not name or name in rows:
            raise ValueError('missing or duplicate Commandos objective name')
        active, complete = objective_flags(name)
        marker = REF.fullmatch(fields.get(
            'm_markerPrefab', f'"{{8B6159923D67F288}}{ROOT}Prefabs/Marker_Any.et"'))
        if not marker or source.resources.get(marker[1]) != [marker[2]]:
            raise ValueError('unresolved Commandos objective marker for ' + name)
        row = {
            'identity': header[2],
            'sourceOrder': len(rows),
            'kind': KINDS[header[1]],
            'availabilityFlag': active,
            'completionFlag': complete,
            'briefing': saved_text(fields.get('m_briefingDescription', '')),
            'completionMessage': saved_text(fields.get('m_objectiveCompletedMsg', '"The %1 has been neutralized!"')),
            'completionDelaySeconds': 60,
            'markerPrefab': marker[0][1:-1],
            'registrations': [],
        }
        if row['kind'] == 'tracker':
            row['markerUpdateSeconds'] = 45
        if row['kind'] != 'interaction':
            row['destroyAll'] = False
        if row['kind'] == 'defector':
            blacklist = fields.get('m_blacklist', '')
            if not re.fullmatch(rf'{QUOTED}(?: {QUOTED})*', blacklist) or fields.get('m_chance') != '0.5':
                raise ValueError('unreviewed Commandos defector eligibility for ' + name)
            row['chance'] = 0.5
            row['excludedRoles'] = [saved_text(value) for value in re.findall(QUOTED, blacklist)]
        elif {'m_blacklist', 'm_chance'} & fields.keys():
            raise ValueError('defector fields on a public Commandos objective')
        rows[name] = row
        i = stop + 1
    return rows


def audit_objectives(source, mode):
    objectives = definitions(source, mode)
    counts = Counter()
    disabled_overrides = []
    for path in sorted(source.closure(WORLD)):
        if not path.startswith(ROOT) or PurePosixPath(path).suffix not in ('.layer', '.et'):
            continue
        lines = source.read(path).splitlines()
        for i, line in enumerate(lines):
            registration = re.fullmatch(r' +GC_CommandosObjRegister "\{([\dA-F]{16})\}" \{', line)
            if registration:
                end = block_end(lines, i)
                if [field.strip() for field in lines[i + 1:end]] == ['Enabled 0']:
                    disabled_overrides.append({'file': path, 'line': i + 1, 'component': registration[1]})
                    continue
                fields = properties(lines[i + 1:end], {'m_objectiveName', 'm_registerMarker', 'm_markerName'})
                name = saved_text(fields.get('m_objectiveName', ''))
                marker = fields.get('m_registerMarker', '0')
                if name not in objectives or marker not in ('0', '1'):
                    raise ValueError('invalid Commandos registration in ' + path)
                record = {'file': path, 'line': i + 1, 'component': registration[1], 'marker': marker == '1'}
                if 'm_markerName' in fields:
                    record['markerLabel'] = saved_text(fields['m_markerName'])
                objectives[name]['registrations'].append(record)
            if path == OBJECTIVES_LAYER and line.lstrip().startswith('GC_CommandosInteraction '):
                fields = lines[i + 1:block_end(lines, i)]
                names = [saved_text(field.strip().removeprefix('m_objectiveName ')) for field in fields
                         if field.strip().startswith('m_objectiveName ')]
                if len(names) != 1:
                    raise ValueError('Commandos action needs one objective name')
                counts[names[0]] += 1
    interactions = {name for name, row in objectives.items() if row['kind'] == 'interaction'}
    if counts.keys() != interactions or any(not row['registrations'] for row in objectives.values()):
        raise ValueError('Commandos objective list and authored actions or registrations differ')
    for name, count in counts.items():
        objectives[name]['authoredActions'] = count
    if (source.files.get(DEFECTOR_PAGE) != DEFECTOR_PAGE_BLOB
            or source.files.get(DEFECTOR_CONTROLLER) != DEFECTOR_CONTROLLER_BLOB):
        raise ValueError('Commandos defector page or owner delivery changed')
    briefing = page(source.read(DEFECTOR_PAGE).splitlines()[1:-1], DEFECTOR_PAGE)
    return {
        'ordinaryDrawCount': 5,
        'totalDrawLimit': 8,
        'defectorDrawLimit': 1,
        'defectorAssignment': {
            'phase': 'Briefing',
            'faction': 'RHS_AFRF',
            'briefingDelivery': 'Player controller owner',
            'page': {'file': briefing.source, 'order': briefing.order,
                     'title': saved_text(briefing.title), 'serializedContent': briefing.body},
        },
        'definitions': objectives,
        'disabledRegistrationOverrides': disabled_overrides,
        'nativeManagerComponent': render_manager(objectives, briefing),
    }
