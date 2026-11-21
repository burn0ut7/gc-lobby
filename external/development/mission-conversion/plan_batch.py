"""Generate, audit and prioritize GC Missions conversion work in one pass."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import subprocess
import sys


def run_tool(name, args):
    script = Path(__file__).with_name(name + '.py')
    subprocess.run([sys.executable, '-B', str(script), *map(str, args)], check=True)


def summarize(inventory, review, limit):
    migration = inventory['batchMigration']
    candidates = defaultdict(list)
    for header, row in inventory['headers'].items():
        destination = row.get('destinationWorld', '').partition('}')[2]
        if migration['worlds'].get(destination, {}).get('status') == 'generated for file review':
            candidates[destination].append(header)

    ready, held = [], []
    for world, headers in sorted(candidates.items()):
        draft = migration['worlds'][world]
        issues = []
        remaining_resources = set()
        layer_repairs = set(draft['files']) if draft.get('existingLayerRepairs') else set()
        for header in sorted(headers):
            comparison = review['missions'][header]
            issues.extend(f'{header} — {issue}' for issue in comparison['issues']
                          if not layer_repairs or issue != 'Unresolved external resource references')
            for entry in comparison.get('unresolvedExternalReferences', []):
                reference = f"{{{entry['guid']}}}{entry['path']}"
                if layer_repairs and layer_repairs.isdisjoint(entry['referencedBy']):
                    remaining_resources.add(reference)
                else:
                    issues.append(reference)
            if comparison['status'] != 'compare' and not comparison['issues']:
                issues.append(f'{header} — comparison incomplete')
            advertised = draft.get('advertisedSlots', {}).get(header)
            if advertised is not None and advertised != draft['verifiedPlayableSlots']:
                issues.append(f"{header} — {advertised} advertised slots, "
                              f"{draft['verifiedPlayableSlots']} converted")
        record = {'world': world, 'launchers': sorted(headers),
                  'behaviorGaps': draft.get('remainingBehavior', [])}
        if draft.get('reviewedReplacements'):
            record['reviewedReplacements'] = sorted(draft['reviewedReplacements'])
        if remaining_resources:
            record['remainingResources'] = sorted(remaining_resources)
        if issues:
            held.append({**record, 'issues': sorted(set(issues))})
        else:
            ready.append(record)

    priority = defaultdict(lambda: {'worldsAffected': 0, 'soleBlockerWorlds': 0, 'examples': []})
    for world, row in sorted(migration['worlds'].items()):
        if row['status'] != 'blocked by conversion rules':
            continue
        by_rule = {}
        for blocker in row['blockers']:
            by_rule.setdefault(blocker['rule'], blocker)
        for rule, example in by_rule.items():
            entry = priority[rule]
            entry['worldsAffected'] += 1
            entry['soleBlockerWorlds'] += int(len(by_rule) == 1)
            if len(entry['examples']) < 3:
                entry['examples'].append({'world': world, **example})
    rules = sorted(priority, key=lambda rule: (
        -priority[rule]['soleBlockerWorlds'], -priority[rule]['worldsAffected'], rule))
    return {
        'source': inventory['source'], 'target': inventory['target'],
        'launcherFileChecks': dict(Counter(row['status'] for row in review['missions'].values())),
        'worldConversionChecks': migration['totals'],
        'suggestedBatch': ready[:limit], 'additionalDrafts': ready[limit:], 'heldDrafts': held,
        'unresolvedResources': review.get('unresolvedResources', []),
        'rules': [{'rule': rule, **priority[rule]} for rule in rules],
    }


def markdown(plan):
    lines = ['# GC Missions batch plan', '', f"Legacy revision `{plan['source']}`",
             f"Dev revision `{plan['target']}`", '',
             '## Drafts ready for source and Dev file review', '']
    selected = plan['suggestedBatch']
    if selected:
        for entry in selected:
            lines.append(f"- `{entry['world']}`")
            lines.extend(f'  - `{header}`' for header in entry['launchers'])
            lines.extend(f'  - Existing Dev layer repair in `{path}`'
                         for path in entry.get('reviewedReplacements', []))
            lines.extend(f'  - Unresolved reference in other layers or resources: `{resource}`'
                         for resource in entry.get('remainingResources', []))
            lines.extend(f'  - {gap}' for gap in entry['behaviorGaps'])
    else:
        lines.append('No drafts cleared the file checks.')
    lines.extend(['', f"Additional drafts that cleared file checks · {len(plan['additionalDrafts'])}",
                  '', '## Drafts held for file checks', ''])
    for entry in plan['heldDrafts']:
        lines.append(f"- `{entry['world']}`")
        lines.extend(f'  - {issue}' for issue in entry['issues'])
        lines.extend(f'  - Unresolved reference in other layers or resources: `{resource}`'
                     for resource in entry.get('remainingResources', []))
    if not plan['heldDrafts']:
        lines.append('None.')
    resources = plan['unresolvedResources']
    if resources:
        lines.extend(['', '## Unresolved resource registrations', '',
                      'Alternate paths show where an installed database registers the same resource ID. '
                      'Verify content and dependency coverage before approving a replacement.', ''])
        for entry in resources:
            lines.append(f"- `{{{entry['guid']}}}{entry['path']}` · {len(entry['launchers'])} launcher(s)")
            lines.extend(f"  - {registration['provider']} · `{registration['path']}`"
                         for registration in entry['registrations'])
            if not entry['registrations']:
                lines.append('  - No installed registration found for this ID.')
    lines.extend(['', '## Conversion rules to investigate', '',
                  '| Rule | Only reported blocker | Worlds affected |',
                  '| --- | ---: | ---: |'])
    for row in plan['rules']:
        rule = row['rule'].replace('|', '\\|')
        lines.append(f"| {rule} | {row['soleBlockerWorlds']} | {row['worldsAffected']} |")
    lines.extend(['', 'The JSON plan includes source locations and examples for each rule.'])
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--missions', required=True, type=Path)
    parser.add_argument('--units', required=True, type=Path)
    parser.add_argument('--source', default='origin/master')
    parser.add_argument('--dev', default='HEAD')
    parser.add_argument('--units-source', default='origin/master')
    parser.add_argument('--units-dev', default='HEAD')
    parser.add_argument('--dependency', action='append', default=[], type=Path)
    parser.add_argument('--base-database', required=True, type=Path)
    parser.add_argument('--workshop-addons', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='output directory outside the addons')
    parser.add_argument('--batch-size', default=10, type=int, help='maximum worlds in the suggested batch')
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error('--batch-size must be positive')
    lobby = Path(__file__).resolve().parents[3]
    dependencies = [lobby, args.units, *args.dependency]
    output = args.output.resolve()
    if any(output.is_relative_to(addon.resolve()) for addon in [args.missions, *dependencies]):
        parser.error('use an output directory outside the addons')
    try:
        units_head = subprocess.check_output(
            ['git', '-C', str(args.units), 'rev-parse', 'HEAD', args.units_dev + '^{commit}'], text=True
        ).splitlines()
        if len(set(units_head)) != 1:
            parser.error('check out the selected Units Dev revision for the resource audit')
        output.mkdir(parents=True, exist_ok=True)
        stage = output / 'drafts'
        inventory = output / 'inventory.json'
        review = output / 'review.json'
        common = ['--missions', args.missions, '--source', args.source, '--dev', args.dev,
                  '--base-database', args.base_database, '--workshop-addons', args.workshop_addons]
        run_tool('convert', [*common, '--batch', '--units', args.units, '--units-source', args.units_source,
                             '--units-dev', args.units_dev, '--stage', stage, '--report', inventory])
        providers = [item for path in dependencies for item in ('--dependency', path)]
        run_tool('review', [*common, *providers, '--inventory', inventory, '--draft-stage', stage,
                            '--report', review, '--summary', output / 'review.md'])
        plan = summarize(json.loads(inventory.read_text(encoding='utf-8')),
                         json.loads(review.read_text(encoding='utf-8')), args.batch_size)
        (output / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (output / 'plan.md').write_text(markdown(plan), encoding='utf-8')
        print(f"{len(plan['suggestedBatch'])} suggested worlds, {len(plan['heldDrafts'])} held drafts. "
              f"See {output / 'plan.md'}")
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
