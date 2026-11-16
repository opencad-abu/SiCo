"""Verify a fresh real capture through standard production, bounded reads and pending consent.

Use pdk_acceptance_smic28.py --capture-only first, then pass its --capture-dir.
This checker never confers design permission or runs a behavior probe.
"""

import argparse
import json
from pathlib import Path

from cadai.pdk_standard.jsonio import LIMIT, encode
from cadai.pdk_standard.provenance import verify
from cadai.pdk_standard.query import Queries
from cadai.pdk_standard.workspace import select
from pdk_acceptance_smic28 import LIBRARY, publish


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--capture-dir', required=True, type=Path)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    _, _, job, batches, elapsed = publish(args.capture_dir, args.workspace)
    if job['status'] != 'ready':
        raise RuntimeError(str(job))
    package = verify(select(args.workspace, LIBRARY, {}))
    files = list(package.base.root.rglob('*.json'))
    query = Queries()
    params, pages, maximum = 0, 0, 0
    request = {'library': LIBRARY, 'section': 'cdf', 'device': 'n09_ckt', 'include_unconfirmed': True}
    while True:
        response = query.read(package, request)
        maximum = max(maximum, len(encode(response)))
        params += sum(row.get('section') == 'parameters' for row in response['items'])
        pages += 1
        if response['complete']:
            break
        request['cursor'] = response['next_cursor']
    candidates = query.read(package, {'section': 'devices'})
    assert candidates['items'] == []
    assert maximum <= LIMIT and all(path.stat().st_size <= LIMIT for path in files)
    assert params > 0 and package.document('device.json')['items']['n09_ckt']['use']['circuit'] == 'unknown'
    report = {'schema_version': '1.0.0', 'scope': 'production/read/pending-confirmation; no design or simulation qualification',
              'library': LIBRARY, 'captured_symbol_cells': job['device_count'],
              'catalog_records': len(package.document('device.json')['items']),
              'resource_references': len(package.document('file.json')['items']),
              'n09_cdf_parameters': params, 'n09_cdf_pages': pages,
              'max_response_bytes': maximum, 'json_files': len(files),
              'max_file_bytes': max(path.stat().st_size for path in files),
              'unconfirmed_design_candidates': len(candidates['items']), 'design_ready': False,
              'batches': batches, 'publish_seconds': round(elapsed, 2),
              'manifest': str(package.base.root / 'package.json')}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
