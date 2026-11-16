"""Blocked PDK workflows must name the upstream cause and the next action."""

from cadai.pdk_standard.blockers import message, report


class Package:
    revision = "rev"
    manifest = {
        "format": "sico.pdk.package", "schema_version": "1.1.0",
        "package_id": "synthetic", "pdk_version": "v1",
        "options": {}, "libraries": {"synthetic": {"name": "synthetic"}},
        "dependencies": {}, "files": {},
    }

    def __init__(self):
        self.devices = {"cell0": {
            "library": "synthetic", "cell": "cell0", "view": "symbol",
            "dir": "cell0", "categories": ["mos"],
            "voltage": {"state": "unknown", "reason": "not collected"},
            "use": {"circuit": "unknown", "testbench": "unknown",
                    "extraction": "unknown", "verification": "unknown"},
            "reason": "usage not confirmed"}}

    def available(self, name):
        return False

    def document(self, name):
        if name == "device.json":
            return {"source": "capture", "depends_on": [], "items": self.devices}
        if name == "file.json":
            return {"source": "capture", "depends_on": [], "items": {
                "file_model": {"kind": "model", "root": "pdk",
                               "path": "models/spectre/test.lib",
                               "purpose": "Model declaration resource; "
                                          "loading configuration unconfirmed"}}}
        raise AssertionError("unexpected document: " + name)


def test_report_names_model_configuration_and_usage_as_root_causes():
    data = report(Package())
    codes = [row["code"] for row in data["roots"]]
    assert codes[:2] == ["model_configuration_missing", "no_usable_device"]
    assert "usage_unconfirmed" in codes
    assert data["publication_blockers"][0]["code"] == "model_configuration_missing"
    assert data["full_library_gaps"][0]["code"] == "usage_unconfirmed"
    assert data["design_ready"] is False
    assert data["next_action"] == "collect_pdk_data"
    assert data["roots"][0]["automatic"] is True
    assert data["publication_blockers"][1]["tool"] == "prepare_pdk_data_update"
    assert "get_pdk_data(section=blockers)" in message(Package())


def test_published_package_with_no_findings_is_ready(monkeypatch):
    import cadai.pdk_standard.coverage as coverage
    import cadai.pdk_standard.readiness as readiness

    monkeypatch.setattr(coverage, "gaps", lambda package: [])
    monkeypatch.setattr(readiness, "issues", lambda package: [])
    package = Package()
    package.manifest["publication"] = {"profile": "circuit-simulation-v1"}
    package.devices["cell0"]["use"]["circuit"] = "allow"
    package.devices["cell0"]["use"]["testbench"] = "allow"
    package.available = lambda name: name == "cell0/simulation.json"
    original = package.document
    package.document = lambda name: (
        {"source": "capture", "depends_on": [], "items": {}}
        if name == "file.json" else original(name))
    data = report(package)
    assert data["status"] == "ok" and data["design_ready"] is True
