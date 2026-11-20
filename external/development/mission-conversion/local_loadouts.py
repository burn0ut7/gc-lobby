"""Migrate explicitly or ancestrally playable character prefabs owned by a mission."""
from functools import lru_cache
from hashlib import sha256
from pathlib import PurePosixPath
import re

from briefings import block_end
from catalog import REF
from groups import inheritance, legacy_member

CHARACTER = re.compile(r'SCR_ChimeraCharacter : "\{[\da-fA-F]{16}\}[^"\n]+\.et" \{')
PLAYABLE = re.compile(r'^  PS_PlayableComponent "\{5EFCC1946F475F15\}" \{\n   m_bIsPlayable 1\n  \}\n', re.M)
LEGACY = re.compile(r'\b(?:PS|TILW)_\w+')
# The published legacy Core already supplied this empty FIA overlay. Units Dev
# carries the same field-for-field resource with the original identity.
EXTERNAL_PARENT_BRIDGES = {
    '7A9EE19AB67B298B': ('Prefabs/Characters/Factions/INDFOR/FIA/Character_FIA_base.et',
                         '6bba2563a432f879d466e9ebed1b312e3f1f5def486175ee71e378f72f3b1c05'),
}


@lru_cache(maxsize=4096)
def unchanged_units_parent(source_units, dev_units, resource):
    try:
        before = inheritance(source_units, resource)
        after = inheritance(dev_units, resource)
    except ValueError:
        return False
    if not before or not after or any(not CHARACTER.fullmatch(text.partition('\n')[0]) for text in (*before, *after)):
        return False
    native_before = tuple(PLAYABLE.sub('', text) for text in before)
    if any(LEGACY.search(text) for text in (*native_before, *after)):
        return False
    if native_before == after:
        return True
    if native_before != after[:-1]:
        return False
    parent = REF.search(before[-1].splitlines()[0])
    bridge = EXTERNAL_PARENT_BRIDGES.get(parent[1].upper()) if parent else None
    return (bool(bridge) and parent[2] == bridge[0] and dev_units.resources.get(parent[1].upper()) == [bridge[0]]
            and sha256(after[-1].strip().encode()).hexdigest() == bridge[1])


def collect(source, dev, source_units, dev_units, stock):
    """Return approved asset bodies by unique source GUID."""
    approved = {}

    def evaluate(guid, seen):
        if guid in approved:
            return approved[guid]
        paths = source.resources.get(guid, ())
        if len(paths) != 1 or guid in seen or guid in source_units.resources or guid in dev_units.resources:
            return None
        path = paths[0]
        if not path.lower().startswith(('worlds/', 'missions/')) or PurePosixPath(path).suffix.lower() != '.et':
            return None
        text = source.read(path)
        first = text.partition('\n')[0]
        if not CHARACTER.fullmatch(first):
            return None
        body, removed = PLAYABLE.subn('', text)
        if removed > 1 or LEGACY.search(body):
            return None
        if removed:
            lines = text.splitlines()
            if lines.count(' components {') != 1:
                return None
            start = lines.index(' components {')
            try:
                components = '\n'.join(lines[start + 1:block_end(lines, start)]) + '\n'
            except ValueError:
                return None
            if not PLAYABLE.search(components):
                return None
        parent = REF.search(first)
        resource, parent_path = parent[1].upper(), parent[2]
        if resource in source.resources:
            parent_asset = evaluate(resource, seen | {guid})
            if not parent_asset or parent_path != parent_asset[0]:
                return None
        elif resource in dev.resources or not parent_path.lower().startswith('prefabs/characters/'):
            return None
        elif resource in source_units.resources or resource in dev_units.resources:
            if not unchanged_units_parent(source_units, dev_units, resource):
                return None
            if not removed and not legacy_member(source_units, resource, False):
                return None
            canonical, = dev_units.resources[resource]
            if not canonical.lower().startswith('prefabs/characters/'):
                return None
            header = first.replace(parent[0], f'"{{{resource}}}{canonical}"', 1)
            body = header + body[len(first):]
        elif not removed or not stock.contains(resource, parent_path):
            return None
        if (dev.resources.get(guid) not in (None, [path])
                or (path in dev.files and body.strip() != dev.read(path).strip())):
            return None
        approved[guid] = (path, body)
        return approved[guid]

    for guid in source.resources:
        evaluate(guid, frozenset())
    return approved
