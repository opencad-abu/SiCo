"""Private shared-node inputs and verified worker evidence; no submit-host bridge."""

import hashlib
import math
import os
import pwd
from dataclasses import asdict
from pathlib import Path

from ..core.contracts import BoundContext, identifier
from ..storage.history import owned_directory
from ..storage.journal import private_dir
from ..storage.project_files import read_record, write_record
from ..transport.framing import ProtocolError
from .background_capability import build_background_probe
from .background_environment import validate_environment
from .background_worker import _read, _write, read_background_artifact

CONTRACT = "cadai.background.node.v1"


def prepare_bundle(root, request, runtime, environment, options, config):
    directory = config.shared(root / "scheduled" / request.job_id)
    for parent in (root, root / "scheduled", directory):
        private_dir(parent)
    if (directory / "manifest.json").exists():
        raise ValueError("Node input already exists; reconcile original submission")
    for directory_path in runtime.libraries.values():
        config.shared(directory_path)
    for executable in (runtime.virtuoso, runtime.xvfb):
        if not executable.is_absolute() or any(c in str(executable) for c in '\0\r\n'):
            raise ProtocolError('Node runtime requires absolute site paths')
    with runtime.context.open('rb') as source:
        data = source.read(32 * 1024 * 1024 + 1)
    if len(data) > 32 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != runtime.context_sha256:
        raise ValueError("Invalid captured worker context")
    _write(directory / "worker.cxt", data)
    settings = asdict(runtime)
    settings.update(context=str(directory / "worker.cxt"),
                    libraries={k: str(v) for k, v in runtime.libraries.items()})
    for key in ("virtuoso", "xvfb"):
        settings[key] = str(settings[key])
    record = dict(contract=CONTRACT, job_id=request.job_id, worker_id=request.worker_id,
        owner_session_id=request.owner_session_id, origin=request.origin.record(),
        method=request.method, params=dict(request.params), runtime=settings,
        environment=validate_environment(environment), options=options,
        scheduler=dict(cluster=config.cluster, owner=config.owner,
                       name="sico-" + request.job_id))
    write_record(directory / "manifest.json", record)
    return directory / "manifest.json"


def read_bundle(path):
    try:
        return _read_bundle(path)
    except ProtocolError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ProtocolError('Invalid node input evidence') from exc


def _read_bundle(path):
    path = Path(path)
    if not path.is_absolute() or path.name != 'manifest.json':
        raise ProtocolError('Node input requires an absolute manifest path')
    for directory in (path.parent.parent, path.parent):
        owned_directory(directory)
    row = read_record(path)
    fields = {"contract", "job_id", "worker_id", "owner_session_id", "origin", "method",
              "params", "runtime", "environment", "options", "scheduler"}
    if not isinstance(row, dict) or set(row) != fields or row["contract"] != CONTRACT:
        raise ProtocolError("Invalid node input manifest")
    from .execution_backend import ExecutionRequest

    request = ExecutionRequest(row["job_id"], row["worker_id"], row["owner_session_id"],
                               BoundContext.from_record(row["origin"]),
                               row["method"], row["params"],
                               row["environment"])
    build_background_probe(request.method, dict(request.params))
    validate_environment(row['environment'])
    if (not isinstance(row['options'], dict) or not isinstance(row['scheduler'], dict)
            or set(row["options"]) - {"startup_timeout", "execution_timeout", "shutdown_timeout"}
            or set(row["scheduler"]) != {"cluster", "owner", "name"}
            or row["scheduler"]["name"] != "sico-" + request.job_id):
        raise ProtocolError("Invalid node execution settings")
    for value in row['options'].values():
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 3600:
            raise ProtocolError('Invalid captured node deadline')
    if path.parent.name != request.job_id:
        raise ProtocolError('Node directory belongs to another input')
    for value in row['scheduler'].values():
        identifier(value)
    from .background_config import runtime_settings

    runtime = runtime_settings(row['runtime'])
    if runtime.context != path.parent / "worker.cxt":
        raise ProtocolError("Node context is outside its input bundle")
    data = _read(runtime.context, limit=32 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != runtime.context_sha256:
        raise ProtocolError("Node input checksum mismatch")
    return row, request, runtime


def worker_directory(root, job_id):
    return root / "scheduled" / job_id / "workers" / job_id


def verified_receipt(root, row, scheduler):
    try:
        return _verified_receipt(root, row, scheduler)
    except ProtocolError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ProtocolError('Invalid node output evidence') from exc


def _verified_receipt(root, row, scheduler):
    from ..transport.framing import strict_json

    manifest, request, runtime = read_bundle(root / 'scheduled' / row['job_id'] / 'manifest.json')
    if (request.worker_id != row['worker_id'] or request.job_id != row['job_id']
            or request.method != row['method']
            or ('origin' in row and request.origin.record() != row['origin'])
            or ('owner_session_id' in row and request.owner_session_id != row['owner_session_id'])):
        raise ProtocolError('Node manifest changed captured admission')
    started = read_record(root / 'scheduled' / row['job_id'] / 'node.started.json')
    expected = {k: scheduler[k] for k in ('cluster', 'owner', 'name', 'job_id')}
    if started != dict(job_id=row['job_id'], worker_id=row['worker_id'], scheduler=expected):
        raise ProtocolError('Node allocation differs from the observed scheduler job')
    directory = worker_directory(root, row['job_id'])
    for parent in (directory.parent.parent, directory.parent, directory):
        owned_directory(parent)
    receipt = strict_json(_read(directory / "receipt.json"))
    if any(receipt.get(k) != row[k] for k in ("job_id", "worker_id", "method", "target")):
        raise ProtocolError("Node result belongs to another execution")
    if receipt.get('protocol') != 'cad_ai_background_worker.v1':
        raise ProtocolError('Invalid node worker receipt')
    if not receipt.get("finished_at") or not receipt.get("cleanup"):
        return None
    if (not isinstance(receipt['cleanup'], dict) or set(receipt['cleanup']) != {
            'process_reaped', 'guardian_reaped', 'broker_closed', 'temporary_removed'}):
        raise ProtocolError('Invalid node cleanup evidence')
    if not all(value is True for value in receipt["cleanup"].values()):
        return None
    if receipt.get('state') not in {'completed', 'cancelled', 'running_unknown',
            'background_unavailable', 'worker_timeout', 'cleanup_failed'}:
        return None
    if receipt["state"] == "completed":
        read_background_artifact(directory)
    return receipt


def run_node(path):
    import fcntl
    from sicolock import lock as state_lock

    from ..storage.journal import open_private
    from .background_worker import BackgroundWorker
    from .lsf_evidence import load

    row, request, runtime = read_bundle(path)
    directory = Path(path).parent
    with os.fdopen(open_private(directory / "node.lock", os.O_CREAT | os.O_RDWR), "w") as lock:
        state_lock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # A scheduler requeue/restart may enter again. Never replay a started node.
        if (directory / "node.started.json").exists():
            return 4
        _, config = load(directory.parent.parent, row)
        cluster = os.environ.get('LSF_CLUSTER_NAME')
        if not cluster:
            from .lsf_scheduler import LsfScheduler

            cluster = LsfScheduler(config).verify_cluster()
        actual = dict(cluster=cluster,
                      owner=pwd.getpwuid(os.getuid()).pw_name,
                      name=os.environ.get("LSB_JOBNAME"))
        if actual != row["scheduler"] or not os.environ.get("LSB_JOBID", "").isdigit():
            raise ProtocolError("Node scheduler allocation identity mismatch")
        write_record(directory / "node.started.json", dict(job_id=request.job_id,
            worker_id=request.worker_id, scheduler=dict(actual, job_id=os.environ["LSB_JOBID"])))
        worker = BackgroundWorker(directory / "workers", runtime,
            job_id=request.job_id, worker_id=request.worker_id,
            environment=row["environment"], **row["options"])
        receipt = worker.run(request.method, dict(request.params))
        return 0 if receipt["state"] == "completed" else 1
