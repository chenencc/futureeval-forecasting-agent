# ForecastAgent

独立的 Ultra 研究运行器。Codex 只是操作和维护入口；包内没有 Codex SDK、MCP、宿主技能目录或登录依赖。

## 工具分层

`tools/`：工具接口；`providers/`：渠道与下载；`readers/`：本地解析；`evidence/`：文档与快照；`runtime/`：调度和预算。旧平铺模块仅兼容转发。详见 [工具说明](tools/README.md)。

## 目录

- `agent.py` / `__main__.py`：统一运行、检查与离线重放入口。
- `retrieval_agent.py`：Ultra 工具循环、任务锁、搜索预算、证据校验和恢复。
- `skill_loader.py` / `skills/`：按需研究方法；每个任务保存 skill 内容与 SHA256，恢复使用原版本。
- `tavily_research.py` / `tavily_extract.py`：搜索与补救读取工具。
- `retrieval_sources.py` / `ultra_research_agent.py`：财经数据、HTML、PDF、CSV、JSON 阅读和模型调用。
- `monitor_tournament.py`：比赛监听与研究调度。
- `retrieval_trial.py` / `fixtures/`：固定历史实验入口与题目。
- `tests/`：本地、无搜索和模型费用的回归验证。
- 其他迁入模块：旧预测模板、快照、市场匹配及实验工具；旧预测模板仍有发布能力，当前研究入口不调用它。

研究状态：analysis → research → audit → report → complete / incomplete。阶段是账本中的运行记录；具体规则仍由工具执行。

## 从仓库根目录运行

```sh
python -m pip install -r ForecastAgent/requirements-retrieval.txt
python -m ForecastAgent skills
python -m ForecastAgent run --input question.json --task-dir snapshots/retrieval/question-123
python -m ForecastAgent inspect --task-dir snapshots/retrieval/question-123
python -m ForecastAgent replay --task-dir snapshots/retrieval/question-123
python -m ForecastAgent.monitor_tournament
python -m unittest discover -s ForecastAgent/tests
```

研究运行需要环境变量 `OPENROUTER_API_KEY`、`TAVILY_API_KEY`；监听还需要原有 Metaculus 凭证。inspect/replay 只读，不需要凭证。模型固定为 `nvidia/nemotron-3-ultra-550b-a55b:free`。

## 硬限制和迁移

- 每题最多三次 Tavily basic 网络尝试，失败计数，恢复不清零。
- 免费抓取最多八次；basic Extract 最多一个批次、五个页面。
- skill 只能提供研究方法，不能覆盖预算、时间过滤或提交权限。
- 不预测、不提交、不交易是当前研究入口的约束。
- 原 `snapshots/` 数据路径保留。Actions 下载、恢复与上传继续使用同一个账本位置。
- 已完成任务返回缓存；代码或 skill 更新不会自动重新采集或开新预算。
- 仓库根目录旧模块和 `scripts/` 仅保留兼容转发；所有新逻辑、测试和 skills 在本目录维护。
- Codex、命令行、Actions 调用同一套独立模块，不要求 Codex 在运行时在线。

skills 是本项目的简单登记与加载格式，不依赖宿主自动发现；Ultra 通过 `load_research_skill` 工具选择。当前 audit 仍是 Ultra 自审，不是独立事实核查。历史数据限制见 [检索说明](../docs/RETRIEVAL.md)。

架构借鉴 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) 的分工、状态编排与恢复思路；未复制其源码，也未引入 LangGraph 或股票交易流程。
