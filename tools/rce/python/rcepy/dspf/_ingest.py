"""Translate streaming DSPF records into the normalized SQLite schema."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ._nodes import NodeStore
from ._sqlite import sqlite3
from .parser import DspfRecord, iter_dspf_records
from .schema import set_metadata


_BUFFER_SIZE = 5000


class _Writer:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.db = connection
        self.nodes = NodeStore(connection)
        self.subcircuit_id: int | None = None
        self.net_id: int | None = None
        self.net_start: int | None = None
        self.subcircuit_start: int | None = None
        self.ground_names: dict[int, set[str]] = {}
        self.resistors: list[tuple[object, ...]] = []
        self.capacitors: list[tuple[object, ...]] = []
        self.diagnostics: list[tuple[object, ...]] = []
        self.instances: list[tuple[object, ...]] = []
        self.ports: list[tuple[object, ...]] = []
        self.instance_pins: list[tuple[object, ...]] = []
        self.records = 0
        self.handlers = {
            "metadata": self._write_metadata,
            "subcircuit_start": self._write_subcircuit_start,
            "subcircuit_end": self._write_subcircuit_end,
            "instance_section": self._write_instance_section,
            "ground": self._write_ground,
            "layer": self._write_layer,
            "net": self._write_net,
            "net_boundary": self._write_net_boundary,
            "node_p": self._write_node_p,
            "node_i": self._write_node_i,
            "node_s": self._write_node_s,
            "resistor": self._write_resistor,
            "capacitor": self._write_capacitor,
            "diagnostic": self._write_diagnostic,
            "instance": self._write_instance,
            "model_device": self._write_model_device,
        }

    def consume(self, record: DspfRecord) -> None:
        self.records += 1
        handler = self.handlers.get(record.kind)
        if handler is None:
            self._add_diagnostic(record, "warning", "unindexed_record", "Record was not indexed")
        else:
            handler(record)
        if (
            len(self.resistors) >= _BUFFER_SIZE
            or len(self.capacitors) >= _BUFFER_SIZE
            or len(self.diagnostics) >= _BUFFER_SIZE
            or len(self.instances) >= _BUFFER_SIZE
            or len(self.ports) >= _BUFFER_SIZE
            or len(self.instance_pins) >= _BUFFER_SIZE
        ):
            self.flush()

    def finish(self, final_line: int) -> dict[str, int]:
        if self.subcircuit_id is not None:
            self.diagnostics.append((
                "warning", "missing_ends", "Subcircuit reached EOF without .ENDS",
                final_line, final_line, self.net_id, None,
            ))
        self._close_net(final_line)
        self._close_subcircuit(final_line)
        self.flush()
        self.db.execute(
            "INSERT INTO diagnostic(severity,code,message,source_line,line_end,net_id,raw_summary) "
            "SELECT 'warning','orphan_node','Node has no declared net',COALESCE(source_line,0),"
            "COALESCE(source_line,0),NULL,name FROM node WHERE net_id IS NULL AND kind!='ground'"
        )
        self.db.execute(
            "INSERT INTO diagnostic(severity,code,message,source_line,line_end,net_id,raw_summary) "
            "SELECT 'warning','cross_net_resistor','Resistor endpoints belong to different nets',"
            "r.source_line,r.source_line,r.declared_net_id,r.name FROM resistor r "
            "JOIN node a ON a.id=r.node1_id JOIN node b ON b.id=r.node2_id "
            "WHERE a.net_id IS NOT NULL AND b.net_id IS NOT NULL AND a.net_id!=b.net_id"
        )
        names = (
            "subcircuit", "net", "node", "resistor", "capacitor", "port",
            "instance_pin", "device_instance", "diagnostic",
        )
        return {name: self.db.execute(f"SELECT count(*) FROM {name}").fetchone()[0] for name in names}

    def flush(self) -> None:
        self.nodes.flush()
        if self.resistors:
            self.db.executemany(
                "INSERT INTO resistor(name,declared_net_id,node1_id,node2_id,value,raw_value,layer,length,width,source_line) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)", self.resistors,
            )
            self.resistors.clear()
        if self.capacitors:
            self.db.executemany(
                "INSERT INTO capacitor(name,declared_net_id,node1_id,node2_id,value,raw_value,layer,source_line) "
                "VALUES (?,?,?,?,?,?,?,?)", self.capacitors,
            )
            self.capacitors.clear()
        if self.diagnostics:
            self.db.executemany(
                "INSERT INTO diagnostic(severity,code,message,source_line,line_end,net_id,raw_summary) "
                "VALUES (?,?,?,?,?,?,?)", self.diagnostics,
            )
            self.diagnostics.clear()
        if self.instances:
            self.db.executemany(
                "INSERT INTO device_instance(subcircuit_id,name,model,raw_summary,source_line) VALUES (?,?,?,?,?)",
                self.instances,
            )
            self.instances.clear()
        if self.ports:
            self.db.executemany(
                "INSERT INTO port(net_id,node_id,direction,capacitance,x,y,source_line) "
                "VALUES (?,?,?,?,?,?,?)",
                self.ports,
            )
            self.ports.clear()
        if self.instance_pins:
            self.db.executemany(
                "INSERT INTO instance_pin(net_id,node_id,instance_name,pin_name,direction,capacitance,x,y,source_line) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                self.instance_pins,
            )
            self.instance_pins.clear()

    def _write_metadata(self, record: DspfRecord) -> None:
        set_metadata(self.db, "dspf_" + str(record.fields["key"]), record.fields["value"])

    def _write_subcircuit_start(self, record: DspfRecord) -> None:
        self._close_net(record.line_start - 1)
        self._close_subcircuit(record.line_start - 1)
        cursor = self.db.execute(
            "INSERT INTO subcircuit(name,line_start) VALUES (?,?)",
            (record.fields["name"], record.line_start),
        )
        self.subcircuit_id = cursor.lastrowid
        self.subcircuit_start = record.line_start
        self.ground_names[self.subcircuit_id] = {"0"}

    def _write_subcircuit_end(self, record: DspfRecord) -> None:
        self._close_net(record.line_start - 1)
        self._close_subcircuit(record.line_end)

    def _write_instance_section(self, record: DspfRecord) -> None:
        self._close_net(record.line_start - 1)

    def _write_ground(self, record: DspfRecord) -> None:
        subcircuit_id = self._require_subcircuit(record)
        name = str(record.fields["name"])
        self.ground_names[subcircuit_id].add(name)
        self.nodes.get(subcircuit_id, name, confidence=2, kind="ground", source_line=record.line_start)
        set_metadata(self.db, f"ground_net_{subcircuit_id}", name)

    def _write_layer(self, record: DspfRecord) -> None:
        self.db.execute(
            "INSERT INTO layer(number,name,itf) VALUES (?,?,?) "
            "ON CONFLICT(number) DO UPDATE SET name=excluded.name,itf=excluded.itf",
            (record.fields["number"], record.fields["name"], record.fields.get("itf")),
        )

    def _write_net(self, record: DspfRecord) -> None:
        subcircuit_id = self._require_subcircuit(record)
        self._close_net(record.line_start - 1)
        try:
            cursor = self.db.execute(
                "INSERT INTO net(subcircuit_id,name,declared_cap,raw_cap,line_start) VALUES (?,?,?,?,?)",
                (subcircuit_id, record.fields["name"], record.fields.get("declared_cap"), record.fields.get("raw_cap"), record.line_start),
            )
            self.net_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            row = self.db.execute(
                "SELECT id FROM net WHERE subcircuit_id=? AND name=?",
                (subcircuit_id, record.fields["name"]),
            ).fetchone()
            self.net_id = row["id"]
            self._add_diagnostic(record, "error", "duplicate_net", "Duplicate net declaration")
        self.net_start = record.line_start
        self.nodes.get(
            subcircuit_id, str(record.fields["name"]), net_id=self.net_id,
            confidence=2, kind="net", source_line=record.line_start,
        )

    def _write_net_boundary(self, record: DspfRecord) -> None:
        self._close_net(record.line_start - 1)

    def _write_node_p(self, record: DspfRecord) -> None:
        node_id = self._connection_node(record, "port")
        if node_id is not None:
            self.ports.append((
                self.net_id, node_id, record.fields.get("direction"),
                record.fields.get("capacitance"), record.fields.get("x"),
                record.fields.get("y"), record.line_start,
            ))

    def _write_node_i(self, record: DspfRecord) -> None:
        node_id = self._connection_node(record, "instance_pin")
        if node_id is not None:
            self.instance_pins.append((
                self.net_id, node_id, record.fields.get("instance"),
                record.fields.get("pin"), record.fields.get("direction"),
                record.fields.get("capacitance"), record.fields.get("x"),
                record.fields.get("y"), record.line_start,
            ))

    def _write_node_s(self, record: DspfRecord) -> None:
        self._connection_node(record, "subnode")

    def _connection_node(self, record: DspfRecord, kind: str) -> int | None:
        if self.net_id is None:
            self._add_diagnostic(record, "error", "connection_outside_net", "Connection record is outside a net block")
            return None
        return self.nodes.get(
            self._require_subcircuit(record), str(record.fields["name"]),
            net_id=self.net_id, confidence=2, kind=kind,
            x=record.fields.get("x"), y=record.fields.get("y"),
            layer=record.fields.get("layer"), source_line=record.line_start,
        )

    def _write_resistor(self, record: DspfRecord) -> None:
        endpoints = self._element_nodes(record, resistor=True)
        if endpoints is not None:
            self.resistors.append((
                record.fields["name"], self.net_id, endpoints[0], endpoints[1],
                record.fields["value"], record.fields["raw_value"], record.fields.get("layer"),
                record.fields.get("length"), record.fields.get("width"), record.line_start,
            ))

    def _write_capacitor(self, record: DspfRecord) -> None:
        endpoints = self._element_nodes(record, resistor=False)
        if endpoints is not None:
            self.capacitors.append((
                record.fields["name"], self.net_id, endpoints[0], endpoints[1],
                record.fields["value"], record.fields["raw_value"], record.fields.get("layer"), record.line_start,
            ))

    def _element_nodes(self, record: DspfRecord, *, resistor: bool) -> tuple[int, int] | None:
        if self.net_id is None:
            self._add_diagnostic(record, "error", "element_outside_net", "Parasitic element is outside a net block")
            return None
        subcircuit_id = self._require_subcircuit(record)
        fields = record.fields
        name1 = str(fields["node1"])
        name2 = str(fields["node2"])
        grounds = self.ground_names[subcircuit_id]
        owner1: int | None = None
        owner2: int | None = None
        confidence1 = 0
        confidence2 = 0
        if resistor:
            owner1 = self.net_id
            owner2 = self.net_id
            confidence1 = 1
            confidence2 = 1
        elif name1 in grounds:
            owner2, confidence2 = self.net_id, 1
        elif name2 in grounds:
            owner1, confidence1 = self.net_id, 1
        elif name1 == fields.get("declared_net"):
            owner1, confidence1 = self.net_id, 2
        elif name2 == fields.get("declared_net"):
            owner2, confidence2 = self.net_id, 2
        else:
            owner1, confidence1 = self.net_id, 1
        get_element = self.nodes.get_element
        node1_id = get_element(
            subcircuit_id, name1, owner1, confidence1, name1 in grounds,
            record.line_start,
        )
        node2_id = get_element(
            subcircuit_id, name2, owner2, confidence2, name2 in grounds,
            record.line_start,
        )
        return node1_id, node2_id

    def _write_diagnostic(self, record: DspfRecord) -> None:
        self._add_diagnostic(record, str(record.fields["severity"]), str(record.fields["code"]), str(record.fields["message"]))

    def _write_instance(self, record: DspfRecord) -> None:
        subcircuit_id = self._require_subcircuit(record)
        self.instances.append((subcircuit_id, record.fields["name"], record.fields.get("model"), record.raw, record.line_start))

    _write_model_device = _write_instance

    def _require_subcircuit(self, record: DspfRecord) -> int:
        if self.subcircuit_id is None:
            cursor = self.db.execute(
                "INSERT INTO subcircuit(name,line_start) VALUES ('<implicit>',?)",
                (record.line_start,),
            )
            self.subcircuit_id = cursor.lastrowid
            self.subcircuit_start = record.line_start
            self.ground_names[self.subcircuit_id] = {"0"}
            self._add_diagnostic(record, "warning", "implicit_subcircuit", "Records appeared before .SUBCKT")
        return self.subcircuit_id

    def _close_net(self, line_end: int) -> None:
        if self.net_id is not None:
            self.db.execute("UPDATE net SET line_end=? WHERE id=?", (max(line_end, self.net_start or line_end), self.net_id))
        self.net_id = None
        self.net_start = None

    def _close_subcircuit(self, line_end: int) -> None:
        if self.subcircuit_id is not None:
            self.db.execute(
                "UPDATE subcircuit SET line_end=? WHERE id=?",
                (max(line_end, self.subcircuit_start or line_end), self.subcircuit_id),
            )
            self.nodes.begin_empty_scope()
            self.ground_names.pop(self.subcircuit_id, None)
        self.subcircuit_id = None
        self.subcircuit_start = None

    def _add_diagnostic(self, record: DspfRecord, severity: str, code: str, message: str) -> None:
        self.diagnostics.append((severity, code, message, record.line_start, record.line_end, self.net_id, record.raw))


def ingest_file(
    source: Path, connection: sqlite3.Connection, *,
    on_line: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[dict[str, int], int, int]:
    writer = _Writer(connection)
    final_line = 0
    final_bytes = 0

    def observe(byte_end: int, line_no: int) -> None:
        nonlocal final_line, final_bytes
        final_line, final_bytes = line_no, byte_end
        if on_line is not None:
            on_line(byte_end, line_no)

    for record in iter_dspf_records(source, on_line=observe, cancelled=cancelled):
        writer.consume(record)
    counts = writer.finish(final_line)
    set_metadata(connection, "record_count", writer.records)
    return counts, final_line, final_bytes
