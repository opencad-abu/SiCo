# LEF Generator

该流程使用 Cadence Virtuoso Abstract Generator 的官方非 GUI replay 接口，
从 OA layout/logical view 生成 abstract view 并导出 LEF。实现依据 IC23.1
`abstract` 文档和 IC618 Abstract Generator RAK。

## 运行入口

从 CIW 或 Layout Editor 的 `SiCo -> LEF Generator` 打开表单。标准单元库批量
交付时选择 `Cell List File`；文件格式遵循 Abstract Generator 官方接口，一行一个
cell name，空行会被忽略。未选择 cell-list 时，流程继续使用 `Single Cell`，兼容
原来的单 cell 用法。GUI 不扫描 OA library 目录，也不解析 OA 内部文件。

`Pins`、`Extract`、`Abstract` 三个步骤可以独立选择，默认全部开启。只要任一步
开启，就必须提供 Abstract Generator options 文件；options 负责工艺相关的 pin、
extract、blockage 和 site 规则，本工具不硬编码这些规则。三步全部关闭时执行纯
LEF 导出，并跳过 options 导入，避免已有效的 abstract 状态被重新标记为待生成。
Cadence 在执行 `absAbstract()` 时仍可能根据 cell 状态自动补跑它所需的前置步骤。

后台执行命令等价于：

```bash
abstract -nogui -replay lef.replay.il -log log/abstract.log -cdslib /path/to/cds.lib
```

replay 会逐个精确选择请求的 cells，把它们统一移动到目标 bin，再只选择这些 cells
执行已启用的步骤和 `Export LEF`。工具生成的规范化 cell-list 同时作为 LEF exporter
的最终边界，因此已有 `.abstract` session 中的其他 cells 不会混入结果。options
导入后，工具会重新设置 view、bin、输出文件和 LEF 版本，避免旧 options 覆盖本次
运行目标。

TOML 的 `[input]` 必须从以下三种输入中选择一种，不能混用：

```toml
cell = "INVX1"
cells = ["INVX1", "NAND2X1"]
cell_list_file = "/project/config/core.cells"
```

步骤配置为：

```toml
[steps]
pins = true
extract = true
abstract = true
```

## 加载配置和常用 options

表单顶部的 `Config File` 只接受共享 v1 Profile，点击 `Load` 后会回填输出根目录、设计输入、
options 文件、执行步骤、输出设置以及 Abstract bin options。TOML 由 Python
backend 先完整解析和校验，再作为严格转义的数据加载到 SKILL 表单；TOML 内容不会直接
作为 SKILL 代码执行。`input.cells` 中的多个 cells 会显示在只读的
`Loaded Inline Cells` 字段中，并在再次运行时保持原样。

`Save`/`Save As...` 保存的是长期复用的版本化 Profile；每次运行仍单独生成
`<run-dir>/lef.toml` 给 Python backend 使用，两者不会互相覆盖。这里的 `<run-dir>`
由 `Save Data to` 的输出根目录和 library/cell、layout view 或 batch 输入自动派生，
不是可编辑的 GUI 字段。Profile 的 schema、
安全加载、原子发布和相对路径规则见 [环境配置文档](../../docs/html/flows.html)。
不带 `[cad_config]` 元数据的运行 TOML 会被拒绝，不能作为 GUI Profile 导入。

`More Abstract Options` 使用 disclosure triangle，默认折叠。展开后可覆盖 options
文件中最常用的三组选项：

- Pins：`PinsTextPinMap`、`PinsPowerNames`、`PinsGroundNames`、
  `PinsGeomSearchLevel`、`PinsTextPreserveLabels`、`PinsRestrictToPRBndry`、
  `PinsBoundaryCreate`、`PinsBoundaryLayers`、`PinsCreatePwrPinsFromRouting`、
  `PinsPwrRoutingLayers`、`PinsCreatePolyPRB`
- Extract：`ExtractSig`、`ExtractLayersSig`、`ExtractPinLayersSig`、
  `ExtractNumLevelsSig`、`ExtractPwr`、`ExtractLayersPwr`、
  `ExtractPinLayersPwr`、`ExtractNumLevelsPwr`、`ExtractConnectivity`
- Abstract：`AbstractBlockageTable`、`AbstractBlockageDetailedLayers`、
  `AbstractBlockageCoverLayers`、`AbstractBlockageShrinkWrapLayers`、
  `AbstractBlockageCutAroundPin`、`BlockageCutVia`、`AbstractPinFracture`、
  `AbstractBlockageFracture`、`AbstractSiteName`

每个高级字段默认是 `From Options File`，这种状态不会写入
`[abstract.bin_options]`。显式选择或输入值后，流程会在 `absImportOptions()` 之后、
执行 Pins/Extract/Abstract 之前调用 `absSetBinOption()`，因此 TOML 值优先于原始
options 文件。三步全部关闭时不应用这些 override。文本字段留空表示显式清空该项，
与 `From Options File` 不同，例如：

```toml
[abstract.bin_options]
ExtractSig = false
AbstractPinFracture = true
AbstractSiteName = ""
```

布尔值、整数和有限浮点数也可以直接写入 TOML；backend 会转换成 Abstract Generator
需要的字符串。GUI 未识别的合法 bin option 也会在 Load 后再次写出时保留。

仓库提供 [IC23.1 options 示例](python/examples/ic618_rak_abstract_ic231.options)，它
包含 GUI 中的 29 个常用 bin options，使用 IC618 RAK/GPDK045 的 `Metal1`、`Via1` 和
`CoreSite` 值。它是项目模板，不是通用 PDK 配置；复制后必须按本项目的 layer 名称、
connectivity、power/ground 命名规则及 site 名称修改。可直接作为 GUI 默认值：

```bash
export LEFGEN_OPT_FILE="$CAD_HOME/tools/lef/python/examples/ic618_rak_abstract_ic231.options"
```

## 环境变量

| 变量 | 用途 | 默认值 |
| --- | --- | --- |
| `LEF_CONFIG` | GUI `Config File` 初值 | 空 |
| `LEF_DB_DIR` | Output Configuration 的 Project Directory | 当前目录 |
| `LEFGEN_OPT_FILE` | GUI options 文件初值 | 空 |
| `LEF_CELL_LIST` | GUI cell-list 文件初值 | 空 |
| `LEF_DEFAULT_BIN` | `Core`、`Block`、`IO` 或 `Corner` | `Core` |
| `LEF_ABSTRACT` | Abstract Generator 可执行文件 | `abstract` |
| `LEF_PYTHON` | LEF backend Python | `CAD_PYTHON`、`CAD_PYTHON_ROOT/bin/python3`、`python3` |
| `LEF_PYTHON_ENTRY` | 开发调试入口覆盖 | `$CAD_HOME/tools/lef/python/lef` |

## 运行目录

单 cell 默认运行目录为 `<run-root>/<library>.<cell>.<view>`，batch 默认目录为
`<run-root>/<library>.batch.<view>`。batch 默认输出名为 `<library>.lef`。

```text
lef.toml                 # 可复现的流程配置
lef.replay.il            # 自动生成的 Abstract Generator SKILL replay
lef.cells.list           # 精确导出的 cell 列表
lef.manifest             # 命令和产物清单
<cell>.lef / <library>.lef # 最终 LEF
log/abstract.log         # Abstract Generator 日志
lefout.log               # Cadence LEF exporter 日志
log/lef.launch.log       # GUI/Python 启动日志
```

同一个派生运行目录再次执行时，流程会先把整个旧目录移动为带时间戳的相邻备份目录，
例如 `<library>.<cell>.<view>.08-26-16-45-32`；若同一秒已有目录，会追加 `.1`、`.2`。
备份包含旧的 `.abstract` session、日志、replay、manifest 和 LEF 输出，新运行目录会
从空目录开始。若运行 TOML 或相对输入文件原本位于运行目录内，会复制回新目录供本次
运行使用。`--dry-run` 和 `generate` 不会移动已有运行目录。

GUI flow 完成后会弹出 `LEF Generation Summary`，显示设计输入、options 文件、LEF 路径、
运行目录、启动日志和退出状态。成功生成非空 LEF 时可直接使用 `Open LEF File` 在 gvim
中打开，或使用 `Copy LEF File` 选择目标路径复制；`Open Log` 会打开或提取本次启动日志。
失败时摘要仍会显示，并保留失败原因和日志入口，LEF 操作按钮会自动禁用。

流程会写入目标 OA library 的 abstract view，因此 library 和关联 technology 必须在
`cds.lib` 中可见，目标 library 也必须可写。

Abstract Generator 某些错误不会反映在进程退出码中。backend 会同时检查本次新生成
的 `abstract.log` 和 `lefout.log`，要求 LEF exporter 明确报告零错误；随后检查 LEF
版本、`END LIBRARY`、每个 `MACRO/END` 结构，并要求 MACRO 集合与请求的 cell 集合
完全一致。旧运行目录会保留为带时间戳的备份，不会与新一轮 Abstract Generator
session 混用。

IC618 RAK 的导入 options 以及 library 已保存的 `.abstract.options` 都可能含有 IC23.1
已删除的旧设置。回归前需把 `ImportLogicalType=TLF` 改为 `LIB`，并删除
`ImportCTLFFiles`；只迁移传给 GUI 的 options 文件不够，因为 library 打开时会先加载
自己的已保存 options。

## 命令行

```bash
$CAD_HOME/tools/lef/python/lef generate /path/to/lef.toml
$CAD_HOME/tools/lef/python/lef run /path/to/lef.toml
```

`generate` 只生成 replay、规范化 cell-list 和 manifest；`run --dry-run` 也不会启动
EDA 进程。生产 Python 3.9 需要 `tomli`。显式配置 Cadence 可执行文件时会保留
`abstract` 符号链接本身，不能解析成 `cdnWrapperWithOA`，因为 wrapper 依赖工具名启动。

仓库中的 `python/examples/ic618_rak_invx1.toml` 对应 IC618 RAK 的单 cell
`AG_Abstract/INVX1`，其中 `[abstract.bin_options]` 演示了两个与 RAK options
同值的安全 override；`python/examples/ic618_rak_ag_pins.toml` 和相邻 `.cells`
文件演示 `AG_Pins` 的批量输入。示例通过 `LEF_RUN_DIR`、`CDS_LIB` 和
`LEFGEN_OPT_FILE` 注入隔离训练环境路径。
