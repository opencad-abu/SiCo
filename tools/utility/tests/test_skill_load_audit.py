"""Load ownership applicability and broken-guard regressions."""

from utility.skill_load_audit import check_load_ownership, local_guards
from conftest import write


def guarded(body="procedure(sample() t)", *, version="v1", assignment="v1"):
    return (
        f'unless(and(boundp(\'sampleVersion) sampleVersion=="{version}")\n'
        f'{body}\nsampleVersion="{assignment}"\n)\n'
    )


def audit(root, files, manifest=None):
    for path, content in files.items():
        write(root, path, content)
    return check_load_ownership(
        root, list(files), manifest or dict(schema_version=1, loaders=[], lifecycles=[])
    )


def test_guards_must_enclose_every_definition_and_assignment(tmp_path):
    errors, _ = audit(tmp_path, {
        "partial.il": guarded() + "procedure(outside() nil)\n",
        "early.il": "unless(and(boundp('sampleVersion) sampleVersion==\"v1\")\n"
                    "procedure(sample() t))\nsampleVersion=\"v1\"\n",
    })
    assert len(errors) == 2
    assert all("1 definitions lack" in item for item in errors)


def test_mismatched_version_is_rejected(tmp_path):
    errors, _ = audit(tmp_path, {"bad.il": guarded(assignment="v2")})
    assert any("compared ['v1'], assigned ['v2']" in item for item in errors)


def test_dependency_guard_does_not_mask_valid_definition_guard(tmp_path):
    dependency = 'unless(and(boundp(\'otherVersion) otherVersion=="v9") load("other.il"))\n'
    errors, records = audit(tmp_path, {"ok.il": dependency + guarded()})
    assert errors == []
    assert records[0]["kind"] == "local-version-guard"


def test_comments_strings_and_quoted_data_are_not_definitions(tmp_path):
    source = '/* procedure(fake() t) */\n"procedure(fake() t)"\n\'(procedure(fake() t))\n'
    assert audit(tmp_path, {"data.il": source}) == ([], [])


def test_fixture_scope_does_not_exempt_production_worker_names(tmp_path):
    errors, records = audit(tmp_path, {
        "tests/probe.il": "procedure(probe() t)",
        "skill/ProbeWorker.il": "procedure(worker() t)",
        "skill/new.il": "procedure(new() t)",
    })
    assert len(errors) == 2
    assert records == [dict(path="tests/probe.il", kind="fixture-or-reference")]


def parent_manifest():
    return dict(schema_version=1, loaders=[dict(
        owner="loader.ils", reason="Group reload", sources=["skill/child.il"]
    )], lifecycles=[])


def test_parent_ownership_requires_live_reference_and_valid_guard(tmp_path):
    manifest = parent_manifest()
    files = {"loader.ils": guarded('load("/skill/child.il")'),
             "skill/child.il": "procedure(child() t)"}
    assert audit(tmp_path, files, manifest)[0] == []
    files["loader.ils"] = guarded('load("/skill/replaced.il")', assignment="v2")
    errors, _ = audit(tmp_path, files, manifest)
    assert any("invalid parent loader guard" in item for item in errors)
    assert any("no source reference" in item for item in errors)


def test_parent_uses_skill_value_slot(tmp_path):
    source = "unless(and(boundp('loaderVersion) symeval('loaderVersion)==\"v1\")\n"
    source += 'load("child.il") set(\'loaderVersion "v1"))'
    assert local_guards(source)[0]
    assert local_guards(source)[1] == []


def test_stale_and_duplicate_ownership_is_rejected(tmp_path):
    manifest = parent_manifest()
    manifest["loaders"][0]["sources"] *= 2
    errors, _ = audit(tmp_path, {
        "loader.ils": guarded('load("/skill/child.il")'),
        "skill/child.il": guarded(),
    }, manifest)
    assert any("duplicate SKILL load owner" in item for item in errors)
    assert any("stale SKILL load ownership" in item for item in errors)


def test_lifecycle_requires_existing_anchored_owner_and_evidence(tmp_path):
    manifest = dict(schema_version=1, loaders=[], lifecycles=[dict(
        source="worker.il", owner="caller.py", owner_anchor="worker_call",
        kind="worker", reason="Explicit context worker lifecycle",
        evidence=[dict(path="worker.il", contains="procedure(worker(")],
    )])
    files = {"caller.py": "worker_call('worker')", "worker.il": "procedure(worker() t)"}
    assert audit(tmp_path, files, manifest)[0] == []
    files["caller.py"] = "renamed_call('worker')"
    errors, _ = audit(tmp_path, files, manifest)
    assert any("stale evidence anchor" in item for item in errors)
