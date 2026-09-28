# SiCo · 矽科

SiCo（矽科）是基于 Codex CLI 的定制集成电路设计平台智能助手：以 Silicon
Copilot 会话为核心，配套 Cadence Virtuoso 流程工具集——统一的工具菜单入口，
DRC / LVS / RCE / MTS 等流程封装，LEF / GDS / CDL 导出，LSF 运行监控。
SiCo的最终目标是多平台定制电路设计AI助手，能够协助电路/版图设计工程师快速完成工作。
- 中文使用手册（离线 HTML）：[docs/html/index.html](docs/html/index.html)

## 仓库结构

| 路径 | 内容 |
| --- | --- |
| `bin/` | 命令入口（sico、drc、lvs、rce、dspfana、lefgen、gdsout、cdlout、mts-netlistor、lsfmonitor、nl2view 等） |
| `tools/sico/` | Silicon Copilot 桌面与会话服务（Python/PyQt5 + SKILL） |
| `tools/common/` | 公共 Python 库与 SKILL 组件（批处理、LSF、Calibre、配置、视图工具） |
| `tools/drc/` `tools/lvs/` | Calibre DRC / LVS 流程封装 |
| `tools/rce/` | 寄生提取流程（RCE / DSPF 分析） |
| `tools/mtsnl/` | MTS 多工艺网表（mts-netlistor） |
| `tools/lef/` | LEF 生成（Abstract Generator replay 流程） |
| `tools/utility/` | 站点菜单注册、多单元部署等实用工具 |
| `etc/` | flow 默认配置（Calibre SVRF、cdl.env、Quantus / StarRC / streamout 选项） |
| `examples/` | `bashrc` / `cshrc` / `cdsinit` 集成示例 |
| `share/` | 图标、QTermWidget 配色与键盘布局、第三方许可证文本 |
| `docs/html/` | 使用手册：安装、环境变量、Virtuoso 接入、CAD flows、DRC/LVS/RCE/MTS/LEF、LSF 等 |

## 环境要求

- Linux x86_64
- Cadence Virtuoso / OpenAccess（当前验证版本 IC23.1）
- Calibre / Quantus / StarRC / Abstract（按所用 flow 配置；SiCo 不代替这些 EDA 工具）
- Python ≥ 3.9 与 PyQt5（图形组件）
- 集群运行需要 LSF；MTS 项目选择需要 Environment Modules

## 当前公开范围

本仓库是 SiCo 的公开源码子集：不包含 AI 参考 / 培训数据、内部部署脚本和内部计划
文档。AI 运行时（OpenAI Codex CLI 0.156.1、ripgrep 15.2.0）以发行附件提供，见
[tools/ai/runtime/README.md](tools/ai/runtime/README.md)。Silicon Copilot 的部分能力
依赖尚未公开的 AI 组件（`cadai`、`aiassistant`、`aivw`）；手册所述的二进制运行包
同样不在本仓库内。

## 作者

SiCo 的作者（按顺序）：GPT、DeepSeek、OpenCAD。

## 许可证

GPL-3.0（见 [LICENSE](LICENSE)）。第三方组件遵循各自许可证：PyQt5（GPL-3.0，
作为运行依赖使用）、QTermWidget（GPL-2.0+，仓库内仅含其配色 / 键盘布局数据与
许可证文本）、vendored `tomli`（MIT，见 `tools/mtsnl/python/mtsnetlistor/_vendor/tomli/`）。
