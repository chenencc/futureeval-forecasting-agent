# 工具与渠道分层

Ultra 继续使用已有工具名称与 JSON 参数，定义集中在 `registry.py`。工具调用由 `runtime/retrieval.py` 执行，网络动作先经过 `runtime/budget.py` 持久化预约，再调用 provider。任务锁、历史限制、来源目录和失败账本继续生效。

| 层 | 当前实现 |
|---|---|
| tools | 工具参数与说明；不包含凭证或模型可修改的额度 |
| providers | Tavily basic/Extract、免费 HTTP 下载、Yahoo/ALFRED、Ultra |
| readers | HTML、PDF、CSV、JSON；读取已下载字节，不联网 |
| evidence | Document 内容与 metadata、快照存储 |
| runtime | 调度、账本、预算、缓存、恢复和证据准入 |

下载器保存真实响应；reader 返回 `page_content + metadata`。PDF metadata 包含页码，CSV 包含数据行，HTML 包含来源；旧 `content` 字段保留，精确引用检查仍针对已保存正文。返回还包括截断标记，原始字节在下载大小限制内完整保留。解析器可对已保存原始响应重新运行，不消耗搜索或抓取额度。

## LangChain 借鉴范围

- [Tools](https://docs.langchain.com/oss/python/langchain/tools)：明确参数、结构化返回和由程序注入运行状态。
- [WebBaseLoader](https://github.com/langchain-ai/langchain-community/blob/main/libs/community/langchain_community/document_loaders/web_base.py)：网页加载与文档内容/metadata 的组织。
- [PDF loaders](https://github.com/langchain-ai/langchain-community/blob/main/libs/community/langchain_community/document_loaders/pdf.py)：按页解析、保留文档位置。

本次只借鉴接口设计，没有复制源码或新增 LangChain 依赖。免费 HTML、PDF、CSV、JSON 解析已实现；服务型搜索的费用仍由服务商决定。LangChain 不提供免费联网能力或自动保证历史可用性。

下一批可评估的本地能力：BeautifulSoup 正文/表格提取、pdfplumber 表格读取、Playwright 动态页面渲染、RSS/sitemap 链接发现。它们尚未接入，不宣称已可用；接入前需通过同一个下载/预算/历史准入入口，关闭会增加网络次数的自动重试。当前 PDF 不做 OCR；CSV 保留字符串值，单位与日期由领域适配器验证。
