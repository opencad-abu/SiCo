"""Effective Calibre decks bind vendor paths without changing source files."""

from sico_calibre_fixture import make_calibre, make_flow, write_script
from sico_datahub.calibre.context import resolve_flow
from sico_datahub.calibre.deck import prepare_flow
from sico_datahub.calibre.jobs import JobStore


def _prepare(tmp_path, flow, run_ref):
    store = JobStore(tmp_path)
    calibre = make_calibre(tmp_path) / "bin/calibre"
    return store, prepare_flow(
        resolve_flow(flow.kind, str(flow.config), str(tmp_path)),
        store=store,
        run_ref=run_ref,
        calibre_executable=calibre,
    )


def test_drc_deck_binds_vendor_placeholders_and_derives_config(tmp_path):
    flow = make_flow(tmp_path, kind="drc")
    original_config = flow.config.read_text(encoding="utf-8")
    flow.runset.write_text(
        'LAYOUT PRIMARY "*"\n'
        'LAYOUT PATH "*.gds"\n'
        "LAYOUT SYSTEM GDSII\n"
        'DRC RESULTS DATABASE "drc_CAL.OUT" ASCII\n'
        'DRC SUMMARY REPORT "drc_CAL.SUM"\n'
        "LAYER M1\n",
        encoding="utf-8",
    )
    store, prepared = _prepare(tmp_path, flow, "deck-drc-1")
    deck = prepared.effective_deck.path
    text = tmp_path / deck
    body = text.read_text(encoding="utf-8")
    assert 'LAYOUT PRIMARY "inv"' in body
    assert f'LAYOUT PATH "{flow.gds}"' in body
    assert '"drc_CAL.OUT"' not in body
    assert '"*.gds"' not in body
    assert str(flow.run_dir / "db/drc/cal_drc.out") in body
    assert str(flow.run_dir / "log/cal_drc.sum") in body
    derived = prepared.config.path
    derived_text = (tmp_path / derived).read_text(encoding="utf-8")
    assert original_config == flow.config.read_text(encoding="utf-8")
    assert f'runset_file = "{deck}"' in derived_text
    assert prepared.preparation["source"] == "svrf"
    assert prepared.preparation["expanded"] is False
    assert store.prepared_deck_path("deck-drc-1", "drc").is_file()


def test_lvs_deck_binds_source_svdb_report_and_erc_paths(tmp_path):
    flow = make_flow(tmp_path, kind="lvs")
    flow.runset.write_text(
        'SOURCE PATH "xxx.cdl"\n'
        'SOURCE PRIMARY "xxx"\n'
        "SOURCE SYSTEM SPICE\n"
        'LAYOUT PATH "xxx.gds"\n'
        'LAYOUT PRIMARY "xxx"\n'
        "LAYOUT SYSTEM GDSII\n"
        'LVS REPORT "lvs.rep"\n'
        'MASK SVDB DIRECTORY "svdb" QUERY CCI\n'
        "ERC RESULTS DATABASE erc.db\n"
        "ERC SUMMARY REPORT erc.rep\n",
        encoding="utf-8",
    )
    _store, prepared = _prepare(tmp_path, flow, "deck-lvs-1")
    body = (tmp_path / prepared.effective_deck.path).read_text(encoding="utf-8")
    assert '"xxx.cdl"' not in body
    assert '"xxx.gds"' not in body
    assert f'SOURCE PATH "{tmp_path / "input.cdl"}"' in body
    assert f'LAYOUT PATH "{flow.gds}"' in body
    assert f'LVS REPORT "{flow.run_dir / "log/lvs.report"}"' in body
    assert f'MASK SVDB DIRECTORY "{flow.run_dir / "db/svdb.inv"}" QUERY CCI' in body
    assert str(flow.run_dir / "log/erc.results") in body
    assert str(flow.run_dir / "log/erc.rep") in body


def test_compile_time_tvf_is_expanded_before_path_binding(tmp_path):
    flow = make_flow(tmp_path, kind="drc")
    flow.runset.write_text(
        "#! tvf\n"
        'LAYOUT PRIMARY "*"\n'
        'LAYOUT PATH "*.gds"\n'
        "LAYOUT SYSTEM GDSII\n"
        'DRC RESULTS DATABASE "drc_CAL.OUT" ASCII\n'
        'DRC SUMMARY REPORT "drc_CAL.SUM"\n',
        encoding="utf-8",
    )
    calibre = tmp_path / "calibre-expand/bin/calibre"
    write_script(
        calibre,
        'if [ "$1" = "-version" ]; then\n'
        '  printf "Calibre 2026.3 fixture\\n"\n'
        '  exit 0\n'
        "fi\n"
        'if [ "$1" = "-E" ]; then\n'
        '  cp "$3" "$2"\n'
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
    )
    store = JobStore(tmp_path)
    prepared = prepare_flow(
        resolve_flow("drc", str(flow.config), str(tmp_path)),
        store=store,
        run_ref="deck-tvf-1",
        calibre_executable=calibre,
        timeout=10,
    )
    assert prepared.preparation["source"] == "tvf"
    assert prepared.preparation["expanded"] is True
    body = (tmp_path / prepared.effective_deck.path).read_text(encoding="utf-8")
    assert f'LAYOUT PATH "{flow.gds}"' in body
