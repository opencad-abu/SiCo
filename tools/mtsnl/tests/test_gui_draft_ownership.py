"""Behavioral F03 acceptance, also executable against a relocated native GUI."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QListWidgetItem

from mtsnetlistor.config import canonical_request_digest, load_workspace, save_workspace
from mtsnetlistor.defaults import SourceDefaults
from mtsnetlistor.gui.controller import ControllerState
from mtsnetlistor.gui.main_window import MtsMainWindow
from mtsnetlistor.model import CellNetlistSpec, NetlistRequest, SourceDesign


class DraftOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.cds = self.root / "cds.lib"
        self.cds.write_text("DEFINE source ./source\n", encoding="ascii")
        self.window = MtsMainWindow(session=None)
        self.page = self.window._pages[0]
        self.page._poll_timer.stop()
        self.page._apply_request_config(NetlistRequest(
            SourceDesign(self.cds, "source", "first"),
            cell_specs=(CellNetlistSpec("source", "first"),
                        CellNetlistSpec("source", "second")),
        ), refresh_catalog=False)
        self.tokens = 0
        self.probes = []
        self.defaults_patch = patch.object(self.page.controller, "read_source_defaults", self.probe)
        self.defaults_patch.start()

    def tearDown(self):
        self.defaults_patch.stop()
        self.window.close()
        self.page.controller.close()
        self.directory.cleanup()

    def probe(self, source, *, dialect, **kwargs):
        self.tokens += 1
        self.probes.append((self.tokens, source, dialect))
        self.page.controller._state = ControllerState(
            token=self.tokens, stage="probing_defaults", busy=True)
        return self.tokens

    def deliver(self, state):
        self.page.controller._state = state
        self.page._receive_state(state)
        self.page._poll_state()

    def test_raw_text_and_publication_remain_independent_across_cells_and_dialects(self):
        page = self.page
        page.temp.setText("27.000")
        page.scale.setText("1e-6")
        page.gmin.setText("1.00e-12")
        before = page._target_free_request(page._request())
        page.publish_text.setChecked(True)
        page.target_library.addItem("target")
        page.target_cell.setText("chosen")
        page.overwrite_netlist_view.setChecked(True)
        self.assertEqual(before, page._target_free_request(page._request()))
        page.simulator.setCurrentText("hspiceD")
        self.assertEqual(page.temp.text(), "")
        self.assertEqual(page.target_cell.text(), "chosen")
        page.temp.setText("85.000")
        page.scale.setText("2e-6")
        page.target_cell.setText("shared")
        page.source_cells_list.setCurrentRow(1)
        self.assertEqual(page.target_cell.text(), "second")
        page.temp.setText("100")
        page.source_cells_list.setCurrentRow(0)
        self.assertEqual(page.temp.text(), "85.000")
        page.simulator.setCurrentText("spectre")
        self.assertEqual((page.temp.text(), page.scale.text(), page.gmin.text()),
                         ("27.000", "1e-6", "1.00e-12"))
        self.assertEqual(page.target_cell.text(), "shared")
        self.assertTrue(page.overwrite_netlist_view.isChecked())
        self.assertEqual(before, page._target_free_request(page._request()))

    def test_configuration_roundtrip_preserves_raw_numeric_text(self):
        self.page.temp.setText("27.000")
        self.page.scale.setText("1e-6")
        self.page.gmin.setText("1.00e-12")
        path = self.root / "workspace.toml"
        save_workspace(self.window._workspace_config(), path)
        process = load_workspace(path).processes[0]
        self.page._apply_request_config(process.request, refresh_catalog=False,
                                        presentation=process.presentation)
        self.assertEqual((self.page.temp.text(), self.page.scale.text(), self.page.gmin.text()),
                         ("27.000", "1e-6", "1.00e-12"))

    def test_deleted_and_readded_identity_rejects_original_defaults(self):
        page = self.page
        key = ("source", "first", "schematic")
        page._enqueue_defaults_probe(key, dialects=("spectre", "hspiceD"))
        old_token, source, dialect = self.probes[0]
        page._delete_source_cell(page.source_cells_list.currentItem())
        item = QListWidgetItem("schematic")
        item.setData(Qt.UserRole, key)
        page.source_view_list.addItem(item)
        page.source_view_list.setCurrentItem(item)
        page._select_source_view()
        self.deliver(ControllerState(token=old_token, stage="defaults_ready", defaults=SourceDefaults(
            "asi_initialization", dialect, source, (), (), "999", "9e-6")))
        self.assertEqual(page.temp.text(), "")
        self.assertNotIn("999", str(page._request().process_options))

    def test_editing_active_probe_dialect_does_not_strand_other_dialect(self):
        page = self.page
        page._enqueue_defaults_probe(("source", "first", "schematic"),
                                     dialects=("spectre", "hspiceD"))
        token, source, dialect = self.probes[0]
        page.temp.setText("28.000")
        self.deliver(ControllerState(token=token, stage="defaults_ready", defaults=SourceDefaults(
            "asi_initialization", dialect, source, (), (), "27", "1e-6")))
        self.assertEqual(page.temp.text(), "28.000")
        self.assertEqual([entry[2] for entry in self.probes], ["spectre", "hspiceD"])
        token, source, dialect = self.probes[-1]
        self.deliver(ControllerState(token=token, stage="defaults_ready", defaults=SourceDefaults(
            "asi_initialization", dialect, source, (), (), "85", "2e-6")))
        page.simulator.setCurrentText("hspiceD")
        self.assertEqual(page.temp.text(), "85")
        self.assertFalse(page.defaults.busy)

    def test_cached_defaults_refresh_the_visible_waiting_cell(self):
        page = self.page
        page._enqueue_defaults_probe(("source", "first", "schematic"))
        page.source_cells_list.setCurrentRow(1)
        page._enqueue_defaults_probe(("source", "second", "schematic"))
        page.publish_text.setChecked(True)
        page.target_library.addItem("target")
        page.target_cell.setText("keep_destination")
        token, source, dialect = self.probes[0]
        self.deliver(ControllerState(token=token, stage="defaults_ready", defaults=SourceDefaults(
            "asi_initialization", dialect, source, (), (), "27.000", "1e-6")))
        self.assertEqual(len(self.probes), 1)
        self.assertEqual(page.temp.text(), "27.000")
        self.assertEqual(page.target_cell.text(), "keep_destination")

    def test_late_generation_after_source_change_is_not_displayed(self):
        page = self.page
        captured = []
        with patch.object(page.controller, "generate_many", lambda request: captured.append(request) or 41):
            self.assertTrue(page._generate())
        from mtsnetlistor.workflow import CellGenerationResult, MultiGenerationResult
        from dataclasses import replace
        items = []
        for spec in captured[0].selected_cells:
            request = replace(captured[0], source=replace(captured[0].source, cell=spec.cell),
                              cell_specs=(), target=spec.target).validate()
            artifact = SimpleNamespace(stable_output=self.root / (spec.cell + ".spe"),
                                       run_dir=self.root,
                                       request_digest=canonical_request_digest(page._target_free_request(request)))
            items.append(CellGenerationResult(request, artifact))
        new_cds = self.root / "other.lib"
        new_cds.write_text("", encoding="ascii")
        page.source_edit.setText(str(new_cds))
        self.deliver(ControllerState(token=41, generation=MultiGenerationResult("succeeded", tuple(items))))
        self.assertFalse(page.open_result_button.isEnabled())
        self.assertIsNone(page.generation.result)

    def test_frozen_publication_and_cancelled_handoff(self):
        page = self.page
        page.publish_text.setChecked(True)
        page.target_library.addItem("target")
        page.target_cell.setText("frozen")
        page.controller._state = ControllerState(target_catalog=SimpleNamespace(authoritative=True))
        with patch.object(page.controller, "generate_many", return_value=51):
            self.assertTrue(page._run())
        frozen = page.generation.pending[51].publication
        page.target_cell.setText("edited")
        self.assertEqual(frozen.selected_cells[0].target.cell, "frozen")
        from mtsnetlistor.workflow import CellGenerationResult, MultiGenerationResult
        from dataclasses import replace
        items = []
        for spec in frozen.selected_cells:
            request = replace(frozen, source=replace(frozen.source, cell=spec.cell),
                              cell_specs=(), target=spec.target).validate()
            artifact = SimpleNamespace(stable_output=self.root / (spec.cell + ".spe"),
                                       run_dir=self.root,
                                       request_digest=canonical_request_digest(page._target_free_request(request)))
            items.append(CellGenerationResult(request, artifact))
        self.deliver(ControllerState(token=51, generation=MultiGenerationResult("succeeded", tuple(items))))
        self.assertIsNotNone(page.generation.handoff)
        with patch.object(page.controller, "publish_many") as publish:
            page._cancel()
            self.application.processEvents()
            publish.assert_not_called()
        self.assertIsNone(page.generation.handoff)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(DraftOwnershipTests))
    if not result.wasSuccessful():
        raise RuntimeError("MTS draft ownership acceptance failed")
