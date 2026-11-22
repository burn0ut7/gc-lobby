"""Verify archived terrain parents that the engine loads by path."""
from hashlib import file_digest

BYSTRANY_LEGACY_PARENT = '{E9D1005D38015A3C}worlds/Bystrany/GC_Bystrany.ent'
BYSTRANY_NATIVE_PARENT = '{7A888BF1C248E2E6}Worlds/Bystrany/Bystrany.ent'
BYSTRANY_MAP_ENTITY = '''SCR_MapEntity MapEntity1 : "{731564B66F91B107}Prefabs/World/Game/MapEntity.et" {
 "Map Geometry Data" "{0C931F860C09AC4C}Worlds/Bystrany/Bystrany.topo"
 "Satellite background image" "{10EB7E9261F936BC}UI/Textures/Map/Worlds/Bystrany/Bystrany_Rasterized_Enhanced.edds"
}
'''

LONGMOOR_PARENT = ('E90C30D273FF302E', 'Worlds/LongmoorTrainingArea/LongmoorTrainingArea.ent')
LONGMOOR_PROVIDER = 'Longmoor_61BD1072371B5470'
LONGMOOR_DATABASE_SHA256 = 'c288efb0afba367fcc6fe3c45ed4612022c106a80dcc61c293686cef2caae69d'
LONGMOOR_ARCHIVE_SHA256 = '85d1286c4bcc1850709ca9c9f131027a2e0288dacca8cf9ae0558551827a8a17'


def is_longmoor_subscene(path, text):
    guid, parent = LONGMOOR_PARENT
    return (path.lower().endswith('.ent')
            and text.strip() == f'SubScene {{\n Parent "{{{guid}}}{parent}"\n}}')


def has_reviewed_longmoor(providers):
    """Match the declared Workshop 3.2.1 archive whose own Game Master uses this parent."""
    database = next((path for path in providers if path.parent.name == LONGMOOR_PROVIDER), None)
    if database is None:
        return False
    archive = database.with_name('data.pak')
    if not archive.is_file():
        return False
    for path, expected in ((database, LONGMOOR_DATABASE_SHA256), (archive, LONGMOOR_ARCHIVE_SHA256)):
        with path.open('rb') as stream:
            if file_digest(stream, 'sha256').hexdigest() != expected:
                return False
    return True
