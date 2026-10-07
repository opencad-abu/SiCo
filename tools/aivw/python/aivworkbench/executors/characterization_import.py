"""Import an authenticated public data set into a fresh immutable workflow.

The imported source generation is historical evidence, not a live OA recheck.
Only a recipe-owned manifest pin can select the data set.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from ..characterization_evidence import read_characterization
from ..executor import ExecutorResult
from ..workspace import sha256_file, write_json_once


def run_characterization_import(context):
    try:
        pin_path = context.recipe.input_path("characterization_source")
        pin = json.loads(pin_path.read_text())
        if set(pin) != {"schema_version", "control_manifest", "sha256", "dataset"} or pin["schema_version"] != 1:
            raise ValueError("unsupported public source pin")
        if pin["dataset"] != "public_calibration":
            raise ValueError("only public calibration may enter a modeling workflow")
        manifest = Path(pin["control_manifest"])
        if not manifest.is_absolute() or manifest.is_symlink() or sha256_file(manifest) != pin["sha256"]:
            raise ValueError("source manifest pin differs")
        source = read_characterization(manifest)
        plan, evidence = source["experiment"], deepcopy(source["evidence"])
        if any(plan["target"][key] != context.recipe.target[key] for key in ("cell", "library")):
            raise ValueError("imported source target differs from recipe")
        if sha256_file(context.recipe.input_path("interface_contract")) != plan["interface_sha256"]:
            raise ValueError("imported source interface differs from recipe")
        old_root = Path(source["payload_root"])
        circuit = Path(source["circuit"])
        old_gate = circuit.parent
        records = [a for c in evidence["cases"] for a in c["artifacts"]]
        wanted = {old_root/a["path"] for a in records}
        wanted.update({circuit, old_gate/"experiment.json", old_gate/"acceptance-decision.json"})
        wanted.update(old_gate/"models"/m["relative_path"] for m in evidence["model_files"])
        paths = []
        remap = {}
        for old in sorted(wanted):
            relative = old.relative_to(old_gate)
            new = context.gate_root/relative
            new.parent.mkdir(parents=True, exist_ok=True)
            data = old.read_bytes()
            with new.open("xb") as stream:
                stream.write(data)
            if sha256_file(old) != sha256_file(new):
                raise ValueError("source artifact changed during import")
            remap[str(old.relative_to(old_root))] = str(new.relative_to(context.run.payload_root))
            paths.append(new)
        for record in records:
            record["path"] = remap[record["path"]]
        # Re-authenticate both original manifests and their artifact index after
        # copying, so a changed source cannot be hidden by a successful copy.
        again = read_characterization(manifest)
        if again["repeat_record"] != source["repeat_record"] or sha256_file(manifest) != pin["sha256"]:
            raise ValueError("source evidence changed during import")
        evidence["import_provenance"] = {**pin, "pin_sha256": sha256_file(pin_path),
                                         "live_source_recheck": "NOT_PERFORMED"}
        output = context.gate_root/"characterization-evidence.json"
        write_json_once(output, evidence)
        return ExecutorResult("PASS", {"scope": "authenticated_historical_public_data", "case_count": len(evidence["cases"])},
                              {"characterization_evidence": str(output.relative_to(context.run.payload_root)),
                               "characterization_evidence_sha256": sha256_file(output),
                               "source_generation": evidence["source_generation"]}, (*paths, output))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return ExecutorResult("BLOCKED_EVIDENCE", {"code": "public_import_invalid", "reason": str(exc)})
