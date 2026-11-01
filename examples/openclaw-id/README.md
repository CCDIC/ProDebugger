# 身份证进入简历：一个公开构造案例

唯一案例是 `ca30-d27b918e`：15 个导入分析事件、2 次读取；离线展示保存 90 条事实（56 条结构事实、34 条历史语义事实）及 12 次实际执行并保存的 ProDebugger 审计模型调用。身份证示例值 `DEMO-ID-0001` 是虚构占位符。

轨迹是基于 OpenClaw / SigNoz 导出构造的副本，`constructed=true`、`executed=false`。继承的时间、trace/span 标识、token 和字节计数属于来源元数据，不证明构造轨迹曾执行。导入事件编号不是原生执行步骤；页面 PARSE → MAP → ROUTE → TRACE → SUBMIT 节点是 ProDebugger 审计流程。

## 文件与复现

- `import.json`：**用户在工作台 UI 中只需选择此文件**，同一封装包含轨迹、文件访问政策及字段政策。
- `trajectory.json`：规范的公开 source 对象，包含 8 份经过公开脱敏的原始文档及严格重放产生的事件，供程序使用。
- `policy.json` / `risk-policy.json`：与工具读取路径一致的文件访问及字段政策。
- 仓库 `web/static/openclaw/assets/case-data.json`：**同一案例**的历史审计展示快照，含已保存请求、响应、SQL、工具结果与事实引用。
- `publication.json`：公开文件的实际 SHA-256、原始文档/消息/文件标识映射和历史来源说明。

从仓库根目录运行 `python examples/openclaw-id/verify.py`。它严格重放原始文档，核验公开文件哈希、事件/事实/原始文档引用、引用区间、政策路径、递归 JSON 字符串和高置信度密钥模式。只打印统计，不输出疑似密钥值；不调用模型、运行原 Agent、读取演示材料或连接 SigNoz。

在工作台 UI 导入 `import.json`，即可同时载入同一轨迹及两份政策。`run.py --seed-example` 继续读取 `trajectory.json`、`policy.json`、`risk-policy.json` 三个规范文件；两种入口不是两条轨迹。

导入会产生一次新的离线结构编译。它不重现历史模型审计，也不保证得到已保存的 90 条事实。展示快照中的模型记录确实曾保存；公开版本经过一致脱敏，**不是私有原件的逐字/逐字节副本**，也不是重新生成的模型结果。

## 来源与公开脱敏

输入来源逻辑标识：`content-access-review-20261030/import-package/01_身份证进入简历_构造轨迹.json`；快照来源逻辑标识：`ProDebugger-offline-story-20261031/assets/case-data.json`。这些是来源归档名称，不是可访问的本机文件链接。没有引入第二条轨迹或 ground truth。

公开版本递归处理嵌套 JSON 字符串，以虚构 `C:\demo` 路径、`DEMO-HOST`、`demo-user` 和公开工作区标识替换机器位置与宿主元数据。trace/span/tool call 标识保留必要关联；因内容变化产生的消息与文件标识、文档哈希、引用区间及模型记录中的相应文本同步映射。语义/FID 和审计调用编号作为历史快照标识保留，不能据其名称推导公开文本长度。过程未调用任何模型。

公开原始文档 SHA-256 对实际发布文档 UTF-8 重新计算，严格导入重放校验没有放宽。`publication.json` 哈希对应当前公开文件字节；快照 `published_source_sha256` 对应公开 source 的排序紧凑 JSON。`source_provenance_sha256`、`historical_compilation_receipt`、`baseline_document_sha256` 和来源清单摘要只表示**脱敏前历史来源证明**，不冒充当前公开内容的哈希。未附带的历史归档条目标记为 `historical_source_artifact_not_distributed`。
