"""Single authority: OA database must only be read/written through platform APIs."""

INSTRUCTIONS = (
    "所有 OpenAccess 数据都必须通过平台工具（CDNS-IC / EMPY-AE / SNPS-CC）的 API 读写："
    "OA 的几何、电气、连接与参数一律经已注册的 MCP 工具获取（bind_pdk_device 返回的 "
    "geometry 行、binding_refs 由 preview/prepare 自动解析 bindings+geometry、"
    "get_pdk_device(sections=[\"geometry\"])、inspect_master_evidence（已保存 master 的 "
    "symbol body 证据）、inspect_schematic/inspect_cdf 等），"
    "写入只走受控入口；不得用 shell、文件读写或落盘镜像/预览产物访问 OA 数据库。"
    "shell 的 grep/find 等文本检索只用于日志、文档与输出文本。"
    "原生文件/Shell 工具只用于有界的项目证据与文档工作；不要用它们修改 OA 数据库。"
)
