# 功能验证记录

追加：2026-09-30 完成 [Polymarket 真实接口专项验证](../docs/POLYMARKET_VALIDATION.md)，十次题目搜索及两个真实子合约正例，完整套件65项通过。以下表格是此前通用功能验证的记录。

验证日期：2026-09-30。最终代码：4f3371b。

[成功运行与完整快照](https://github.com/chenencc/futureeval-forecasting-agent/actions/runs/36714712263)

| 功能 | 验证方式与结果 |
|---|---|
| HTML / PDF / JSON | 真实公开页面下载、解析、原始字节与文档 metadata，通过 |
| CSV / ALFRED | 真实 DGS30 历史版本 2026-08-19，266 条非缺失观测，最新观测 2026-08-18，通过 |
| Yahoo | 真实 ^TYX 历史数据 268 行，最新为 2026-08-19，排除截止日及之后日期，通过 |
| Tavily basic | 累计一次真实调用，10 条结果；官方页面免费读取通过 |
| Ultra / skill | 累计一次逻辑工具调用，正确调用并加载 evidence-review；复测使用保存结果 |
| Basic Extract | 请求参数、资格、部分失败、恢复与额度通过模拟测试；本次免费读取成功，无资格实调 |
| 预算 / 锁 / 缓存 / 恢复 | 三次硬上限、失败计数、并发阻止、任务输入固定、旧入口共用账本，通过 |
| 证据 / 时间 / 审计 / 快照 | 精确引用、错误实体、未来日期、原始响应哈希、历史导入、只读回放，通过 |
| skills | 按需加载、未知名称拒绝、内容版本固定，通过；研究方法有效性尚需赛题实验 |
| 监听 / Polymarket 匹配 | 分页、状态恢复、重复任务防止、子合约及年份匹配通过模拟测试；本次未新增真实平台采集 |

本地和 Linux Actions：57 项测试通过。真实检查先发现并修复两个问题：application/csv 类型未支持、历史列名 YYYYMMDD 未识别。新增了对应回归测试；错误版本仍拒绝。

输出：report.json、offline-tests.txt、原始页面/财经响应，以及 search-task/bundle.json。下载报告位于 snapshots/retrieval-live/36714712263/。

验证总消耗：1 次 Tavily basic、0 次 Extract、1 次逻辑 Ultra 调用（其传输层仍保留原有重试规则）。未提交预测或交易。成功验证不证明任意网页、扫描 PDF、历史版本或研究结论都可正确处理。
