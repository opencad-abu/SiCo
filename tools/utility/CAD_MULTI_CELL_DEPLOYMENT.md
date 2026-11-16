# DRC/LVS/RCE/LEF 生产部署安装报告

## 1. 发布信息

- 发布日期：2026-08-13
- 工具入口：`SiCo -> DRC`、`SiCo -> LVS`、`SiCo -> RCE`、
  `SiCo -> LEF Generator`
- 部署范围：Virtuoso CIW、Layout Editor，以及 DRC/LVS/RCE/LEF Python runner
- 功能范围：OA-only multi-cell、有界并行、Current Host/LSF、取消、结果 monitor、
  RCE CIW 串行 OA publication、DRC Rule Select、DRC/LVS/RCE Customized SVRF
  Command，以及 RCE/LVS/Export CDL 的 CDL Include 选择和外部文件绝对路径固化
- 用户指南：`tools/common/docs/CAD_MULTI_CELL_USER_GUIDE.md`

本 `full-v6` 是 `20260812-full-v5` 之后的累计完整包，同时包含 common runtime
抽取、multi-cell mapping 编辑修复、DRC/LVS/RCE 自定义 SVRF、RCE `Corner Scope` 标签、
CDL Include 控件及其 INPUT 底部布局修复。它保留全部 Single Cell 行为。Multiple Cells 只支持 OA；
CDL/GDS/SVDB/CCI 等输入不会进入批量模式，也不计划在该模式中支持。

勾选 `Customized SVRF Command` 后，用户文本会原样追加到生成的 `log/drc.cal`、
`log/lvs.cal` 或 Calibre xRC 执行文件 `_xrc.cal_` 末尾。多 corner xRC 的每个生成
控制文件都会追加；foundry DRC rule file 和 `corner/xrc.cal` 都不会被修改。

DRC `Rule Select` 从选定 rule file 中发现 Calibre `GROUP` 及其 check，并在独立的
PyQt5 两级树中显示。group 可整组选中，展开后也可单独选择 check；group checkbox
以未选、全选和三态部分选择显示状态。编译型 TVF 优先使用 `calibre -E` 展开；许可证、
工具或展开失败时回退到静态解析。启用后会在生成的 `log/drc.cal` 中、Customized SVRF
Command 之前追加带引号的 `DRC SELECT CHECK "<group-or-check>" ...`。compile-time TVF
会用 `tvf::VERBATIM` 包裹追加内容。若 rule file 或自定义 SVRF 已有
`DRC SELECT CHECK`/`DRC SELECT CHECK BY LAYER`，为避免 Calibre 并集合并扩大检查集合，
生成阶段会拒绝运行。未启用时保持 rule file 默认
选择；启用但未选 group 或 check 会在提交前拒绝运行。选择随 DRC batch 共享，更换
rule file 会使原选择失效。

`CDL Include` 默认读取 `CDL_HEADER_FILE`。只有需要从 OA schematic 导出 CDL 的
`OA` 和 `SCH+GDS` 输入显示该行；`CDL+GDS`、`CDL+LAY`、`SVDB`、`CCI` 输入隐藏。
勾选时，所选文件的绝对路径写入 TOML 并最终生成 `si.env` 的 `incFILE`；取消勾选
时显式写入空字符串，不会再次回退到环境变量。Export CDL 始终显示该行。

DRC/LVS/RCE GUI 中原 `Runset File` 标签统一显示为 `Rule File`；内部字段名、环境变量
和 TOML 的 `runset_file` key 不变。用户从 Virtuoso 启动目录选择的相对文件或目录会在
TOML 移入 cell run directory 前固化为绝对路径，覆盖 rule file、CDL/GDS、Hcell、
CDL Include、layer map、`cds.lib`、RCE process directory、selection、pin-order 及
OA view mapping 文件，避免 runner 切换工作目录后重新按错误基准解析。

## 2. 运行时文件清单

生产部署应使用完整 source-runtime 包。若因紧急变更必须文件级同步，以下运行时文件
必须作为同一个版本原子更新，不能只复制 GUI 文件：

```text
drc/skill++/DRC.ils
drc/skill++/DRCCB.ils
drc/skill++/DRCCFG.ils
drc/skill++/DRCGUI.ils
drc/skill++/DRCPROFILE.ils
drc/skill++/DRCRULESEL.ils
drc/skill++/DRCRUN.ils
drc/python/drcpy/cli.py
drc/python/drcpy/generator.py
drc/python/drcpy/rule_select.py
drc/python/drcpy/rule_select_gui.py
drc/python/drc

lvs/skill++/LVS.ils
lvs/skill++/LVSCB.ils
lvs/skill++/LVSCFG.ils
lvs/skill++/LVSGUI.ils
lvs/skill++/LVSPROFILE.ils
lvs/skill++/LVSRUN.ils
lvs/skill++/STAGECB.ils
lvs/skill++/STAGECFG.ils
lvs/skill++/STAGEGUI.ils

rce/python/rcepy/gen_lvs.py
rce/python/rcepy/gen_xrc.py

common/python/cadstage/__init__.py
common/python/cadstage/backup.py
common/python/cadstage/command_files.py
common/python/cadstage/process.py
common/python/cadcalibre/__init__.py
common/python/cadcalibre/lvs.py

common/docs/LSF_LOAD_MONITOR_PLAN.md
common/skill++/BASEGUI.ils
common/skill++/BATCHGUI.ils
common/skill++/PROFILEGUI.ils
common/skill/CAD_toml.il
common/skill/CAD_guiProtocol.il
common/skill/CAD_envOptions.il
common/skill/CAD_lsf.il
common/skill/CAD_profile.il
common/skill/CAD_batchAdapter.il
common/skill/CAD_batchCore.il
common/skill/CAD_batchGui.il
common/skill/CAD_batchMonitor.il
common/skill/CAD_batchRows.il
share/close.png
share/monitor.png
common/python/cad-batch
common/python/cad-lsf
common/python/cad-profile
common/python/cadbatch/__init__.py
common/python/cadbatch/cli.py
common/python/cadbatch/compat.py
common/python/cadbatch/controller.py
common/python/cadbatch/manifest.py
common/python/cadbatch/status.py
common/python/cadgui/__init__.py
common/python/cadgui/environment.py
common/python/cadgui/lifecycle.py
common/python/cadgui/protocol.py
common/python/cadgui/transfer.py
common/python/cadgui/wheel.py
common/python/cadlsf/__init__.py
common/python/cadlsf/cache.py
common/python/cadlsf/cli.py
common/python/cadlsf/collector.py
common/python/cadlsf/gui/__init__.py
common/python/cadlsf/gui/app.py
common/python/cadlsf/gui/main_window.py
common/python/cadlsf/gui/models.py
common/python/cadlsf/gui/selector.py
common/python/cadlsf/gui/workers.py
common/python/cadlsf/model.py
common/python/cadlsf/policy.py
common/skill/CAD_lsfMonitor.il
common/python/cadprofile/__init__.py
common/python/cadprofile/cli.py
common/python/cadprofile/compat.py
common/python/cadprofile/model.py
common/python/cadprofile/skill_data.py

rce/skill++/RCE.ils
rce/skill++/RCEBASEGUI.ils
rce/skill++/RCECB.ils
rce/skill++/RCECFG.ils
rce/skill++/RCEGUI.ils
rce/skill++/RCEBATCH.ils
rce/skill++/RCEPROFILE.ils
rce/skill/RCE_toml.il
rce/skill/RCE_envOptions.il
rce/skill/RCE_dspfAnalyzer.il
rce/skill/RCE_dspfLauncher.il
rce/python/rce
rce/python/rcepy/dspf_gui/app.py

lef/skill++/LEF.ils
lef/skill++/LEFCB.ils
lef/skill++/LEFCFG.ils
lef/skill++/LEFGUI.ils
lef/skill++/LEFLOAD.ils
lef/skill++/LEFOPT.ils
lef/skill++/LEFPROFILE.ils
lef/skill++/LEFRUN.ils
lef/skill/UI_lefSummary.il
lef/python/lef
```

`common/python/cad-batch`、`common/python/cad-lsf`、`common/python/cad-profile`
和 `lef/python/lef` 必须保持可执行权限。
仓库根目录对应生产安装树中的
`$CAD_HOME/tools`。
当前 flow discovery 只使用 `common/python/cad-lsf` 的 versioned TSV；旧 shell
discovery、RCE batch/LSF 转发器和旧 GUI loader 不再随生产包提供。

## 3. Revision 基线

安装完成后应得到：

```text
drcLoaderRevision()              => "20260818.temp.permissions.v2"
drcFrontendRevision()            => "20260818.temp.permissions.v2"
lvsLoaderRevision()              => "20260818.temp.permissions.v2"
lvsFrontendRevision()            => "20260818.temp.permissions.v2"
rceLoaderRevision()              => "20260818.temp.permissions.v2"
rceFrontendRevision()            => "20260818.temp.permissions.v2"
rceConfigRevision()              => "20260818.temp.permissions.v2"
rceGuiRevision()                 => "20260818.temp.permissions.v2"
rceBaseGuiRevision()             => "20260820.prompt.labels.v2"
CAD_tomlRevision()               => "20260818.temp.permissions.v4"
CAD_envOptionsRevision()         => "20260812.common.v1"
CAD_lsfRevision()                => "20260820.rce.live.log.v10"
CAD_lsfMonitorRevision()         => "20260820.monitor.jobs.v4"
CAD_profileRevision()            => "20260819.profile.strict.v2"
CAD_profileGuiRevision()         => "20260818.profile.gui.v2"
drcProfileRevision()             => "20260820.rce.live.log.v4"
lvsProfileRevision()             => "20260820.rce.live.log.v4"
lvsRveLauncherRevision()         => "20260828.prompt.return.v1"
rceProfileRevision()             => "20260820.rce.live.log.v4"
lefProfileRevision()             => "20260820.rce.live.log.v4"
cadBaseHardwareRevision()        => "20260820.lsf.monitor.v7"
lefHardwareGuiRevision()         => "20260820.lsf.monitor.v1"
lefLoaderRevision()              => "20260818.temp.permissions.v2"
lefFrontendRevision()            => "20260818.temp.permissions.v2"
cadBatchAdapterRevision()        => "20260812.common.v1"
cadBatchGuiSharedRevision()      => "20260812.common.v1"
cadBatchRowsRevision()           => "20260812.common.v1"
cadBatchCoreRevision()           => "20260813.temp.path.v2"
cadBatchMonitorRevision()        => "20260812.common.v1"
cadBatchGuiRevision()            => "20260812.common.v1"
rceBatchAdapterRevision()        => "20260812.common.v1"
cadBaseLvsCustomSvrfRevision()   => "20260812.custom.svrf.align.v2"
cadBaseCustomSvrfRevision()      => "20260813.custom.svrf.common.v1"
drcCustomSvrfRevision()          => "20260813.custom.svrf.common.v1"
drcCustomSvrfGuiRevision()       => "20260813.custom.svrf.common.v1"
drcRuleSelectRevision()          => "20260814.rule.select.pyqt.v5"
drcRuleSelectGuiRevision()       => "20260814.rule.select.pyqt.v1"
lvsCustomSvrfRevision()          => "20260813.custom.svrf.common.v1"
lvsCustomSvrfGuiRevision()       => "20260813.custom.svrf.common.v1"
rceCustomSvrfRevision()          => "20260813.custom.svrf.common.v1"
rceCustomSvrfGuiRevision()       => "20260813.custom.svrf.common.v1"
cadBaseRuleFileLabelRevision()   => "20260812.rule.file.label.v1"
drcRuleFileGuiRevision()         => "20260812.rule.file.label.v1"
drcAbsolutePathRevision()        => "20260812.toml.absolute.paths.v1"
lvsAbsolutePathRevision()        => "20260812.toml.absolute.paths.v1"
lvsStageAbsolutePathRevision()   => "20260812.toml.absolute.paths.v1"
rceAbsolutePathRevision()        => "20260812.toml.absolute.paths.v1"
cadBaseCdlIncludeRevision()      => "20260812.cdl.include.v2"
lvsCdlIncludeRevision()          => "20260812.cdl.include.v2"
lvsCdlIncludeConfigRevision()    => "20260812.cdl.include.v2"
lvsCdlIncludeGuiRevision()       => "20260812.cdl.include.layout.v3"
lvsCdlIncludeBatchRevision()     => "20260812.cdl.include.v2"
lvsStageCdlIncludeRevision()     => "20260812.cdl.include.v2"
lvsStageCdlIncludeConfigRevision()=> "20260812.cdl.include.v2"
lvsStageCdlIncludeGuiRevision()  => "20260812.cdl.include.v2"
rceCdlIncludeRevision()          => "20260812.cdl.include.v2"
rceCdlIncludeConfigRevision()    => "20260812.cdl.include.v2"
rceCdlIncludeGuiRevision()       => "20260812.cdl.include.layout.v3"
rceCdlIncludeBatchRevision()     => "20260812.cdl.include.v2"
rceDspfLauncherRevision()        => "20260814.dspf.launcher.v5"
CAD_guiProtocolRevision()        => "20260814.cadgui.v1"
```

## 4. 部署前检查

1. 停止新的 DRC/LVS/RCE 提交，等待活动 batch 完成或从 summary 中取消并确认 LSF
   job 已结束。运行中不要替换 Python 或 SKILL 文件。
2. 记录当前生产 release、`SOURCE-RUNTIME-MANIFEST.txt` 中的 `cad_commit`，并保留上一
   个完整安装包用于回退。
3. 确认 `$CAD_HOME` 指向包含 `tools/` 和 `scripts/` 的安装根目录：

   ```bash
   test -n "$CAD_HOME"
   test -f "$CAD_HOME/tools/cadToolRegister.il"
   test -L "$CAD_HOME/scripts/cadAutoLoad.il"
   ```

4. 生产 Python 应为 3.9 或更新版本。推荐统一设置：

   ```bash
   export CAD_PYTHON=/App/others/python3.9.13/bin/python3
   "$CAD_PYTHON" -c 'import concurrent.futures, importlib.util, json, sys; assert sys.version_info >= (3, 9); assert sys.version_info >= (3, 11) or importlib.util.find_spec("tomli")'
   PYTHONNOUSERSITE=1 "$CAD_PYTHON" -c 'import PyQt5.QtCore'
   ```

   Python 3.11 及以后使用标准库 `tomllib`，无需外部 `tomli`。DRC Rule Select
   使用 PyQt5；`PYTHONNOUSERSITE=1` 检查确保依赖来自共享安装而不是发布帐号的
   `$HOME/.local`。
5. 分别确认当前 flow 的 `DRC_DB_DIR`、`LVS_DB_DIR`、
   `RCE_DB_DIR` 或表单选择的输出根目录可写，并允许建立 `.cad-batches`；
   flow 不会借用其他 flow 的目录变量。
6. LSF 环境应能执行 `bsub` 和 `bkill`。站点可用 `SICO_LSF_BSUB` 和
   `SICO_LSF_BKILL` 覆盖默认命令；Queue/Host 必须来自当前用户的 Python discovery。
7. RCE 会创建或更新 OA view 时，目标 library 必须可写，并具备项目要求的 OA/DM
   备份或 checkpoint。
8. 自定义 SVRF 会直接改变 Calibre 控制文件语义，只允许经过项目审批的规则内容进入
   生产运行。部署动作不会替用户验证 SVRF 语义。

## 5. 完整包安装

发布包名遵循：

```text
cad-tools-source-runtime-rhel7-rhel8-20260812-full-v6-<commit>-x86_64.tar.gz
```

先校验 SHA256，并在独立 staging 目录解包：

```bash
sha256sum -c cad-tools-source-runtime-rhel7-rhel8-20260812-full-v6-*.tar.gz.sha256
mkdir -p /path/to/release-staging
tar -xzf cad-tools-source-runtime-rhel7-rhel8-20260812-full-v6-*.tar.gz \
  -C /path/to/release-staging
```

归档内唯一顶层目录是 `cad-tools-source-runtime-rhel7-rhel8-x86_64`，其中包含
`tools/` 和 `scripts/`。推荐由现有发布系统把该顶层目录作为一个 release 原子发布，
并通过版本目录或受控 symlink 切换 `$CAD_HOME`。不要在活动 Virtuoso batch 运行时
就地覆盖。

安装后保持 bootstrap 布局：

```text
$CAD_HOME/scripts/cadAutoLoad.il -> ../tools/deploy/cadAutoLoad.il.src
```

若生产系统使用固定 `$CAD_HOME` 做受控文件同步，先完整备份旧树，再同步归档中的
`tools/` 和 `scripts/`；必须保留源文件 mode，尤其是
`common/python/cad-batch`、`common/python/cad-lsf`、`common/python/cad-profile`、
`lef/python/lef` 的 0755。

## 6. 包完整性验收

`SOURCE-RUNTIME-MANIFEST.txt` 记录由 clean Git `HEAD` 经 `git archive` 导出的精确
commit。只有已经提交到该 `HEAD` 的文件会进入生产包；新增 Profile 文件必须先
`git add` 并提交，打包器会拒绝 dirty worktree。安装后执行：

```bash
grep '^cad_commit=' "$CAD_HOME/tools/SOURCE-RUNTIME-MANIFEST.txt"
test -x "$CAD_HOME/tools/common/python/cad-batch"
test -x "$CAD_HOME/tools/common/python/cad-lsf"
test -x "$CAD_HOME/tools/common/python/cad-profile"
test -f "$CAD_HOME/tools/common/skill/CAD_batchCore.il"
test -f "$CAD_HOME/tools/common/skill/CAD_batchAdapter.il"
test -f "$CAD_HOME/tools/common/skill/CAD_guiProtocol.il"
test -f "$CAD_HOME/tools/common/skill/CAD_profile.il"
test -f "$CAD_HOME/tools/common/skill++/PROFILEGUI.ils"
test -f "$CAD_HOME/tools/common/python/cadgui/protocol.py"
for profile_module in __init__.py cli.py compat.py model.py skill_data.py; do
  test -f "$CAD_HOME/tools/common/python/cadprofile/$profile_module"
done
for lsf_module in __init__.py cache.py cli.py collector.py model.py policy.py; do
  test -f "$CAD_HOME/tools/common/python/cadlsf/$lsf_module"
done
for lsf_gui_module in __init__.py app.py main_window.py models.py selector.py workers.py; do
  test -f "$CAD_HOME/tools/common/python/cadlsf/gui/$lsf_gui_module"
done
test -f "$CAD_HOME/tools/common/skill/CAD_lsfMonitor.il"
test -f "$CAD_HOME/tools/share/close.png"
test -f "$CAD_HOME/tools/share/monitor.png"
test -f "$CAD_HOME/tools/common/docs/LSF_LOAD_MONITOR_PLAN.md"
test -f "$CAD_HOME/tools/drc/skill++/DRCPROFILE.ils"
test -f "$CAD_HOME/tools/lvs/skill++/LVSPROFILE.ils"
test -f "$CAD_HOME/tools/rce/skill++/RCEPROFILE.ils"
test -f "$CAD_HOME/tools/lef/skill++/LEFPROFILE.ils"
test -f "$CAD_HOME/tools/lef/skill/UI_lefSummary.il"
test -x "$CAD_HOME/tools/lef/python/lef"
test -x "$CAD_HOME/tools/drc/python/drc"
test -x "$CAD_HOME/tools/rce/python/rce"
test -f "$CAD_HOME/tools/rce/skill++/RCEBATCH.ils"
test -f "$CAD_HOME/tools/rce/skill/RCE_dspfLauncher.il"
test -f "$CAD_HOME/tools/rce/python/rcepy/gen_lvs.py"
test -f "$CAD_HOME/tools/rce/python/rcepy/gen_xrc.py"
test -f "$CAD_HOME/tools/common/python/cadstage/command_files.py"
test -f "$CAD_HOME/tools/common/python/cadstage/process.py"
test -f "$CAD_HOME/tools/common/python/cadcalibre/lvs.py"
CAD_RUNTIME="${CAD_PYTHON:-${CAD_PYTHON_ROOT:+${CAD_PYTHON_ROOT%/}/bin/python3}}"
CAD_RUNTIME="${CAD_RUNTIME:-python3}"
"$CAD_RUNTIME" "$CAD_HOME/tools/common/python/cad-batch" --help
"$CAD_RUNTIME" "$CAD_HOME/tools/common/python/cad-lsf" --help
"$CAD_RUNTIME" "$CAD_HOME/tools/common/python/cad-profile" --help
"$CAD_RUNTIME" "$CAD_HOME/tools/drc/python/drc" --help
"$CAD_RUNTIME" "$CAD_HOME/tools/rce/python/rce" --help
```

包内 `cad_commit` 必须与发布审批记录一致，不能继续使用不含本次布局、路径和标签修复的
`20260812-full-v5` 包。

## 7. Virtuoso 加载与 Revision 验收

生产验收优先使用新 Virtuoso 会话。标准 `.cdsinit` 入口保持不变：

```skill
cadHome=(getShellEnvVar "CAD_HOME")
(load (strcat cadHome "/scripts/cadAutoLoad.il"))
```

已有会话热加载前必须关闭旧 DRC/LVS/RCE/LEF 表单和 multi-cell summary，然后依次加载：

```skill
cadHome=(getShellEnvVar "CAD_HOME")
(load (strcat cadHome "/tools/drc/skill++/DRC.ils"))
(load (strcat cadHome "/tools/lvs/skill++/LVS.ils"))
(load (strcat cadHome "/tools/rce/skill++/RCE.ils"))
(load (strcat cadHome "/tools/lef/skill++/LEF.ils"))
```

在 CIW 中检查第 3 节全部 revision，并确认没有 `*Error*` 或 reader warning。

## 8. 功能验收

使用两个经过批准的小测试 cell，按以下顺序验收：

1. 分别打开 DRC/LVS/RCE，确认默认 `Single Cell` 时 mapping 和 `Parallel Cells`
   隐藏，原输入字段可用。
2. 分别在 DRC/LVS/RCE/LEF 顶部使用 `Save As...` 保存 Profile，修改至少一个输入后
   Load 并确认完整恢复；检查文件含 `[cad_config]`、`version = 1` 和正确 `flow`。
   再选择一个不含 `[cad_config]` 的运行 TOML，Load 必须拒绝且表单状态保持不变。
3. 切换 `Multiple Cells`，确认 mapping 和 `Parallel Cells` 显示；LVS/RCE Input Type
   自动变为 OA 且不可编辑。
4. DRC 加入两条 Layout L/C/V；LVS/RCE 加入两条 Schematic/Layout 配对行。验证
   未进入编辑态时 `Add / Update` 始终追加新行，双击后可更新指定行，右键
   `Delete Selected Mapping` 可删除选中行，且界面没有 `Remove/Clear` 按钮。
5. 使用 `Current Host`、`Parallel Cells=2` 启动，确认两个任务并发、单任务失败不会
   阻止其他任务、summary 能打开 result/log。
6. 检查 `<output>/.cad-batches/<batch-id>/status.json` 和 `status.tsv`，以及每个 run
   directory 下的 launch log。
7. RCE 选择一个需要 OA view 的输出，确认提取可并行，但 summary 进入 Publishing
   后由 CIW 串行创建 view，并通过 OA stamp 校验。
8. 使用测试 LSF queue 重复两 cell 运行并取消，确认 job name 唯一、`bkill -J`
   生效，最终状态为 Canceled。
9. 在 DRC、LVS 和 RCE 中确认 `Customized SVRF Command` 默认未勾选且文本框隐藏；勾选后
   文本框显示并左对齐。输入两行测试 SVRF，确认其位于生成控制文件末尾；取消勾选后
   即使文本仍存在也不写入。DRC/xRC 验收同时确认 PDK 原始 rule file 未被修改。
10. 在 DRC 中选择已知含 `GROUP` 的 rule file，勾选 `Rule Select` 并打开 `Select...`。
   确认独立 PyQt5 两级树的 group 名和 check 数量正确，group 可展开；点击 group 后
   所有子 check 均被选中，取消其中一条后 group 显示部分选择。确认 group/check
   筛选不会丢失选择，并确认 Apply 后窗口关闭、Cancel/标题栏关闭不改变原选择。
   选择完整 `GAA` 及 `GGT` 中的 `GT_1` 后运行，确认 `log/drc.cal` 在自定义 SVRF
   之前包含 `DRC SELECT CHECK "GAA" "GT_1"`；TVF deck 中应位于 `tvf::VERBATIM`
   内。更换 rule file 应清空选择；启用但未选择
   group 或 check 应拒绝提交；PDK 原始 rule file 应保持不变。
11. 在 RCE/LVS 中依次选择 `OA`、`SCH+GDS`、`CDL+GDS`、`CDL+LAY`，确认
   `CDL Include` 仅在前两种输入显示。设置测试 header 后勾选运行，确认生成的
   `si.env` 中 `incFILE` 为所选绝对路径；取消勾选后应为 `incFILE = ""`。在
   Export CDL 中重复勾选和取消勾选测试。Multiple Cells 模式下还应确认该行位于
   mapping 框之后，是 INPUT 区域最后一行。
12. 确认 DRC/LVS/RCE 的规则选择标签均显示为 `Rule File`。从 Virtuoso 启动目录以
    相对路径选择 rule、Hcell、CDL Include、layer map 或 RCE process/selection 文件，
    将输出设到其他目录后生成 TOML，确认相应 path key 均为启动目录下的绝对路径。
13. 返回 `Single Cell`，确认 LVS/RCE 恢复切换批量模式前的 Input Type，单 cell 流程
    仍按原方式运行。

## 9. 运行与数据约束

- cell mapping 之外的所有表单参数由整个 batch 共用。
- `Parallel Cells` 乘以单任务 CPU 设置是潜在最大资源量，上线时应符合 queue policy。
- 所有 TOML 成功生成且冲突检查通过后才备份并启动。若某个备份动作失败，不会启动
  controller，但此前已成功轮转的其他 run directory 可能已经生成时间戳备份。
- 一个 batch 内重复 run directory、config 或 RCE OA target 会被拒绝；不同 Virtuoso
  会话之间仍需由项目流程避免写同一目标。
- 取消时已经进入 RCE Publishing 的成功任务仍会完成 OA publication；只要存在被取消
  的任务，batch 最终状态保持 Canceled。
- `Customized SVRF Command` 是原文透传，不执行格式化、语法修复或规则冲突检查；
  Virtuoso 多行字段上限为 8191 bytes。DRC compile-time TVF 只额外添加必要的
  `tvf::VERBATIM` 包装；文本内容仍不改写。
- DRC `Rule Select` 中显示的 check 列表和数量是选择预览；实际选择语义由 Calibre 编译
  rule file 决定。调用 `calibre -E` 需要站点可用的 TVF 预处理许可证，但不会运行
  layout DRC。
- GUI 相对路径以生成 TOML 时的 Virtuoso `pwd()` 为基准固化；更改 CIW 当前目录后应
  重新选择相对文件或直接使用绝对路径。
- 代码发布和回退不会自动修改或清理 `.cad-batches`、run data 或已生成的 OA views。
- CAD 脚本临时文件统一位于启动 Virtuoso 时的 `$CWD/.cad`；EDA stage 恢复原临时
  环境。最终 run data、`.cad-batches` 状态、显式 output/cache，以及为原子替换而
  必须与目标文件同目录的 staging 不迁移。

## 10. 回退方案

1. 停止新任务，取消或等待所有活动 batch 完成，并确认 LSF 无残留 job。
2. 将整个 `$CAD_HOME` release 切回上一份已验收的完整 source-runtime 包；不要只回退
   单个 GUI 或 Python 文件。
3. 新启动 Virtuoso，检查上一 release 的 revision 和 Single Cell 基线。
4. 代码回退不会撤销已生成的数据。DRC/LVS/RCE run directory 可按时间戳备份恢复；
   RCE 已创建或更新的 OA view 必须从 OA library 备份、DM/GDM checkpoint 或项目版本
   管理中恢复。
5. 保留失败批次的 `.cad-batches/<batch-id>`、task logs 和 LSF job 记录用于审计。

## 11. 发布验证基线

提交和打包前至少执行：

```text
pytest -q common/python/tests
RCE_RUN_SKILL_PROBE=1 pytest -q common/python/tests
pytest -q drc/python/tests
pytest -q lef/python/tests
pytest -q rce/python/tests
python3 utility/skill_style.py --check
git diff --check
```

真实 Cadence 验证应同时覆盖 DRC/LVS/RCE loader 的 `dbAccess` reader-clean probe、
Virtuoso `-nograph` 表单实例化，以及 `cadChangeTechLib` Core/Via 任意顺序重复加载。
所有新增 SKILL 文件和拆分 helper 必须通过全局 procedure 唯一性与 load-safety 检查。
