"""Read-only acceptance of conditional CDF input rules on an installed package.

The design consumer decides conditional write/requirement rules from confirmed
values and blocks unresolved operands. This checker never runs callbacks, opens
OA or writes data. For each allowed device it enumerates the declared finite
selector modes and requires every conditional decision in every mode to be
decidable. It also projects the P03 target state (execution promoted to CDF
callbacks) and requires every operand of a callback-mode conditional rule to be
a writable input. A passing report only covers this chain; it is not a general
design-ready statement, and value domains still come from P02/P05 facts.
"""

import argparse
import json
from copy import deepcopy
from pathlib import Path

from cadai.pdk_standard.interface import parameters
from cadai.pdk_standard.policy_modes import rows
from cadai.pdk_standard.readiness import callback_operand_gaps
from cadai.pdk_standard.workspace import select

PURPOSES = ('circuit', 'testbench')


def conditional(cdf, selected):
    return bool(cdf.get('rules')) or any(
        cdf['parameters'][name]['write'] == 'conditional'
        or cdf['parameters'][name]['requirement'] == 'conditional'
        or cdf['parameters'][name]['domain']['kind'] == 'conditional' for name in selected)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--library', default='smic28hkmg')
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    package = select(args.workspace, args.library, {})
    report = {'schema_version': '1.1.0',
              'scope': 'conditional CDF input rules only, with execution projected to CDF; '
                       'no OA, probe, callback or write',
              'library': args.library, 'revision': package.revision,
              'allowed_devices': 0, 'conditional_devices': 0, 'modes': 0,
              'operand_flags': [], 'nonfinite': [], 'unresolved': [], 'unsatisfiable': [],
              'devices': []}
    for key, row in sorted(package.document('device.json')['items'].items()):
        purpose = next((p for p in PURPOSES if row['use'].get(p) == 'allow'), None)
        if not purpose:
            continue
        report['allowed_devices'] += 1
        directory = row.get('dir')
        if not directory:
            continue
        cdf = package.document(directory + '/cdf.json')
        selected = parameters(cdf)
        if not conditional(cdf, selected):
            continue
        report['conditional_devices'] += 1
        promoted = deepcopy(cdf)
        promoted['execution'] = {'mode': 'cdf', 'order': sorted(
            name for name, value in promoted['parameters'].items()
            if value['write'] in {'allow', 'conditional'})}
        for name in callback_operand_gaps(promoted, selected):
            report['operand_flags'].append({'device': key, 'path': '/parameters/' + name,
                                            'reason': 'conditional_operand_not_writable'})
        table, status = rows(cdf)
        report['devices'].append({'device': key, 'mode_status': status,
                                  'modes': len(table) if status == 'ok' else None})
        if status != 'ok':
            report['nonfinite'].append(key)
            continue
        report['modes'] += len(table)
        for mode in table:
            if mode['unresolved']:
                report['unresolved'].append({'device': key, 'mode': mode['id'],
                                             'inputs': mode['unresolved']})
            missing = sorted(set(mode['explicit_required']) - set(mode['writable']))
            if missing:
                report['unsatisfiable'].append({'device': key, 'mode': mode['id'],
                                                'inputs': missing})
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'devices'}, ensure_ascii=False))
    failed = bool(report['operand_flags'] or report['unresolved'] or report['unsatisfiable'])
    raise SystemExit(1 if failed else 0)


if __name__ == '__main__':
    main()
