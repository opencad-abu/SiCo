"""Synthetic contract data, deliberately not a real PDK snapshot."""
from copy import deepcopy


def raw(value, typ=None):
    if typ is None:
        typ = "nil" if value is None else "string" if isinstance(value, str) else "integer"
    return {"type": typ, "status": "known", "value": value}


def fixture():
    lib = {"name": "synthetic", "resolved_path": "/synthetic/pdk", "status": "complete",
           "technology_library": "tech", "technology_status": "known", "pdk_ref": None,
           "pdk_version": None, "pdk_status": "unknown", "source": "synthetic_test"}
    ctx = {"session_ref": "session:synthetic", "bridge_generation": 1, "collector_revision": "test-v1",
           "process_id": 123, "virtuoso_version": "synthetic", "working_directory": "/project",
           "workspace": "/project", "captured_at_cadence": "synthetic", "libraries": [lib],
           "source": "synthetic_test"}
    identity = {"target": {"library": "synthetic", "cell": "unknown", "view": "symbol"},
                "available_views": ["symbol"], "resolved_view_path": "/synthetic/pdk/unknown/symbol",
                "master_file_modified": raw("synthetic-time"),
                "oa_view_type": "schematicSymbol", "source": "synthetic_test"}
    param = {"name": raw("w"), "paramType": raw("string"), "type": raw("cellData"),
             "defValue": raw(""), "editable": raw("cdfgData->x->value"), "display": raw("t"),
             "callback": raw("error(\"must not execute\")"), "parseAsNumber": raw("yes"),
             "parseAsCEL": raw(None), "units": raw("lengthMetric")}
    sec = lambda items: {"status": "complete", "items": items, "source": "synthetic_test", "issues": []}
    data = {"identity": identity, "library": lib,
            "cdf": {"presence": "present", "parameters": sec([param]), "simulators": sec([]),
                    "cell_metadata": {"formInitProc": raw(None), "doneProc": raw(None)},
                    "cell_metadata_status": "complete"},
            "database": {"ports": sec([{"name": "DATA<3:0>", "direction": raw("inputOutput"),
                "width": 4, "members": ["DATA<3>", "DATA<2>", "DATA<1>", "DATA<0>"],
                "members_status": "complete", "net_expression": raw(None), "net_expression_status": "known",
                "net_name": "DATA<3:0>", "pin_count": 2}]), "geometry": sec([]),
                "master_state": {"modified": False, "source": "test"}},
            "project_models": sec([])}
    return ctx, data


class Bridge:
    def __init__(self):
        self.ctx, self.data = fixture()
        self.calls = []
        self.count = 3
        self.init_loaded = True
        self.init_result = None

    def call(self, expression):
        """Session-scoped initialization replies for the shared preparation fixture."""
        self.calls.append(("skill", expression))
        if expression.startswith("aiPdkInitializeLibrary("):
            library = expression.split('"')[1]
            if self.init_result is not None:
                return deepcopy(self.init_result)
            self.init_loaded = True
            return {"ok": True, "library": library, "init_file": "libInit.il",
                    "init_path": "/synthetic/pdk/" + library + "/libInit.il",
                    "status": "initialized", "cached": False, "initialized": True}
        raise AssertionError("unexpected skill call: " + expression)

    def capture(self, op, target):
        self.calls.append((op, deepcopy(target)))
        if op == "categories":
            members = ["cell" + str(index) for index in range(self.count)]
            data = {"status": "complete", "issues": [], "unassigned": [],
                    "items": [{"name": "mos", "members": members, "source": "synthetic_test"}],
                    "source": "ddCatGetLibCats/ddCatGetCatMembers/ddCatGetCellsNotInCat"}
            return deepcopy({"context": self.ctx, "data": data})
        if op == "device":
            data = deepcopy(self.data)
            data["identity"]["target"] = target
            return deepcopy({"context": self.ctx, "data": data})
        records = []
        for index in range(self.count):
            ident = deepcopy(self.data["identity"])
            ident["target"]["cell"] = "cell" + str(index)
            records.append({"identity": ident, "cdf_summary": {"status": "complete", "presence": "present", "names": ["w"]}})
        return deepcopy({"context": self.ctx, "data": {"items": records, "status": "complete", "truncated": False, "issues": []}})
