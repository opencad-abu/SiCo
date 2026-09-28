# cadChangeTechLib 生产部署安装报告

## 1. 发布信息

- 发布日期：2026-08-11
- 工具入口：`SiCo -> Change Tech Library...`
- 部署范围：Virtuoso CIW、Layout Editor、Schematic Editor
- 运行时依赖：Cadence Virtuoso SKILL、OpenAccess、目标 PDK technology library
- 验证环境：Cadence Virtuoso IC23.10.130

本版本提供 instance master 和 standard via master 的多行映射替换。Design Cell、
instance source 字段及 via source 字段支持完整名称正则表达式；单独的 `*` 表示匹配
所有名称。Via target ViaDef 为 `*` 时，在目标 technology library 中复用每个匹配到
的 source ViaDef 名称。

## 2. 运行时文件清单

生产部署必须同时更新以下文件，不能只复制表单文件：

```text
utility/menu.toml
utility/menu.generated.il
utility/skill/cadChangeTechLib.il
utility/skill/cadChangeTechLibVia.il
utility/skill/cadChangeTechLibGui.il
utility/skill/cadChangeTechLibViaGui.il
utility/skill/cadChangeTechLibForm.il
```

仓库根目录对应生产安装树中的 `$CAD_HOME/tools`。测试和文档文件不参与 Virtuoso
运行，但建议随发布版本保留，便于验收和审计。

## 3. 发布版本基线

安装完成后，各模块 revision 应为：

```text
cadChangeTechLibCoreRevision()    => "20260811.mapping.v7"
cadChangeTechLibViaRevision()     => "20260814.via.v6"
cadChangeTechLibGuiRevision()     => "20260811.gui.v10"
cadChangeTechLibViaGuiRevision()  => "20260811.viagui.v5"
cadChangeTechLibFormRevision()    => "20260811.form.v11"
```

## 4. 部署前检查

1. 确认生产环境的 `CAD_HOME` 指向安装根目录，而不是 Git 仓库根目录：

   ```bash
   test -n "$CAD_HOME"
   test -f "$CAD_HOME/tools/cadToolRegister.il"
   ```

2. 备份当前 `$CAD_HOME/tools/utility`，或记录当前生产 Git commit/tag。
3. 确认目标 PDK library 已在生产 `cds.lib` 中定义并可被 Virtuoso 打开。
4. 对将要修改的 OA design library 建立备份或确认版本管理 checkpoint 可恢复。
5. 首次上线不要直接使用 Design Cell `*`；先在复制的测试 library 和单个 cell 上验收。

## 5. 安装步骤

推荐通过现有发布系统把本 release commit 完整同步到 `$CAD_HOME/tools`。如果使用文件
级发布，必须原子更新第 2 节列出的全部运行时文件，并保持普通用户可读：

```bash
chmod 0644 "$CAD_HOME/tools/utility/menu.toml" \
  "$CAD_HOME/tools/utility/menu.generated.il" \
  "$CAD_HOME/tools/utility/skill/cadChangeTechLib"*.il
```

生产 bootstrap 应保持为以下布局：

```text
$CAD_HOME/scripts/cadAutoLoad.il -> ../tools/deploy/cadAutoLoad.il.src
```

若尚未建立该入口，可执行：

```bash
mkdir -p "$CAD_HOME/scripts"
ln -s ../tools/deploy/cadAutoLoad.il.src "$CAD_HOME/scripts/cadAutoLoad.il"
```

不要把 `cadAutoLoad.il` 放入 `$CAD_HOME/tools/scripts`。

## 6. Virtuoso 加载

新启动的 Virtuoso 在 `.cdsinit` 中加载标准入口：

```skill
cadHome=(getShellEnvVar "CAD_HOME")
(load (strcat cadHome "/scripts/cadAutoLoad.il"))
```

仅安装公共菜单、不加载项目 SKILL 时，可以改为：

```skill
cadHome=(getShellEnvVar "CAD_HOME")
(load (strcat cadHome "/tools/cadToolRegister.il"))
```

已有 Virtuoso 会话可在 CIW 中热更新：

```skill
cadHome=(getShellEnvVar "CAD_HOME")
(load (strcat cadHome "/tools/utility/skill/cadChangeTechLibForm.il"))
(when (isCallable 'witMenuReload) (witMenuReload))
```

版本 guard 会关闭旧表单并重新加载发生变化的模块。正在执行 Apply 时不要热更新。

## 7. 安装验收

在 CIW 执行：

```skill
list(
  cadChangeTechLibCoreRevision()
  cadChangeTechLibViaRevision()
  cadChangeTechLibGuiRevision()
  cadChangeTechLibViaGuiRevision()
  cadChangeTechLibFormRevision()
)
```

然后完成以下人工检查：

1. CIW、Layout Editor 和 Schematic Editor 的 SiCo 菜单均包含
   `Change Tech Library...`。
2. Design View 为 schematic 时不显示 `Update Via Master` 和 Via Mapping 区域。
3. Design View 为 layout 时显示未勾选的 `Update Via Master`，Via Mapping 区域隐藏。
4. 勾选后显示 Via Mapping 区域，取消勾选后再次隐藏。
5. 选择 source/target technology library 后，ViaDef 下拉列表能够读取该技术库的
   `viaDefs`，并允许手工输入 `*` 或 source 正则表达式。
6. 在测试 library 的单个 cell 上分别验证 instance mapping 和 via mapping；检查 CIW
   中的 `[cadChangeTechLib]` matched、changed、success 和 error 输出。
7. 验证修改后的 schematic 通过 `schCheck`，layout 能保存并重新打开，via 的 origin、
   orient 和 override parameters 保持不变。

## 8. 生产使用约束

- Apply 会以 append/edit 模式打开并保存匹配到的 cellView，不是只读操作。
- 多条 mapping 同时匹配时第一条生效；应把精确规则放在宽泛正则或 `*` 规则之前。
- Design Cell `*` 会处理 design library 中所有具有指定 view 的 cell，必须经过变更审批。
- Target instance lib/cell/view 必须存在。
- Target ViaDef 必须存在于目标 technology library，且目标 technology 必须位于 design
  cellView 的 technology graph 中，否则 `dbCreateVia` 会失败并在 CIW 报错。
- Via 替换先创建目标 via，成功后才删除源 via；单个对象失败会被记录，但已成功修改的
  其他对象仍可能保存。因此批量运行前必须具备 OA 数据备份。

## 9. 回退方案

1. 停止新的 Apply 操作并关闭 Change Tech Library 表单。
2. 将第 2 节运行时文件恢复到上一生产 release，然后重新加载
   `$CAD_HOME/tools/utility/skill/cadChangeTechLibForm.il` 和 `witMenuReload()`。
3. 代码回退只恢复工具，不会撤销已经 `dbSave` 的 OA 数据。已修改的 cellView 必须从
   OA library 备份、DM/GDM checkpoint 或项目版本管理中恢复。
4. 回退后重新执行第 7 节的菜单和 revision 检查，并在测试 cell 上确认旧流程可用。

## 10. 发布验证记录

本 release 在提交前执行：

```text
python3 utility/skill_style.py --check
pytest -q rce/python/tests/test_change_tech_lib.py rce/python/tests/test_menu_bootstrap.py
git diff --check
```

同时使用真实 Virtuoso 验证了 SKILL 加载、表单实例化、ViaDef ComboField、正则匹配、
版本热更新，以及 layout 未勾选/勾选/取消勾选三个 Via Mapping 显示状态。
