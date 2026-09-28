from cadprofile_profile_fixtures import *

def test_cli_validate_json_and_form_data(tmp_path: Path) -> None:
    source = _write(tmp_path / "drc.toml", _drc_profile())
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    validated = subprocess.run(
        [str(ENTRY), "validate", "--flow", "DRC", str(source), "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    payload = json.loads(validated.stdout)
    assert payload["flow"] == "DRC"
    assert payload["values"]["runtime.cpus"] == "4"

    output = tmp_path / "form.il"
    generated = subprocess.run(
        [str(ENTRY), "form-data", "--flow", "DRC", str(source), str(output)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert generated.stdout == ""
    assert generated.stderr == ""
    assert output.read_text(encoding="utf-8").startswith(
        'SICO_profileSetLoadData("DRC" 1 list('
    )

    published = tmp_path / "published.toml"
    subprocess.run(
        [str(ENTRY), "publish", "--flow", "DRC", str(source), str(published)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert load_profile(published, "DRC").flow == "DRC"
    assert published.read_text(encoding="utf-8") == render_profile_toml(
        load_profile(source, "DRC")
    )
    assert stat.S_IMODE(published.stat().st_mode) == 0o600


def test_cli_publish_does_not_replace_target_on_invalid_source(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "bad.toml",
        _drc_profile().replace("rule_select_enable = true", 'rule_select_enable = "yes"'),
    )
    target = _write(tmp_path / "published.toml", "keep me\n")

    completed = subprocess.run(
        [str(ENTRY), "publish", "--flow", "DRC", str(source), str(target)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert completed.stderr.startswith("[CAD-PROFILE][ERROR]")
    assert target.read_text(encoding="utf-8") == "keep me\n"


def test_cli_reports_validation_error_without_output(tmp_path: Path) -> None:
    source = _write(tmp_path / "bad.toml", _drc_profile().replace("version = 1", "version = 9"))
    output = tmp_path / "form.il"

    completed = subprocess.run(
        [str(ENTRY), "form-data", "--flow", "DRC", str(source), str(output)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert completed.stderr.startswith("[CAD-PROFILE][ERROR]")
    assert not output.exists()
