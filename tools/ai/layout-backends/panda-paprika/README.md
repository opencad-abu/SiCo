# PANDA Paprika Layout Backend

这是一个 **SiCo 外部 layout backend**，只封装 PANDA `pr_kernel` 的
placement/routing 进程，不把 PANDA planner、sizing、Spectre 或
`virtuoso-bridge-lite` 引入 SiCo。

## 状态

- `backend.describe` / `backend.validate` / 异步 job / status / collect /
  cancel 已实现。
- TN65 反相器 placement POC 已跑通：真实 `pr_kernel/placement` 接收
  `tsmcN65` PCell GDS、TN65 tech JSON 和最小网表，产出
  `INV.gp.gds` 与 `db_size_information.json`。JSON-RPC server 也已验证
  `describe -> validate -> job.start -> status -> collect`。
- routing 仍受 Paprika post-process 的 GR/power-stripe 单位问题影响：
  `INV.pp.gds` 可生成，但 detailed routing 在 VSS/GR 访问点上不收敛。
- 因此当前 manifest **只声明 `place`**；`route` / `place_and_route` 代码
  保留但不再对外宣告。等 routing 在 TN65 上完成 DRC 验收后再加回。
- 运行时必须使用 **LibTorch 1.12.1**（`torch==1.12.1+cpu`）。1.13.x
  会在 `torch::tensor`/`set_autograd_meta` 段错误；2.0.x 缺少
  `c10::Warning::warn` 符号。1.12.1 不需要 `LD_PRELOAD` shim。

## TN65 输入要点

- `techfile` 必须是 Paprika `lef.json` 风格 JSON，至少包含
  `layer_map`、`layer_datatype`、`min_w_rules`、`sp_rules`、
  `textlayers`、`pmos`、`nmos`、`cap`、`res`。`layer_map` 是
  layer name -> GDS layer number；为空会让 `parse_pcell` 卡死。
- `pcell_bundle` 目录中每个器件一个 GDS，文件名为
  `<top>__<instance>.gds`（双下划线），GDS top structure 使用同一名字。
- TN65 PCell 用 `"layout"` 视图和
  `dbCreateParamInstByMasterName(..., list(list("l" "string" "60n") ...))`
  生成；临时 OA 库必须 `techBindTechFile` 到 `tsmcN65`，否则 GDS 只有
  text label、没有 polygon。
- 最小 placement smoke 可令 `max_iterations=0`、`maxIters=1`、
  `useLSEWL=true`、`useOverlap=true`；`max_iterations=1` 在当前最小
  两器件用例会在 SA 首次 move 段错误。
- PANDA 的 placement GDS 使用 `unit=1e-12` / `precision=1e-9`。
  Virtuoso XStream In 对该单位组合会 SIGFPE；导入前必须按 SiCo
  `process_view.units` 归一化。TN65 `maskLayout` 的权威值是 PDK techfile
  中的 `("micron" 1000)`，即 `stream_unit_m=1e-6`、
  `database_unit=1e-9`、`dbu_per_um=1000`；不要在 adapter 里写死单位。
- `inputs.process_view` 现在是 `place` 的必需输入；adapter 从它读取
  `dbu_per_um` 并写入 `geometry_manifest.json`。

## 输入

`inputs` 是 SiCo 编译/导出的普通文件，不由本插件生成：

```text
netlist         topology_clean.sp
techfile        lef.json
stdcell         stdcell.json
pcell_bundle    flat PCell GDS directory
paramfile       allconfig.json
placement_result  route 输入（place 后的结果目录）
sym / placepr / multi_sym / symmetric_net  可选 Paprika sidecar
```

`source` 必须为 `{"kind":"netlist"}`；SiCo 只传 spec、工艺视图和路径，
Paprika 不读取 OA、Virtuoso 或 PDK 包内部。

## 配置

复制 `config.example.json` 为同目录 `config.json`，设置：

```json
{
  "panda_root": "/path/to/PANDA",
  "pr_lib_dir": "/path/to/PANDA/pr_kernel/libso",
  "shell": "/bin/bash"
}
```

`panda_root` 必须包含：

```text
run_placement.sh
run_route.sh
pr_kernel/placement
pr_kernel/routing
```

`backend.example.json` 中的 `transport.command` 和 `cwd` 也需要按实际安装
位置替换。manifest 不保存许可证或私有 token。

## 输出

- `place`：`placement_layout` role，来自 `placement_result/*.gds`
- `route` / `place_and_route`：`layout` role，来自 `route_result/*.gds`
- `placement_result/geometry_manifest.json`：`sico.layout.geometry.v1`
  几何 bundle，含 `bbox_um` 和 `terminal_map` 引用
- `terminal_map`：如果请求提供 `inputs.terminal_map`，复制为
  `output/terminal_map.json` 并以 `terminal_map` role 返回
- `run_place.log` / `run_route.log`：只作为证据

实际 GDS 的 DRC/LVS 仍由 SiCo 在导入 OA 后执行，不由 Paprika 自报通过。

## 许可

PANDA 的 `pr_kernel` 使用 PolyForm Noncommercial 1.0.0；商业使用需要单独
书面许可。这个插件不改变许可边界，也不打包 `pr_kernel` 二进制。
