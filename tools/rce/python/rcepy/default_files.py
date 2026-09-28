"""Select only the installation defaults consumed by a flow request."""

from .config import RceConfig


def default_files(cfg: RceConfig, flow: str = "rce") -> tuple[str, ...]:
    names = []
    if flow == "drc":
        return ("streamout.options", "calibre-drc.svrf")
    if flow in {"cdl", "gds"}:
        return ("cdl.env" if flow == "cdl" else "streamout.options",)
    if cfg.input_type in {"OA", "SCH+GDS"}:
        names.append("cdl.env")
    if cfg.input_type in {"OA", "CDL+LAY"}:
        names.append("streamout.options")
    if flow == "lvs":
        if cfg.input_type != "SVDB":
            names.append("calibre-lvs.svrf")
    else:
        if "lvs" in cfg.enabled_stages():
            names.append("calibre-lvs.svrf")
        tool = cfg.ext_tool.upper()
        if tool in {"QRC", "QUANTUS"}:
            names.extend(("quantus.options", "quantus.launch.args"))
        elif tool in {"STARRC", "STARXTRACT"}:
            names.append("starrc.options")
        elif cfg.is_xrc:
            names.append("calibre-xrc.svrf")
    return tuple(names)
