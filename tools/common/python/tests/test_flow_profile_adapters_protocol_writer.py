from __future__ import annotations

from flow_profile_adapter_fixtures import *

def test_skill_writer_serializes_false_and_empty_arrays_with_dbaccess(
    tmp_path: Path,
) -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    output = tmp_path / "writer.toml"
    sources = (
        CAD_ROOT / "common/skill/SICO_toml.il",
        CAD_ROOT / "common/skill/SICO_profile.il",
    )
    skill = "\n".join(
        (
            *(f'load("{source}")' for source in sources),
            f'out=outfile("{output}" "w")',
            "SICO_profileWriteEntries(out list(",
            '  list("batch.tasks" nil)',
            '  list("drc.custom_svrf_command" "DRC SELECT CHECK M1\\nDRC MAXIMUM RESULTS ALL")',
            '  list("drc.rule_select_checks" nil)',
            '  list("drc.rule_select_enable" nil)',
            '  list("drc.rule_select_groups" nil)',
            '  list("extract.corner_temperatures" nil)',
            '  list("extract.corners" nil)',
            '  list("input.cells" nil)))',
            "close(out)",
            'printf("PROFILE_WRITER_OK\\n")',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess],
        input=skill,
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
    )
    combined = completed.stdout + completed.stderr
    assert completed.returncode == 0, combined
    assert "*Error*" not in combined
    assert "(reader)" not in combined
    assert "PROFILE_WRITER_OK" in combined
    text = output.read_text(encoding="utf-8")
    assert "rule_select_enable = false" in text
    for key in (
        "tasks",
        "rule_select_checks",
        "rule_select_groups",
        "corner_temperatures",
        "corners",
        "cells",
    ):
        assert f"{key} = []" in text
    assert 'custom_svrf_command = "DRC SELECT CHECK M1\\nDRC MAXIMUM RESULTS ALL"' in text


def test_shared_profile_paths_preserve_raw_only_for_same_destination_with_dbaccess(
    tmp_path: Path,
) -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    source = tmp_path / "source/profile.toml"
    destination = tmp_path / "copy/profile.toml"
    source.parent.mkdir()
    destination.parent.mkdir()
    helpers = (
        CAD_ROOT / "common/skill/SICO_toml.il",
        CAD_ROOT / "common/skill/SICO_profile.il",
    )
    skill = "\n".join(
        (
            *(f'load("{helper}")' for helper in helpers),
            "form=makeTable(\"profilePathForm\" nil)",
            f'putpropq(form "{source}" cadProfileSourcePath)',
            'putpropq(form list(list("drc.runset_file" "rules.drc" '
            '"/project/source/rules.drc")) cadProfilePathStates)',
            'entries=list(list("drc.runset_file" '
            '"/project/source/rules.drc"))',
            f'same=SICO_profilePreserveRawPaths(form entries "{source}")',
            f'copy=SICO_profilePreserveRawPaths(form entries "{destination}")',
            'when(and(SICO_profileDataValue(same "drc.runset_file")=="rules.drc" '
            'SICO_profileDataValue(copy "drc.runset_file")== '
            '"/project/source/rules.drc")',
            '  printf("PROFILE_SAVE_AS_PATH_OK\\n"))',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess],
        input=skill,
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "(reader)" not in output
    assert "PROFILE_SAVE_AS_PATH_OK" in output


def test_shared_profile_writer_rejects_conflicting_duplicate_keys_with_dbaccess(
    tmp_path: Path,
) -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    output_path = tmp_path / "duplicates.toml"
    helpers = (
        CAD_ROOT / "common/skill/SICO_toml.il",
        CAD_ROOT / "common/skill/SICO_profile.il",
    )
    skill = "\n".join(
        (
            *(f'load("{helper}")' for helper in helpers),
            f'out=outfile("{output_path}" "w")',
            'same=SICO_profileWriteEntries(out list('
            'list("drc.tool" "Calibre") list("drc.tool" "Calibre")))',
            "close(out)",
            f'out=outfile("{output_path}" "a")',
            'conflict=SICO_profileWriteEntries(out list('
            'list("drc.run_mode" "Hier") list("drc.run_mode" "Flat")))',
            "close(out)",
            'when(and(same !conflict) printf("PROFILE_DUPLICATE_GUARD_OK\\n"))',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess],
        input=skill,
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "(reader)" not in output
    assert "PROFILE_DUPLICATE_GUARD_OK" in output
    assert output_path.read_text(encoding="utf-8").count("tool =") == 1


def test_skill_writer_validates_publishes_and_removes_staging_with_dbaccess(
    tmp_path: Path,
) -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    output = tmp_path / "drc-profile.toml"
    sources = (
        CAD_ROOT / "common/skill/SICO_toml.il",
        CAD_ROOT / "common/skill/SICO_profile.il",
    )
    entries = (
        ("run.root_type", "Customize Directory"),
        ("run.root", "/tmp/runs"),
        ("run.run_type", "Current Host"),
        ("run.queue_name", ""),
        ("run.server_name", ""),
        ("input.type", "OA"),
        ("input.layout.lib", "demo"),
        ("input.layout.cell", "top"),
        ("input.layout.view", "layout"),
        ("drc.tool", "Calibre"),
        ("drc.runset_name", "default"),
        ("drc.runset_file", "/tmp/rules.drc"),
        ("drc.run_mode", "Hier"),
        ("runtime.cpus", "1"),
        ("batch.scope", "Single Cell"),
        ("batch.parallel_cells", "2"),
    )
    entry_source = " ".join(
        f'list("{path}" "{value}")' for path, value in entries
    )
    entry_source += " " + " ".join(
        (
            'list("drc.rule_select_enable" nil)',
            'list("drc.rule_select_groups" nil)',
            'list("drc.rule_select_checks" nil)',
            'list("drc.custom_svrf_enable" nil)',
            'list("drc.custom_svrf_command" "")',
            'list("batch.tasks" nil)',
        )
    )
    skill = "\n".join(
        (
            *(f'load("{source}")' for source in sources),
            f'setShellEnvVar("SICO_PROFILE_PYTHON_ENTRY" "{CAD_ROOT}/common/python/sico-profile")',
            f'setShellEnvVar("SICO_PYTHON" "{_profile_python()}")',
            f"entries=list({entry_source})",
            f'if(SICO_profileWrite("{output}" "DRC" entries)',
            '  then printf("PROFILE_SAVE_OK\\n")',
            '  else printf("PROFILE_SAVE_FAILED\\n"))',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess],
        input=skill,
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
        cwd=tmp_path,
        env={**os.environ, "PWD": str(tmp_path)},
    )
    combined = completed.stdout + completed.stderr
    assert completed.returncode == 0, combined
    assert "*Error*" not in combined
    assert "[CAD-PROFILE][ERROR]" not in combined
    assert "PROFILE_SAVE_OK" in combined
    assert output.stat().st_mode & 0o777 == 0o600
    assert not list((tmp_path / ".cad").glob("cad-profile-save*"))
    values = load_profile(output, "DRC").as_dict()
    assert values["drc.rule_select_enable"] is False
    assert values["drc.rule_select_groups"] == ()
    assert values["batch.tasks"] == ()


def test_skill_writer_rejection_preserves_target_and_removes_staging_with_dbaccess(
    tmp_path: Path,
) -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    output = tmp_path / "drc-profile.toml"
    output.write_text("keep me\n", encoding="utf-8")
    sources = (
        CAD_ROOT / "common/skill/SICO_toml.il",
        CAD_ROOT / "common/skill/SICO_profile.il",
    )
    skill = "\n".join(
        (
            *(f'load("{source}")' for source in sources),
            f'setShellEnvVar("SICO_PROFILE_PYTHON_ENTRY" "{CAD_ROOT}/common/python/sico-profile")',
            f'setShellEnvVar("SICO_PYTHON" "{_profile_python()}")',
            'entries=list(list("run.root" "/tmp/runs") '
            'list("run.run_type" "Current Host"))',
            f'if(!SICO_profileWrite("{output}" "DRC" entries)',
            '  then printf("PROFILE_SAVE_REJECTED\\n"))',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess],
        input=skill,
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
        cwd=tmp_path,
        env={**os.environ, "PWD": str(tmp_path)},
    )
    combined = completed.stdout + completed.stderr
    assert completed.returncode == 0, combined
    assert "*Error*" not in combined
    assert "PROFILE_SAVE_REJECTED" in combined
    assert output.read_text(encoding="utf-8") == "keep me\n"
    assert not list((tmp_path / ".cad").glob("cad-profile-save*"))
