# Changelog

## Unreleased

- `install.py --uninstall`：默认打印清理清单；`--apply` 移除工作流叠加层，不删除产品源码。`--purge-state` / `--purge-runtime` 分别删除工作流档案和 Git common-dir runtime。
- `install.py --export-product <dir>`：导出不含 `.codex-workflow` 引擎与状态的产品树。

## 4.0.0

Codex Workflow V4 正式产品源码包。安装：`python install.py <project-root> --project-name <name>`。

- 新任务默认 `task-record-v4`：可观察 focus slice、decision log、product checkpoint。
- 交付安全外环沿用协议代次 3：lane、canonical delivery、独立 Reviewer、strict-ff closeout。
- 已有 V3 record 按原算法收尾。
- 只读 GitHub provider receipt 可附加，不能替代 Independent Reviewer 或 ff/CI 证明。
- 已验证范围：macOS / CPython 3.9（`python3 -B verify_package.py`）。Windows / 3.12 / 3.13 未验证。

能力与测试依据见 [功能.md](功能.md)。形成过程见 [docs/history/](docs/history/)。
