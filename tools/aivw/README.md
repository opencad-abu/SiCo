# AI Verification Workbench

AI Verification Workbench 是面向 Virtuoso、Xcelium、Spectre 和 SystemVerilog/Verilog-A
的通用混合信号自动化验证平台。ADC、PLL、comparator 和 Cadence LAB 只是 qualification/
回归样本，不是产品边界。产品命令固定为：

```text
aivw
```

源码目录为 `tools/aivw/`；文档产品名称保留 `ai-verification-workbench`（AI Verification Workbench）。

当前 `0.3.0-dev4` 在原 M0/M1 垂直样板上新增 recipe schema v1、fail-closed recipe loader、
workflow DAG、可绑定的 executor/model-class registry、依赖闭包执行、双 manifest 绑定和
control/payload split storage。首份 `saradc.comparator_new` recipe 是通用框架回归输入，
不是 CLI 或 kernel 中的专用分支。M0-E 与 M0-S 已各完成两次正式 PASS；M1 已产生首次真实
配对 correlation 结果，新的 RNM/Xcelium run 也已迁移到 split storage。

Agent foundation 的固定解释器与库政策见
[实施计划中的“Python 库约束（固定安装前缀）”](docs/IMPLEMENTATION_PLAN_LDO_MVP.md)，
实测版本、能力探测和 relocation/launcher 结果见
[M0 Agent Foundation Acceptance](docs/M0_AGENT_FOUNDATION_ACCEPTANCE.md)。生产运行时采用
固定前缀库政策：`CAD_PYTHON_ROOT` 内已经安装的标准库、`site-packages`、`lib-dynload`、
native extension 和 PyQt5 均在允许范围内；仍禁止切换解释器、user site、外部路径注入和
运行时安装/下载依赖。Python 3.9.13 原生不含 `tomllib`；当前固定前缀提供并允许使用
`tomli 2.4.1`、`pytest 8.4.2` 和 `PyQt5 5.15.11`（以及同一前缀内其他已安装库），但
不得把缺失库通过网络或运行时安装补齐。

M1 离线 LDO deterministic slice 的 topology、experiment plan、metrics 和受控 provider
验收记录见 [M1 LDO Deterministic Acceptance](docs/M1_LDO_DETERMINISTIC_ACCEPTANCE.md)。
该 slice 只证明 controlled fixture 的流程与安全边界，不代表 Spectre 相关性或模拟质量
PASS。

M2 provider qualification、candidate/revision provenance、Bundle relocation、fake HTTP/
private AF_UNIX 边界和 Codex Core Profile 状态见
[M2 External Provider Acceptance](docs/M2_EXTERNAL_PROVIDER_ACCEPTANCE.md)。当前报告的
controlled qualification 已通过；真实外部模型服务 qualification 和真实 LDO analog
qualification 仍属于后续工作。

AIVW 后续可以经 authenticated cdns-ipc 创建或更新 recipe 明确授权的 SystemVerilog/
Verilog-A text view；schematic、symbol、config 和 ADE setup 不包含在这项权限中。候选必须先
在隔离区通过 gate，再以 expected-old-hash、发布前快照、写后读回和失败恢复方式发布。

M1-AI comparator nominal 与 default+45-PVT golden gate 已于 2026-08-30 各完成两次独立
正式 PASS；均使用批准的 Spectre 23.1，源 OA identity 不变。PVT gate 覆盖 46 points、
Offset 和 InputReferredNoise 两个 test；PVT 的两次正式 run 为
`20260829T234247.657745Z-m1-ai-pvt-cfc59fbb11bd` 和
`20260829T235814.252212Z-m1-ai-pvt-cfc59fbb11bd`。该结论仍不覆盖 AI RNM 相关性或
ADC 系统级签核。

## Control 与 EDA payload 目录契约

这里的 `$CWD` 表示用户启动 `aivw` 时的当前工作目录，不依赖名为 `CWD` 的 shell
环境变量。轻量控制面位于：

```text
$CWD/.aivw/runs/<run_id>/
  request.json / manifest.json / events.jsonl / report.json
```

Xcelium、Spectre、AMS、OCEAN、网表、波形、worklib 和仿真数据库等大体积工件位于：

```text
$PROJ_AMS_DB_DIR/<lib>.<cell>.<view>/runs/<run_id>/
```

当前 setup 静态导出 `PROJ_AMS_DB_DIR=/path/to/amsVerify/data/ams`。AIVW 不整体 source
setup，只读取白名单 export，并检查绝对路径、安全边界和可写性。Control 与 payload 以同一
run ID、request digest 和 manifest hash 绑定，所有 run 目录 exclusive-create。

历史 M0/M1 正式工件仍在 `.aivw` 中只读保留。Comparator RNM/Xcelium runner 已完成
split-storage 迁移；其他 legacy M0/M1 runner 仍在分批迁移，迁移前不应启动会把大体积
EDA payload 写入项目启动目录的新正式运行。

## 快速检查

从希望保存工件的目录启动：

```bash
export SICO_PYTHON_ROOT="/software/pkgs/python/3.9.13"
export SICO_PYTHON="$SICO_PYTHON_ROOT/bin/python3"

tools/aivw/bin/aivw info
tools/aivw/bin/aivw recipes
tools/aivw/bin/aivw recipe saradc.comparator_new
tools/aivw/bin/aivw run-recipe saradc.comparator_new --dry-run
tools/aivw/bin/aivw run-recipe saradc.comparator_new --through structure
tools/aivw/bin/aivw run-recipe saradc.comparator_new --through connectivity
tools/aivw/bin/aivw check-connectivity \
  --netlist /absolute/path/to/ihnl/cds5/netlist --map /absolute/path/to/map/current \
  --globalmap /absolute/path/to/ihnl/globalmap --candidate /absolute/path/to/structure.sv \
  --module comparator_new --port out --port outb --port cdsNet1 --port cdsNet0 --port clk
tools/aivw/bin/aivw run-recipe saradc.comparator_new --through model_check
tools/aivw/bin/aivw probe-text-view --profile amsverify
tools/aivw/bin/aivw verify-run \
  --control-manifest /absolute/path/.aivw/runs/<run_id>/manifest.json \
  --payload-manifest /absolute/path/data/ams/<lib>.<cell>.<view>/runs/<run_id>/payload-manifest.json
tools/aivw/bin/aivw pilot m0-e
tools/aivw/bin/aivw pilot m0-s
tools/aivw/bin/aivw doctor
tools/aivw/bin/aivw run m0-e
tools/aivw/bin/aivw run m0-s
tools/aivw/bin/aivw qualify m1-ai
tools/aivw/bin/aivw run m1-ai --gate golden-nominal
tools/aivw/bin/aivw run m1-ai --gate golden-pvt
tools/aivw/bin/aivw run m1-ai --gate rnm
tools/aivw/bin/aivw run m1-ai --gate rnm-evidence --rnm-source-manifest /absolute/path/to/m1-ai-rnm/manifest.json
tools/aivw/bin/aivw run m1-ai --gate spectre-evidence --spectre-source-manifest /absolute/path/to/m1-ai-golden/manifest.json
```

外部模型 provider 的资格化命令默认只做离线配置校验，不会连接网络或本地 gateway：

```bash
tools/aivw/bin/aivw qualify-external-provider \
  --provider http \
  --endpoint https://model.example.invalid/v1 \
  --allowed-host model.example.invalid \
  --api-key-env AIVW_MODEL_KEY \
  --report /absolute/path/external-provider-config.json \
  --json
```

只有项目批准的 live qualification 才能同时提供 `--live --approved`、非敏感
`--approval-id`、严格 JSON/JSONL `--requests` fixture 和显式 `--source-generation`；HTTP
还必须提供 `--allowed-host`，Unix provider 还必须提供私有 `0700` `--managed-root` 和
`0600` 当前用户 socket。`--template-lock` 与 `--expected-action-kind` 仅用于 live
qualification。config-only 模式明确拒绝 `--requests`、`--approval-id`、
`--expected-action-kind`、`--source-generation` 和 `--template-lock`，不会把 live
provenance 混入 `CONFIG_VALIDATED` 报告。`--approval-id` 只接受不超过 256 字符的 ASCII
变更引用（字母、数字和 `._:/@+-`），拒绝空白、控制字符、Unicode 和 credential-like
前缀；报告只保存 endpoint/socket digest 和 `SecretReference`，不会保存 endpoint 凭据或
环境变量值：

```bash
tools/aivw/bin/aivw qualify-external-provider \
  --provider unix \
  --socket /absolute/path/to/managed/model.sock \
  --managed-root /absolute/path/to/managed \
  --requests /absolute/path/requests.jsonl \
  --source-generation approved-source-generation \
  --template-lock sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --expected-action-kind FINISH \
  --live --approved --approval-id change-2026-09-02 \
  --report /absolute/path/external-provider-live.json \
  --json
```

`CONFIG_VALIDATED` 只证明 provider 配置和安全边界通过离线预检，不是模型响应或 LDO
质量的 `PASS`；真实外部 provider qualification 仍需项目批准的 endpoint/gateway 和
独立证据。

`doctor` 只读取 `/path/to/amsVerify/setup.bashrc` 中的 `module load` 声明和白名单
`PROJ_AMS_DB_DIR` literal export，不会 source 或执行该文件的其他内容。然后在一次性子
shell 中加载：

```text
ic/23.10.130
xcelium/23.09.002
spectre/23.10.538
```

检查通过需要实际二进制路径、工具自身报告版本、ADC/PLL `cds.lib`、Lab6 run file
及 PLL config/AMS state 同时匹配 profile。license、PATH、setup 或输入问题分别归入
`BLOCKED_ENVIRONMENT` 或 `BLOCKED_INPUT`，不会伪装成设计 PASS。

`run-recipe ... --dry-run` 校验 recipe、DAG、registry 和 split-storage 安全边界，创建
control/payload run envelope，并发布相互绑定的 manifest；它不会执行 EDA 或 AI gate，
`design_verdict` 固定为 `NOT_RUN`。`--through <gate>` 是 bounded execution：它只运行该
gate 的完整依赖闭包，不把拓扑排序误当成线性前缀。例如 `--through model_check` 不会启动
独立的 `golden` 分支。缺失的 handler 产生 `BLOCKED_EXECUTOR_UNAVAILABLE`，依赖节点产生
`SKIPPED_DEPENDENCY`。Generic CLI 永远拒绝 `publish`；正式发布必须使用未来独立的、带
human approval 和 expected-old-hash 的 promotion 入口。

在 snapshot handler 接入前，真实环境 fail-closed run
`20260830T054445.705882Z-recipe-run-3cdf1b5cd790` 已验证 executor 缺失时的状态机。现在
`virtuoso.snapshot` 已成为真实 read-only handler：它复用 `cad/ai` controller 拥有的
authenticated Unix-socket session，不创建 TCP listener；只允许 `get_context`、
`inspect_library`、`inspect_schematic` 和 `inspect_symbol_ports`。它对 library mapping、
schematic 和 symbol 做双读，要求 `truncated=false`、`dbIsCellViewModified(cv)=nil` 和两次
canonical hash 相同，再产生 source generation 与 preliminary normalized structure。未保存、
读间变化和超限分别为 `BLOCKED_UNSAVED_SOURCE`、`STALE_SOURCE` 和 `BLOCKED_INPUT`。

当前自动化 shell 没有 controller 委派的 session credentials，因此资格 run
`20260830T055456.459942Z-recipe-run-3cdf1b5cd790` 正确返回
`BLOCKED_ENVIRONMENT/session_credentials_unavailable`；handler 已执行且产生结构化 evidence，
manifest pair 完整性为 `PASS`、业务 `run_status=BLOCKED_WORKFLOW`。这不是 OA/input 失败，
也不是 live IPC PASS。正式 live snapshot 必须从 controller 明确委派 credentials 的会话入口
执行；token 从不写入 request、log 或 artifact。新 inspection saved-state 字段由
`AI_inspect.il` v5 提供，旧会话没有该字段时会 fail closed。

`cadence.si` 现在也已绑定为真实 structure handler。它不从 kernel 推断 ADC/PLL 路径：
profile 按 target library 选择绝对 `cds.lib`、project root 和受控 project environment，recipe
选择 library/cell/source view。Handler 在 payload gate 目录生成可审计的 `si.env`，从独立
run directory 执行：

```text
si -batch -command netlist -cdslib <absolute-approved-cds.lib>
```

PASS 不能只依赖 `si` 返回 0。完整 stdout/stderr 必须没有 error/fatal，必须出现 OSS
netlisting completion evidence，`cds.lib` 在运行期间不得变化，且 `si.env` 的五个设计
identity 字段必须保持语义不变；Cadence 标准 runtime-key 回写必须通过 namespace 白名单并
逐项记录。运行同时必须得到
至少一个非空 `ihnl/cds*/netlist`、非空 `map/current`、非空 `ihnl/globalmap`；globalmap
还必须包含 recipe 请求的 library/cell/view。逻辑产物写入 `structure-inventory.json`，命令、
hash、诊断和 source generation 写入 `si-evidence.json`。`VLOGNET-40`、`OSSHNL-514`、
missing/empty artifact、timeout、错误 workspace 和路径逃逸均有 fail-closed 测试。Controller
IPC credentials 只提供给带 `ipc` capability 的 executor，`si`/Xcelium 等非 IPC 子进程会
先移除 `CAD_AI_*`/`CAD_CODEX_*`。

为封闭 snapshot 与 netlisting 之间的 save race，structure handler 在 `si` 产物本身通过后
再执行一次 authenticated saved-state snapshot，并写入 `source-stability-evidence.json`。前后
`source_generation` 必须完全相同才会返回 `structure_authoritative=true`；post snapshot 未保存、
失败或 generation 改变分别阻断或返回 `STALE_SOURCE`。`cadence.si` executor 仅为这次 post
snapshot 保留 IPC 会话能力，启动真正的 `si` 子进程前仍明确剥离 controller credentials。

Comparator 的真实 authoritative structure 已在 controller 委派的 AI Assistant 会话中
完成同一 DAG run 的 live snapshot、官方 SI netlisting 和 post-snapshot stability PASS；
正式证据见 [M1_STRUCTURE.md](docs/M1_STRUCTURE.md)。普通 shell 缺少 credentials 时仍会在
snapshot 阻断，DAG 会把 structure 标成 `SKIPPED_DEPENDENCY`，不会绕过上游直接启动 `si`。

实现后的 `doctor` run `20260830T065335.042857Z-doctor-e3f4ebbbea41` 为 PASS，新增的
project mapping/environment 也进入资格记录。普通 shell 的真实 bounded safety run
`20260830T065344.476951Z-recipe-run-cf4754c043ba` 按预期得到 snapshot
`BLOCKED_ENVIRONMENT/session_credentials_unavailable`、structure `SKIPPED_DEPENDENCY` 和
workflow `BLOCKED_WORKFLOW`；manifest pair 为 PASS 且 `run_status=BLOCKED_WORKFLOW`。Payload
没有 `si.env`、netlist/map/globalmap 或 `si.log`，证明上游未通过时没有启动 `si`。

为避免要求普通 agent shell 持有 session token，`cad/ai` MCP 现新增需审批的
`run_aivw_recipe` 编排工具。它只接受 recipe ID、`through` gate、profile 和有限 timeout，
从 controller-delegated workspace 启动独立 AIVW 进程组，并把凭据仅委派给该进程；token 不
进入 tool result 或 run artifact。Codex/Claude 的普通 shell credential exclusion 保持不变，
generic `run-recipe` 对 publish 的禁止也保持不变。新 MCP 工具需要新建 AI Assistant session
后才会出现在 tool list；旧进程不会热替换服务器定义。

`verify-run` 是只读的复核入口。它重新计算双方 canonical core hash、request/run 一致性、
peer locator 和 manifest 中列出的 artifact SHA-256；任何一项不匹配都返回错误，不能将
历史或复制出来的 PASS 当作当前有效结果。验证结果中的 `status=PASS` 只表示 manifest pair
完整；被验证运行的业务结论必须读取 `run_status`。

## Pilot 顺序

```text
M0-E Lab6 execution qualification
  -> M0-S PLL config structural qualification
  -> M1-G generic recipe/registry/evidence foundation
  -> M1-P ADC comparator modeling pilot as the first recipe
```

详细边界见 [M0_PILOTS.md](docs/M0_PILOTS.md)。

## 当前边界

M0-E runner/verdict 和 M0-S 双 worker/AMS UNL adapter 已实现。M0-S 将源 ADE state
按 `stateLoadDir/lib/cell/ams/stateName` 复制到本次 scratch，再由 `runams` 读取副本；
这是因为 IC23.1 会迁移并回写 2014 年的 cellview state。正式源 OA/config/state 只做
前后 identity/hash 比较，不作为 runams 的可写 state 目录。

M0 已完成的资格结论只覆盖“平台会正确执行/判定已知良好测试”和“平台会正确读取
PLL mixed config 并生成可重复结构工件”，不覆盖 AI 行为建模正确性，也不代表 PLL
功能或性能签核。正式 run ID、digest、诊断 run 和 state 恢复证据见
[M0_PILOTS.md](docs/M0_PILOTS.md)。

M1-AI 当前将 `saradc/comparator_new/maestro` 作为 comparator 顶层 ADE Assembler
verification cockpit。`qualify m1-ai` 只验证只读 OA、Maestro test/spec/corner 和旧 DUT
binding 是否自洽；`run m1-ai --gate golden-nominal` 则完整复制 `saradc` 到本次 `$RUN`，
先用最小电路检查批准的 Spectre runtime/license，再在副本中 rebind 两个 testbench、
执行 Check and Save，并只运行 nominal offset。PASS
同时要求 Offset 落在 `[-5m,+5m]`、CSV verdict 为 pass、实际 Spectre 为批准的 23.1
版本、netlist 中 DUT 为 `comparator_new`，且源 OA 前后逐文件 identity 不变。这个 gate
仍不代表 PVT、PSS/Pnoise 或 AI RNM 已通过。

`run m1-ai --gate golden-pvt` 在同一 scratch 契约下运行 `_default + C1` 的 46 个设计点，
同时执行 offset 与 PSS/Pnoise-derived noise test；要求 RDB 的 46 点矩阵、92 个 test
status、两个 specification 和每个 test 的 Spectre/netlist/launcher 工件全部闭环通过。
详细证据见 [M1_AI_GOLDEN.md](docs/M1_AI_GOLDEN.md)。

`run m1-ai --gate rnm` 生成一个带 provenance 的 `comparator_new.rnm.sv` baseline，执行
`check_rnm.py` 的接口/type/style gate、固定 Xcelium 23.09 compile，以及包含正/负/零
差分、clock hold 和互补输出断言的 smoke TB。该 gate 的 PASS 只证明生成物满足当前
RNM supplement、可编译且 smoke 行为通过；supplement 明确将电平、offset、epsilon、
延迟和 metastability 行为标记为假设，必须经过 Spectre-to-RNM correlation 后才能进入
更高等级结论。

新的 split-storage 正式 RNM run
`20260830T053423.615444Z-m1-ai-rnm-91c15ee49107` 已使用 Xcelium
`23.09-s002` 完成 checker、compile 和 smoke，model SHA-256 为
`651a51dadaf2d5b57e67d236faae8d1acdaa8d73a6d33113807a813533ab3000`。Control 只保存
request/report/manifest；模型、checker、Xcelium log/worklib 和 payload manifest 位于
`$PROJ_AMS_DB_DIR/saradc.comparator_new.rnm/runs/<run_id>`。生成逻辑现位于注册的
`latched_dynamic_comparator` model-class plugin，端口拼写来自 spec semantic roles；它仍是
deterministic baseline，provenance 明确记录 `ai_generation=not performed`，不能当作真实 AI
首次生成成功率。

SystemVerilog disposable text-view qualification 已在 IC23.1 实机通过。首轮诊断 run
`20260830T053253.616689Z-text-view-probe-ace85fbafe5e` 暴露了 importer 默认 linked primary
以及把 `systemVerilogText` 错当成 OA database viewType 的问题，业务状态为
`FAIL_TEXT_VIEW_READBACK`。修正后 run
`20260830T053358.284577Z-text-view-probe-ace85fbafe5e` 对隔离 scratch library 完成
create/update/readback/restore 并 PASS：Library Manager 类型为 `systemVerilogText`，shadow
OA 读回类型为 `netlist`，`CDS5X_NOLINK=1` 保证 `verilog.sv` 是 self-contained regular
file，restore hash 与 create 完全相同。该探针只证明 importer/readback/restore 组合可用，
不等于正式 authenticated IPC publisher 已获准写项目 library。

`run m1-ai --gate correlation` 已提供离线 Spectre-to-RNM evidence gate。它要求两个
绝对路径 JSON，按相同 case ID、corner 和指标比较 Spectre 与 RNM，并把源 PASS manifest
及 SHA-256 写入 `$CWD/.aivw/runs/<run_id>`。当前已有的 Spectre golden 与 RNM smoke
工件原本不是成对测量；现已新增 `rnm-evidence` 和 `spectre-evidence` 两个受控 exporter。
正式 RNM evidence run `20260830T014117.937086Z-m1-ai-rnm-evidence-e548211c9b8c` 与正式
Spectre evidence run `20260830T014333.527155Z-m1-ai-spectre-evidence-94b206e6c0e6` 均为
PASS，并使用相同三向量、corner、rising edge 和 1 ns sample delay。正式 correlation run
`20260830T014352.089313Z-m1-ai-correlation-f596fdfa3cb1` 为 `FAIL_CORRELATION`：正/负差分
通过，零差分的真实 Spectre negative rail 与 RNM midpoint tie placeholder 不一致。该结果
是模型缺口证据，不是环境阻断；RNM supplement 未自动晋级。独立 repeat runs
`20260830T014803.449197Z-m1-ai-rnm-evidence-e548211c9b8c`、
`20260830T014803.481922Z-m1-ai-spectre-evidence-94b206e6c0e6` 和
`20260830T014822.499426Z-m1-ai-correlation-a9fe73efcede` 得到逐 case 完全相同的结果。
详细 JSON 契约和人工决策点见
[M1_AI_CORRELATION.md](docs/M1_AI_CORRELATION.md)。

当前 M1-AI Spectre golden 证据见 [M1_AI_GOLDEN.md](docs/M1_AI_GOLDEN.md)，RNM
generation/check/TB 证据见 [M1_AI_RNM.md](docs/M1_AI_RNM.md)。

Recipe revision 2 的 M2 live run
`20260830T124023.329864Z-recipe-run-17ead7853bd8` 已完成 snapshot、structure、generate 和
connectivity 四个 gate 的正式 PASS。Connectivity 使用官方 SI hierarchy/map/globalmap/
inherited-connection 证据，结果为 `connectivity_match`、0 findings；双 manifest 验证 2 个
control 与 61 个 payload 工件，publication 为 `NOT_AUTHORIZED`。当前结构候选是
Cadence-sourced qualification reference，不代表 AI 独立合成 topology。完整边界见
[M2_CONNECTIVITY.md](docs/M2_CONNECTIVITY.md)。

Recipe revision 3 已实现 M3 的第一层 machine-verifiable L1 gate。Recipe 明确声明三条
comparator case、两条 precondition check、每条 case 的 expected metrics/tolerance 和 4 个
assertion ID；生成的 TB 用 `AIVW_L1_EVENT v1 JSONL` 输出实测结果。通用
`xcelium.rnm_check` 要求 exact case/check/metric/assertion set、有限数值、100% case coverage、
唯一 Xcelium runtime identity 和无 error/fatal 日志，并写出绑定 test-plan/model/TB hash 的
`l1-evidence.json`。L1 plan 由 deterministic kernel 从 recipe 重建；generation 输出必须与其
canonical hash 等价，否则在 RNM checker/Xcelium 启动前 `BLOCKED_INPUT`。隔离实机资格已在
Xcelium `23.09-s002` 完成 checker、compile、
elaboration/simulation，3/3 cases、2/2 checks、12/12 case assertions、0 findings。正式
authenticated full-DAG `through=model_check` 已于 `20260831T010643.635950Z-recipe-run-5660c8d692b2`
通过；`verify-run` 返回 `status=PASS`、`run_status=PASS`、`artifacts_verified=true`，包括 2 个
control 与 67 个 payload 工件，publication 为 `NOT_AUTHORIZED`。完整边界见
[M3_XCELIUM_L1.md](docs/M3_XCELIUM_L1.md)。

M3-L1 的通用契约还支持可选的 case `corner`/`vector_id`、recipe-owned temporal assertion、
coverage（UCIS/IMC backend、最低分数、required points）和 waveform（SHM/VCD/FSDB、retention）
字段。evaluator 对 temporal/coverage/waveform 事件执行 exact-set、状态、分数、路径和 payload
文件校验；当前 comparator plugin 对尚未注册的 temporal/coverage/waveform binding fail closed，
因此这些字段不会被误报为已完成。真实 UCIS/IMC 生成与合并、波形 dump/retention adapter、PVT
矩阵和第二 model class 仍需独立工具资格。

后续仍需在 split-storage 和 recipe 契约下增加：

- zero/near-zero comparator policy、corner/mismatch 扩展和 correlation PASS 后的人工参数晋级流程；
- 其余 legacy M0/M1 EDA runner 的 payload 迁移；当前 split manifest 已提供独立的
  `artifact_locators` 出口，可安全引用 SHM/FSDB/UCIS/IMC 等文件或目录，目录只做类型/存在性
  校验，不递归哈希整个 EDA 数据库；
- 通用 metric evaluator、真实 AI generation/revision backend 和第二/第三 model class；
- config-binding snapshot、分页/大结构 transport、post-save invalidation，以及带审批/并发
  保护的正式 text-view publisher；Verilog-A disposable publication 尚需独立资格探针；
- 扩展 M3 L1：已加入 corner/vector、temporal、coverage/waveform 的通用 recipe/evidence 契约，
  仍需 model-class SVA/coverage/waveform adapter、真实 UCIS/IMC 资格、更广 PVT 矩阵和 revision
  统计；
- PyQt5 GUI 和 Virtuoso `aivw*` launcher。

总体架构和发布边界见 [ARCHITECTURE.md](docs/ARCHITECTURE.md)。
