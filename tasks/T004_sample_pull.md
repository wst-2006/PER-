# T004 小样本导出

**目的**：让设计方手里有一份几 MB 的真实数据，用来核对字段含义、边界情况，并在自己的环境里跑通核心代码。

**前置**：T002 完成。

## 步骤

```powershell
python scripts/sample_pull.py
```

约 700 次调用，预计 10 分钟左右。脚本会跳过没有权限的接口并记录下来。

## 交付

1. **提交进仓库**：`results/T004/sample_manifest.json`、`results/T004/run_meta.json`、`results/T004/REPORT.md`。提交信息：`T004: 样本导出清单`。
2. **不提交**：`samples/` 目录和 `samples/perq_sample.zip`。它们已被 `.gitignore` 排除。提交前用 `git status` 确认没有 parquet 或 zip 文件被加入。
3. 告诉用户压缩包的完整路径，由用户把它直接发给设计方。

## 验收标准

- `sample_manifest.json` 的 `status` 为 `completed` 或 `completed_with_failures`。
- 压缩包大小在 50 MB 以内。超过则不要发送，在报告里说明。
