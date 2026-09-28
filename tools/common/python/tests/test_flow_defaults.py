"""Behavioral checks for installation defaults and literal export options."""

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import MappingProxyType

import pytest

from caddefaults import Defaults
from caddefaults.source import Source
from cadcalibre.defaults import calibre_defaults
from cadstage.command_files import CdlCommandOptions, GdsCommandOptions
from cadstage.command_files import render_cdl_env, render_streamout_cmd
from cadstage.defaults import cdl_lines
from sicopaths import Installation, IDENTITY_BYTES


def bundle(name, text):
    return Defaults(MappingProxyType({name: Source(name, Path("/defaults") / name, text.encode())}))


def test_request_snapshot_is_immutable_and_next_read_is_fresh(tmp_path, monkeypatch):
    (tmp_path / "etc/config").mkdir(parents=True)
    (tmp_path / "etc/config/sico-install.json").write_bytes(IDENTITY_BYTES)
    (tmp_path / "bin").mkdir()
    (tmp_path / "tools/common").mkdir(parents=True)
    directory = tmp_path / "etc/flow-defaults"
    directory.mkdir()
    file = directory / "calibre-drc.svrf"
    file.write_text("DRC ICSTATION YES\n")
    monkeypatch.setattr("caddefaults.source.installation", lambda **_: Installation(tmp_path))
    before = Defaults.load((file.name,))
    file.write_text("DRC ICSTATION NO\n")
    monkeypatch.chdir(tmp_path / "bin")
    after = Defaults.load((file.name,))
    assert "YES" in before.source(file.name).text
    assert "NO" in after.source(file.name).text
    before.save(tmp_path / "log")
    snapshot = tmp_path / "log/flow-defaults" / file.name
    record = json.loads(snapshot.with_name(file.name + ".json").read_text())
    assert record["sha256"] == sha256(snapshot.read_bytes()).hexdigest()
    assert snapshot.read_bytes() == before.source(file.name).data
    with pytest.raises(TypeError):
        before.sources["new"] = before.source(file.name)
    file.unlink()
    with pytest.raises(ValueError, match="Cannot read flow defaults"):
        Defaults.load((file.name,))


def test_export_default_edit_and_removal_leave_request_fields_owned():
    opts = GdsCommandOptions("lib", "top", "layout", "/layer.map", "top.gds", False)
    text = render_streamout_cmd(opts, defaults=bundle("streamout.options", '[vertices]\nmaxVertices "350"\n'))
    assert 'maxVertices "350"' in text
    assert 'replaceBusBitChar                  "false"' in text
    assert 'strmFile "top.gds"' in text
    empty = render_streamout_cmd(opts, defaults=bundle("streamout.options", ""))
    assert "maxVertices" not in empty
    with pytest.raises(ValueError, match="Reserved"):
        render_streamout_cmd(opts, defaults=bundle("streamout.options", '[pins]\nreplaceBusBitChar "true"\n'))
    cdl = CdlCommandOptions("lib", "top", "schematic", "top.cdl", "")
    assert "shortRES" not in render_cdl_env(cdl, defaults=bundle("cdl.env", ""))
    assert "shortRES = 100" in render_cdl_env(cdl, defaults=bundle("cdl.env", "[devices]\nshortRES = 100\n"))


@pytest.mark.parametrize("assignment", [
    'shortRES = system("touch bad")', 'shortRES = (load "bad.il")',
    'shortRES = 1; load("bad.il")', 'simCellName = "other"',
])
def test_cdl_rejects_code_and_generated_fields(assignment):
    with pytest.raises(ValueError):
        cdl_lines(bundle("cdl.env", "[devices]\n" + assignment).source("cdl.env"))


@pytest.mark.parametrize("statement", [
    "DRC SELECT CHECK M1", 'LAYOUT PATH "/wrong.gds"', 'INCLUDE "other.svrf"',
    "VIRTUAL CONNECT COLON YES", "PEX NETLIST wrong DSPF", "SOURCE CASE NO",
])
def test_calibre_defaults_cannot_reconfigure_gui_or_flow(tmp_path, statement):
    with pytest.raises(ValueError, match="owned"):
        calibre_defaults("calibre-drc.svrf", bundle("calibre-drc.svrf", statement), tmp_path)


def test_installation_root_conflict_is_not_a_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("SICO_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="identity"):
        Defaults.load(("calibre-drc.svrf",))


def test_relocated_source_installation_reads_only_its_own_defaults(tmp_path):
    source = Path(__file__).resolve().parents[4]
    root = tmp_path / "relocated"
    (root / "etc/config").mkdir(parents=True)
    (root / "etc/config/sico-install.json").write_bytes(IDENTITY_BYTES)
    (root / "etc/flow-defaults").mkdir()
    (root / "etc/flow-defaults/calibre-drc.svrf").write_text("DRC ICSTATION NO\n")
    (root / "bin").mkdir()
    python = root / "tools/common/python"
    python.mkdir(parents=True)
    for name in ("sicopaths.py", "sicoenv.py"):
        shutil.copy2(source / "tools/common/python" / name, python / name)
    shutil.copytree(source / "tools/common/python/caddefaults", python / "caddefaults")
    result = subprocess.run(
        [sys.executable, "-c", 'from caddefaults import Defaults; print(Defaults.load(("calibre-drc.svrf",)).source("calibre-drc.svrf").text)'],
        cwd=tmp_path,
        env=dict(os.environ, SICO_HOME=str(root), PYTHONPATH=str(python), PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "DRC ICSTATION NO"
