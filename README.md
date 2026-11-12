# PER：个人 A 股量化研究系统

全市场选股、组合与风控、回测、每日指令单、纸面验证。信号在收盘后生成，由人次日手动执行。

当前阶段：**M1 数据层已实现，等待试跑（T006）与全量回填（T007）**。系统尚无任何可用策略结论。

- 总体设计（正确性）：[`docs/DESIGN.md`](docs/DESIGN.md)
- 研究蓝图（有效性）：[`docs/ALPHA_BLUEPRINT.md`](docs/ALPHA_BLUEPRINT.md)
- 文献调研笔记：[`docs/RESEARCH_NOTES.md`](docs/RESEARCH_NOTES.md)
- 执行代理守则：[`AGENTS.md`](AGENTS.md)
- 当前任务：[`tasks/`](tasks/)
- 任务结果：`results/`

## 协作方式

设计方编写架构、核心代码、测试和任务单；执行代理在本地机器上按任务单运行并把结果摘要提交到本仓库；设计方根据仓库内容审查并给出下一步。

## 重要说明

- 本仓库不含任何行情数据和密钥。数据来自 Tushare Pro，仅存放在本地。
- 本项目用于研究与纸面验证，不构成投资建议，不自动交易。
