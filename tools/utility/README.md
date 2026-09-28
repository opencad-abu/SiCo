# CAD Utility Menu

TOML-driven menu registration for Virtuoso CIW, Layout Editor, and Schematic Editor.

Repository-wide setup and variable semantics are documented in
[ENVIRONMENT.md](../../docs/html/environment.html).

Set the installation root before starting Virtuoso:

```bash
export CAD_HOME=/path/to/cad
```

Then load the top-level entry from `.cdsinit` or CIW:

```skill
cadHome=(getShellEnvVar "CAD_HOME")
(load (strcat cadHome "/tools/cadToolRegister.il"))
```

This registers the TOML-driven menus and then loads temporary `.il`/`.ils`
sources below `${CAD_HOME}/tools/scripts` in deterministic, depth-first
lexicographic order. Flow frontends are loaded lazily by menu callbacks. All
repository and module paths are resolved below `${CAD_HOME}/tools` at runtime.
`tools/scripts` is a temporary source directory, not a bootstrap directory;
the production `cadAutoLoad.il` remains at `${CAD_HOME}/scripts/cadAutoLoad.il`.

## Silicon Copilot

**Silicon Copilot** in the CIW, Layout, and Schematic SiCo menus opens the
Python/PyQt Agent for a captured target. It uses `ai/skill++/AI_AGENT.ils` and also
installs the ADE Results/History customization callbacks. The existing
**AI Assistant** entry continues to open the original frontend.

The desktop reads `.cad/ai/agent-provider.json` relative to the Virtuoso launch
directory, supports an in-memory API Key prompt, and restores the same session
after the window is hidden. Current tools are read-only. See the
[Agent quick start](../ai/docs/AI_ASSISTANT_AGENT_QUICKSTART.md) for source loading,
Anthropic configuration, lifecycle behavior, and the ADE test coverage boundary.

## Netlist to View

**Netlist to View...** is available from the CIW, Layout, and Schematic SiCo
menus. It imports Spectre, HSPICE, SPICE, or DSPF text with Cadence
`cdsTextTo5x`, including the generated OA connectivity database. The form runs
asynchronously and verifies that the target library/cell/view exists before
reporting success. The selected `cds.lib` must resolve the destination library
to the same directory used by the active Virtuoso session. Closing an active
import terminates the importer process group. PSpice is intentionally
unsupported.

The same importer is available as `${CAD_HOME}/tools/bin/nl2view`; see
[Flow configuration guide](../../docs/html/flows.html) for its CLI contract.

## Change Tech Library

The **Change Tech Library...** SiCo menu item opens a form for one design
cellView. Instance masters use a reusable six-field mapping file. Each
non-comment line contains:

```text
sourceLib sourceCell sourceView targetLib targetCell targetView
```

Blank lines and lines beginning with `;` or `#` are ignored. A source field can
be `*` to match any value or a regular expression matched against the complete
name. Every target lib/cell/view must resolve to an existing cellView. When
several rows match, the first row wins.

The instance mapping area holds multiple rows. **Add** appends a new source
mapping, while **Update** modifies only the selected row. **Apply** processes all
rows in one operation.

For layout cellViews, the form also displays an unchecked **Update Via Master**
control outside the via mapping frame. Enabling it displays the separate
multi-row **Layout Via Master Mappings** editor. Its mapping file uses four
whitespace-separated fields:

```text
sourceLib sourceViaDef targetLib targetViaDef
```

The source pair is matched against the via header's master library and
`viaDefName`; either source field may be `*` or a full-name regular expression.
The source and target ViaDef controls are editable drop-downs populated from the
selected library's `techGetTechFile(...)~>viaDefs~>name`. The target ViaDef must
be a concrete name or `*`; target `*` reuses each matched source ViaDef name in
the target technology. Via replacement preserves each via's origin,
orientation, and override parameters. The target technology must be in the
design cellView's technology graph; otherwise Virtuoso rejects `dbCreateVia`
and the operation is reported as failed.

The instance and via editors each have independent Add, Update, Remove, Clear,
Load, and Save controls. Apply can run either mapping type alone or both in one
operation.

The non-GUI via entry point is:

```skill
cadChangeTechLibReplaceViaMasters(
  "designLib" "topCell" "layout" "/path/via.map")
```

Both non-GUI entry points accept an exact design cell name, `*` for every cell
having the requested view, or a full-name regular expression:

```skill
cadChangeTechLib("designLib" "topCell" "schematic" "/path/master.map")
```

It returns a property list containing `success`, `changed`, `matched`, and
`errors`. Schematic cellViews are checked before saving. The implementation uses
`dbSetInstHeaderMasterName` and resolves PCell variant headers through their
super instance header.

## Flow Logs

RCE, LVS, DRC, Stream GDS, and Export CDL open a separate flow log window for
each run. The window tails the complete `*.launch.log` file and provides the
standard Cadence viewfile search, navigation, and Save As actions. Detailed
tool output stays out of the CIW; start, log-path, completion, and failure
summaries remain there.

The log window remains open when a flow form uses `Run+Close`. Closing the log
window does not stop the flow or affect the on-disk launch log.

## Window Icons

Flow-log windows use `${CAD_HOME}/tools/share/logo.png`; the image is loaded once
per Virtuoso process and cached by absolute path. DRC, LVS, Stream GDS, Export
CDL, RCE, helper forms, completion summaries, Cadence message boxes, and file
dialogs use the standard Virtuoso form shell and its application icon. Cadence
does not expose a supported form-to-window API for changing the title-bar icon
without bypassing standard `buttonLayout` and `initialSize` behavior.

## Recursive `.il` Loading

Loading only `cadToolRegister.il` is preferred. The legacy `loadThisFirst.il`
forwards to it. Legacy startup code may recursively load
the `.il` files below `${CAD_HOME}/tools`; their function definitions are guarded and
can be loaded in any order or more than once. Do not load every regular file in that tree
because it also contains Python, TOML, Markdown, shell, and `.ils` files.
Skip `${CAD_HOME}/tools/scripts` in the outer recursion because
`cadToolRegister.il` owns that temporary source directory and loads it once.

For a legacy recursive loader, use an exact `.il` suffix check:

```skill
procedure(vtsoCustom()
  let((logo sklDir scriptsDir interProc)
    hiResizeWindow(hiGetCIWindow() list(10:10 800:600))
    ddsOpenLibManager()
    logo=getShellEnvVar("LOGO")
    unless(and(logo strlen(logo)>0)
      logo="SiCo")
    sklDir=simplifyFilename(
             strcat(getShellEnvVar("CAD_HOME") "/tools") t)
    scriptsDir=simplifyFilename(strcat(sklDir "/scripts") t)
    interProc=lambda((dir)
      let((tmp)
        foreach(dirOrFile sort(setof(i getDirFiles(dir)
                                      i!="." && i!="..") 'alphalessp)
          tmp=strcat(dir "/" dirOrFile)
          cond(
            (and(isDir(tmp)
                  simplifyFilename(tmp t)!=scriptsDir)
              apply(interProc list(tmp)))
            (and(isFile(tmp) rexMatchp("[.]il$" dirOrFile))
              load(tmp))
          )
        )
      )
    )
    info(strcat("<INFO> Loading " logo " utility SKILL files....\n"))
    apply(interProc list(sklDir))
  )
)
```

The exact end anchor matters: `.ils` files below the DRC/LVS/RCE module directories
are frontend implementation files and are loaded lazily by their guarded entry points.
Temporary `.ils` below `tools/scripts` are loaded only by `cadToolRegister.il`.

`CAD_PYTHON` selects the shared Python executable for RCE, LVS, DRC, LEF, and
menu generation. `RCE_PYTHON`, `LVS_PYTHON`, `DRC_PYTHON`, and `LEF_PYTHON`
remain flow-level overrides, and `RCE_ANALYZER_PYTHON` overrides only the DSPF
Analyzer. Consumers then try `CAD_PYTHON_ROOT/bin/python3` and `python3`; one
flow never reads another flow's override.

`LOGO` overrides the displayed brand, followed by `COMPANY`. Unset/empty values
and the legacy `OCAD` default fall back to `SiCo`.

Python subprocesses do not inherit Virtuoso's `LD_LIBRARY_PATH`. Set
`CAD_SYSTEM_LD_LIBRARY_PATH` only when the selected Python needs an explicit
library search path. The original `LD_LIBRARY_PATH` is preserved for EDA child
processes.

`menu.generated.il` is generated from `menu.toml` by `menu_to_skill.py` during
build or release preparation and is installed beside `menu.il`. At Virtuoso
startup, the menu loader reads that installed file without modifying anything
below `${CAD_HOME}/tools`. Ordinary users therefore only need read access to the
CAD installation.

For compatibility with an older bootstrap that still invokes the generator at
startup, `menu_to_skill.py` treats an existing byte-identical generated file as
a successful no-op. A read-only generated file with stale content still fails
explicitly; update `menu.il`, `menu.toml`, and `menu.generated.il` together when
publishing a release.

Reload the installed menu data and reinstall the menus in the current session:

```skill
(witMenuReload)
```

To publish menu changes, edit `menu.toml` and run the generator in a writable
source or build tree before installation:

```bash
python3 utility/menu_to_skill.py \
  utility/menu.toml utility/menu.generated.il
```

For development without installation-directory write access, this explicit
command regenerates the data in `SICO_MENU_CACHE_DIR`,
`${XDG_CACHE_HOME}/sico`, or `${HOME}/.cache/sico` and immediately installs
it in the current Virtuoso session:

```skill
(witMenuRegenerate)
```

When the installed `menu.generated.il` is unavailable, startup also falls back
to existing user-cache data and generates that cache only when necessary.
