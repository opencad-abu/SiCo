"""Reviewed baseline maintenance updates with one recorded explicit overlay rebase.

Verified pdk_version/options and dependency refreshes belong to the complete
baseline, not to the cumulative workspace delta (standard C.4). An update
records one explicit rebase: the new baseline carries the corrected identity
and/or the live-re-verified dependency fingerprints, every delta change whose
recorded precondition cites a refreshed dependency is re-approved explicitly,
all logical documents are proven unchanged, and the baseline and overlay index
entries switch together atomically. Affected documents, devices and
confirmations are listed in the review and re-verified through the normal
follow-up flows; a fingerprint refresh never silently renews them. Readers
never silently accept a stale base digest.
"""

import math
import uuid
from copy import deepcopy
from pathlib import Path

from sicostate import project_directory

from . import VERSION
from .impact import confirmations, graph
from .jsonio import fail, read
from .package import locked
from .publication import invalidate
from .rebase import reapproved_names, rewrite
from .refresh import current_record, plan as refresh_plan, targets_from
from .review import validate_scope
from .update_store import Reviews
from .update_store import result as review_result
from .updates import difference
from .workspace import select

IDENTITY = ('pdk_version', 'options')
MAX_SCOPE_DEVICES = 10
MAX_SCOPE_CONFIRMATIONS = 8
MAX_REAPPROVED_LISTED = 20
EMPTY_PLAN = {'changed': {}, 'unchanged': [], 'devices': []}


def identity_from(args):
    """The validated identity fields carried by the request (possibly empty)."""
    identity = {}
    if 'pdk_version' in args:
        value = args['pdk_version']
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            fail('pdk_version must be a bounded nonempty string')
        identity['pdk_version'] = value.strip()
    if 'options' in args:
        options = args['options']
        if not isinstance(options, dict) or not options or len(options) > 16:
            fail('options must be a short nonempty mapping')
        for key, value in options.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                fail('Invalid process option name')
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)) and math.isfinite(value):
                continue
            if isinstance(value, str) and value and len(value) <= 256:
                continue
            fail('Process option values must be bounded scalar facts')
        identity['options'] = deepcopy(options)
    return identity


def request_plan(package, args, bridge):
    """The live refresh plan of a request (empty when nothing was requested)."""
    devices, resources = targets_from(args)
    if not devices and not resources:
        return EMPTY_PLAN
    return refresh_plan(package, bridge, devices, resources)


def re_verification_scope(package, plan):
    if not plan['changed']:
        return {'required': False}
    docs, device_of, usage = graph(package)
    affected_docs = {name for ref in plan['changed']
                     for name in usage.get(ref, {}).get('documents', ())}
    affected_devices = sorted({device for ref in plan['changed']
                               for device in usage.get(ref, {}).get('devices', ())})
    found = sorted(confirmations(package, affected_docs, docs))
    return {
        'required': True,
        'devices': affected_devices[:MAX_SCOPE_DEVICES],
        'devices_truncated': len(affected_devices) > MAX_SCOPE_DEVICES,
        'documents': len(affected_docs),
        'confirmations': found[:MAX_SCOPE_CONFIRMATIONS],
        'confirmations_truncated': len(found) > MAX_SCOPE_CONFIRMATIONS,
        'note': 'refreshed sources change the basis of these objects; re-collect and re-review them '
                'through the normal flows - the refresh does not silently renew them.',
    }


def scope_for(package, args, identity, plan):
    """The exact reviewed differences, rebase consequences and re-verification scope."""
    before = {key: deepcopy(package.manifest.get(key)) for key in identity}
    after = {**before, **identity}
    if after == before and not plan['changed']:
        fail('Baseline identity or source refresh already matches the requested values')
    rows = difference(before, after) if identity else []
    for ref, row in sorted(plan['changed'].items()):
        rows.append({'path': '/dependencies/' + ref + '/fingerprint',
                     'before': row['from'], 'after': row['to']})
    reapproved = reapproved_names(package, plan['changed'])
    overlay = getattr(package, 'overlay', None)
    rebase = {'required': False}
    if overlay:
        manifest = read(overlay / 'package.json')
        rebase = {
            'required': True,
            'overlay_revision': manifest['revision'],
            'base_revision': package.base.revision,
            'reapproved_changes': reapproved[:MAX_REAPPROVED_LISTED],
            'reapproved_truncated': len(reapproved) > MAX_REAPPROVED_LISTED,
            'checks': ['every change precondition (expected) is re-read against the new baseline',
                       'changes citing a refreshed dependency are re-approved under this answer',
                       'recorded dependency fingerprints must stay unchanged elsewhere',
                       'all logical documents are proven unchanged before the atomic switch'],
        }
    return {
        'library': args['library'], 'revision': package.revision, 'kind': 'baseline',
        'changes': [{'file': 'package.json', 'changes': rows}],
        'rebase': rebase,
        're_verification': re_verification_scope(package, plan),
    }


class Baseline:
    def __init__(self, workspace, environment, bridge=None):
        self.workspace, self.environment = Path(workspace), environment
        self.bridge = bridge
        self.confirmation = None
        self.previews = {}
        self.reviews = Reviews(workspace)

    def prepare(self, args):
        index = project_directory(self.workspace, 'ai/pdk-data') / 'index.json'
        if not index.is_file():
            fail('Baseline update requires a workspace PDK data index; run '
                 'prepare_pdk_data with a workspace baseline first',
                 'pdk_standard_unavailable')
        package = select(self.workspace, args['library'], self.environment)
        if package.revision != args['revision']:
            fail('Effective package changed; read again', 'pdk_update_conflict')
        identity = identity_from(args)
        plan = request_plan(package, args, self.bridge)
        if not identity and not plan['changed']:
            if plan['unchanged'] or plan['devices']:
                fail('No source changes to refresh: the requested objects still match their '
                     'declared fingerprints')
            fail('No baseline identity or source refresh requested')
        scope = scope_for(package, args, identity, plan)
        validate_scope(scope)
        if len(self.previews) >= 32:
            completed = next(
                (key for key, row in self.previews.items() if row.get('applied')), None
            )
            if completed:
                del self.previews[completed]
            else:
                fail('Too many pending baseline previews')
        ref = 'pdk-baseline:' + uuid.uuid4().hex
        result = review_result(ref, scope)
        evidence, digest = self.reviews.save(ref, args, scope)
        self.previews[ref] = {
            'args': deepcopy(args),
            'identity': identity,
            'plan': plan,
            'result': result,
            'scope': evidence,
            'digest': digest,
        }
        return deepcopy(result)

    def restore(self, ref):
        """Reconstruct a pending review without granting or replaying confirmation."""
        args, scope, digest = self.reviews.read(ref)
        package = select(self.workspace, args['library'], self.environment)
        receipt = self.reviews.receipt(ref)
        if receipt:
            return receipt
        if package.revision != args['revision']:
            fail('Concurrent PDK update or baseline change', 'pdk_update_conflict')
        identity = identity_from(args)
        plan = request_plan(package, args, self.bridge)
        if scope_for(package, args, identity, plan) != scope:
            fail('Review evidence changed', 'pdk_update_conflict')
        preview = {
            'args': args,
            'identity': identity,
            'plan': plan,
            'result': review_result(ref, scope),
            'scope': self.reviews.path(ref),
            'digest': digest,
        }
        self.previews[ref] = preview
        return deepcopy(preview['result'])

    def apply(self, args):
        ref = args['update_ref']
        with self.reviews.lock(ref):
            if ref not in self.previews:
                restored = self.restore(ref)
                if not restored.get('confirmation_required'):
                    return restored
            preview = self.previews[ref]
            receipt = self.reviews.receipt(ref)
            if receipt:
                return receipt
            answer = self.confirmation(preview['result']) if self.confirmation else None
            if not answer:
                return deepcopy(preview['result'])
            self.reviews.check(preview)
            if answer.get('decision') != 'confirm':
                result = {'schema_version': VERSION, 'status': 'cancelled', 'update_ref': ref}
                self.reviews.finish(ref, result)
                preview['applied'] = result
                return result
            if not answer.get('actor') or not answer.get('evidence_ref'):
                fail('Host confirmation lacks answer evidence')
            root = project_directory(self.workspace, 'ai/pdk-data', create=True)
            with locked(root):
                package = select(self.workspace, preview['args']['library'], self.environment)
                receipt = self.reviews.receipt(ref)
                if receipt:
                    return receipt
                if package.revision != preview['args']['revision']:
                    # An interrupted identical commit may already be effective.
                    if converged(package, preview):
                        result = {'schema_version': VERSION, 'status': 'ok', 'recovered': True,
                                  'revision': package.revision, 'update_ref': ref,
                                  'identity': preview['identity'],
                                  'refreshed': refreshed_summary(preview['plan']),
                                  'next_action': 'get_pdk_data'}
                        self.reviews.finish(ref, result)
                        preview['applied'] = result
                        return result
                    fail('Concurrent PDK update or baseline change', 'pdk_update_conflict')
                plan = request_plan(package, preview['args'], self.bridge)
                if plan['changed'] != preview['plan']['changed']:
                    fail('Source changed again; prepare the refresh again', 'pdk_update_conflict')
                result = commit(root, package, preview, plan)
            current = select(self.workspace, preview['args']['library'], self.environment)
            result = {**result, 'revision': current.revision}
            self.reviews.finish(ref, result)
            preview['applied'] = result
            return result


def converged(package, preview):
    """Whether the preview's requested identity/refresh is already effective."""
    if not all(package.manifest.get(key) == value for key, value in preview['identity'].items()):
        return False
    for ref, row in preview['plan']['changed'].items():
        record = current_record(package, ref)
        if not record or record['fingerprint'] != row['to']:
            return False
    return True


def refreshed_summary(plan):
    return {ref: {'from': row['from'], 'to': row['to'], 'kind': row['kind']}
            for ref, row in sorted(plan['changed'].items())}


def commit(root, package, preview, plan):
    """Assemble the new manifest and perform the one atomic rebase."""
    args, identity = preview['args'], preview['identity']
    manifest = deepcopy(package.base.manifest)
    invalidate(manifest)
    manifest.update(identity)
    additions = set(package.dependency_updates)
    for ref, row in plan['changed'].items():
        if ref in additions:
            continue  # refreshed inside the delta's dependency_updates instead
        if ref not in manifest['dependencies']:
            fail('Unknown dependency to refresh: ' + ref, 'pdk_update_conflict')
        manifest['dependencies'][ref] = {**manifest['dependencies'][ref], 'fingerprint': row['to']}
    outcome = rewrite(root, package, manifest, plan['changed'], reason=args.get('reason'))
    return {
        'schema_version': VERSION, 'status': 'ok',
        'update_ref': preview['result']['update_ref'],
        'baseline_revision': outcome['baseline_revision'],
        'identity': {key: manifest[key] for key in identity},
        'refreshed': refreshed_summary(plan),
        'reapproved': {'changes': outcome['reapproved'][:MAX_REAPPROVED_LISTED],
                       'truncated': len(outcome['reapproved']) > MAX_REAPPROVED_LISTED},
        'rebase': outcome['rebase'],
        're_verification': {'logical_documents_compared': outcome['documents_compared'],
                            'logical_documents_changed': 0,
                            'overlay_changes_rechecked': outcome['changes_rechecked'],
                            'fingerprints_refreshed': len(plan['changed'])},
        'next_action': 'get_pdk_data',
    }
