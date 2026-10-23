# SiCo

本仓库包含 SiCo 源码、开发工具和验证入口。当前版本与使用文档见
[现行文档索引](docs/SICO_CURRENT_DOCUMENTATION.md)，Agent 与 Python host 说明见
[SiCo 模块 README](tools/sico/python/sico/README.md)。

## 开发入口

开发在 `/path/to/sico-src/` 或该仓库下的专用工作树进行；
`/software/sico/latest` 仅用于测试已合入 `main` 的代码。
开发、验证、合入和测试树同步要求见 [AGENTS.md](AGENTS.md)，
环境配置见 [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md)。

## SKILL 文件与 JSON 清单维护（必做）

**后续新增任何项目 SKILL/SKILL++ 代码文件（`.il`、`.ils`），都必须在同一变更中
同步对应的 SKILL JSON 清单及受影响的生成入口，并确认相关编译输入覆盖该文件。
未完成登记、生成和检查，不视为开发完成，不得合入或交付。**
移动、重命名、删除文件时，同样必须同步清单，清除旧路径和重复归属。

### 登记源码加载归属

[tools/utility/skill_loader/manifest.json](tools/utility/skill_loader/manifest.json)
是源码加载的权威清单，声明功能组、组依赖、成员顺序、入口和初始化函数。
新增实现应放入对应功能组；新增功能组须声明依赖和加载顺序。
共享组通过 `source_manifest` 引用下列清单，只维护所引用的成员清单，避免重复登记：

| 代码归属 | 成员清单 |
| --- | --- |
| AI 共享实现 | [tools/utility/ai_shared_sources.json](tools/utility/ai_shared_sources.json) |
| 通用 GUI 实现 | [deploy/common_gui_sources.json](deploy/common_gui_sources.json) |
| 通用 LSF 实现 | [deploy/common_lsf_sources.json](deploy/common_lsf_sources.json) |

新增源码目录时，必须同步主清单的 `source_directories`，使遗漏文件检查覆盖新目录。
独立 worker、context 初始化等具有单独生命周期的文件，须明确登记主清单中的
`excluded` 理由和 [skill_load_ownership.json](tools/utility/skill_load_ownership.json)
中的相应生命周期；不得以排除项绕过普通功能实现的登记。

在仓库根目录重新生成并检查加载入口；已有源码修改后也须执行，以更新内容摘要：

```sh
python3 tools/utility/skill_loader.py --write
python3 tools/utility/skill_loader.py --check
```

不得手工修改生成入口，或添加零散 `load` 调用绕过统一加载器。
完整加载规则、失败重试和生命周期边界见 [SKILL 加载说明](docs/SICO_SKILL_LOADING.md)。

### 确认编译输入覆盖

**源码加载清单目前不会自动更新所有 context 的编译输入。** 新增文件必须检查它所
归属的编译目标；涉及显式列表的，同时更新对应 JSON 或构建器的输入列表：

| 编译目标 | 需要维护或核对的入口 |
| --- | --- |
| 非 AI 前端及其共享实现 | [deploy/nonai_skill_sources.json](deploy/nonai_skill_sources.json) |
| 共享 catalog 与 MTS worker | [deploy/worker_skill_sources.json](deploy/worker_skill_sources.json) |
| AI / SiCo context | [deploy/build_ai_skill_context.py](deploy/build_ai_skill_context.py)：读取生成入口中的顺序并扫描当前指定源码目录；共享依赖仍有显式列表，新增目录或依赖须核对覆盖与顺序 |
| PDK worker | [deploy/build_pdk_worker_context.py](deploy/build_pdk_worker_context.py) 的 `SOURCES` |
| Runtime bootstrap | [deploy/build_runtime_bootstrap.py](deploy/build_runtime_bootstrap.py) 的显式源码列表 |

即使某个构建器能扫描到新增文件，也必须完成源码清单登记，不能以自动扫描替代归属管理。
实际编译交付时须重新构建受影响的 `.cxt`，核对 `.build.json` 中的输入路径与摘要，
并在新 Virtuoso 进程验收；已有 context 不会因新增源文件而自动包含新代码。
正式发行遵守 [运行时发行政策](deploy/RUNTIME_RELEASE_POLICY.md)。

### 提交前检查

- `skill_loader.py --check` 必须通过，并运行受影响的加载和编译输入回归。
- 合入前运行 `python3 tools/utility/engineering_gate.py --base-ref <本次变更前的目标提交>`，
  按[工程门禁](tools/utility/A_PHILOSOPHY_GATE.md#current-source-merge-gate-eq-05-2026-10-15)
  判定结果；`accepted_debt` 仅表示存量豁免有效，不等于严格通过。
- 源文件、相关 JSON、构建输入调整及生成入口必须随同一次变更提交；合入 `main` 后，
  从明确的主线提交一并同步到测试树，避免源码、清单和入口版本不一致。
