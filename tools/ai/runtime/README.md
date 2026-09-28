# SiCo AI runtime

Silicon Copilot 需要两个上游运行时（不随 Git 仓库分发）：

| 组件 | 版本 | 目标 |
| --- | --- | --- |
| OpenAI Codex CLI | 0.156.1 | x86_64-unknown-linux-musl |
| ripgrep | 15.2.0 | x86_64-unknown-linux-musl |

它们以 GitHub Release 附件（`v0.0.1-202610`）提供。在本目录执行：

```sh
./fetch-ai-runtime.sh
```

脚本下载并校验归档（`SHA256SUMS`），按下列布局展开：

```
tools/ai/runtime/codex/x86_64-unknown-linux-musl/bin/codex
tools/ai/runtime/codex/x86_64-unknown-linux-musl/bin/codex-code-mode-host
tools/ai/runtime/ripgrep/x86_64-unknown-linux-musl/bin/rg
```

每个归档内含上游 `MANIFEST.txt`（来源 URL、版本、SHA-256）与许可证文本：
Codex CLI 为 Apache-2.0，ripgrep 为 MIT / Unlicense / PCRE2 / musl。
