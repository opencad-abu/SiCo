"""Cadence transformation oracle; no library/view access or database writes."""

import os
import re
import shutil
import subprocess

import pytest

from cadai.circuit_geometry_schema import MATRICES, transform, transform_box


@pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_GEOMETRY_NATIVE") != "1", reason="opt-in Cadence coordinate oracle"
)
def test_orientations_match_cadence_db_transform_functions(tmp_path):
    binary = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if not binary:
        pytest.skip("dbAccess unavailable")
    points = [[1.5, 2.5], [-2, 1], [0, 0], [0.0625, -0.125]]
    source = """
procedure(geometryNativeOracle()
  let((gPoint gBox)
    foreach(gOrient list("R0" "R90" "R180" "R270" "MX" "MY" "MXR90" "MYR90")
      foreach(gIndex list(0 1 2 3)
        gPoint=dbTransformPoint(nth(gIndex list(1.5:2.5 -2:1 0:0 0.0625:-0.125))
          list(3.25:-4.5 gOrient 1.0))
        printf("GEOM_POINT|%s|%d|%.12g|%.12g\\n" gOrient gIndex xCoord(gPoint) yCoord(gPoint)))
      gBox=dbTransformBBox(list(-1:-2 3:4) list(3.25:-4.5 gOrient 1.0))
      printf("GEOM_BOX|%s|%.12g|%.12g|%.12g|%.12g\\n" gOrient
        xCoord(car(gBox)) yCoord(car(gBox)) xCoord(cadr(gBox)) yCoord(cadr(gBox))))))
geometryNativeOracle()
exit()
"""
    (tmp_path / "oracle.il").write_text(source)
    result = subprocess.run(
        [binary, "-nocdsinit"],
        input=source,
        text=True,
        capture_output=True,
        cwd=tmp_path,
        timeout=30,
    )
    output = result.stdout + result.stderr
    (tmp_path / "oracle.log").write_text(output)
    assert result.returncode == 0, output
    seen_points, seen_boxes = set(), set()
    for orient, index, x, y in re.findall(
        r"GEOM_POINT\|(\w+)\|(\d+)\|([^|\s]+)\|([^|\s]+)", output
    ):
        i = int(index)
        assert [float(x), float(y)] == pytest.approx(transform(points[i], orient, [3.25, -4.5]))
        seen_points.add((orient, i))
    for orient, x1, y1, x2, y2 in re.findall(
        r"GEOM_BOX\|(\w+)\|([^|\s]+)\|([^|\s]+)\|([^|\s]+)\|([^|\s]+)", output
    ):
        expected = transform_box([[-1, -2], [3, 4]], orient, [3.25, -4.5])
        assert [float(x1), float(y1), float(x2), float(y2)] == pytest.approx(
            expected[0] + expected[1]
        )
        seen_boxes.add(orient)
    assert seen_points == {(orient, i) for orient in MATRICES for i in range(4)}
    assert seen_boxes == set(MATRICES)
