'use strict';
// Exact UI copy only. Trace content is never sent through a translator.
(() => {
  const entries = `
构造轨迹 · 未执行 / Constructed · Not executed|Constructed · Not executed
工具返回包含禁止获取的信息|Tool return contains information prohibited from acquisition
允许获取字段内容|Allowed to acquire field content
路径权限、内容获取、模型输入、报告输出和对外发送分别检查。|File-path permission, content acquisition, model input, report output and external transfer are checked separately.
路径权限|Path permission
内容获取|Content acquisition
模型输入|Model-input exposure
报告输出|Output disclosure
删除轨迹|Delete traces
删除所选轨迹|Delete selected traces
确认删除|Delete
回收站|Trash
恢复|Restore
原始轨迹已保护，不能删除|Original trace is protected
分析正在运行，完成或取消后再删除|Review is running; finish or cancel it before deleting
所选轨迹受保护或正在分析，不能删除。|Selected traces are protected or have an active review.
轨迹移入回收站，原始文件与分析结果保留，可以恢复。|Move these traces to Trash. Source files and analysis results are retained and can be restored.
正在读取回收站…|Loading Trash…
选择轨迹恢复到工作区。|Restore a trace to the workspace.
回收站为空。|Trash is empty.
轨迹已恢复。|Trace restored.
查看全部历史轨迹|View all history
返回本次工作区|Back to this workspace
全部历史轨迹|All historical traces
比较轨迹一和轨迹二|Compare Trace 1 and Trace 2
ProDebugger · SigNoz 轨迹风险审查|ProDebugger · Trace Risk Review
ProDebugger 轨迹工作台|ProDebugger trace workspace
轨迹风险审查|Trace Risk Review
工作台操作|Workspace actions
跳到轨迹工作区|Skip to trace workspace
导入轨迹|Import traces
随机抽样|Random sample
受控采集|Capture
诊断队列|Jobs
更多工作台操作|More workspace actions
更多|More
诊断记录与队列|Diagnosis history and queue
使用说明|Help
打开使用说明|Open help
连接中|Connecting
本地服务已连接|Connected
服务已连接|Connected
服务连接失败|Connection failed
已连接|Connected
在线|Online
离线|Offline
轨迹库|Trace library
刷新轨迹与诊断状态|Refresh traces and jobs
搜索名称或 ID|Search name or ID
搜索轨迹|Search traces
来源|Source
全部来源|All sources
开发验收案例|Development cases
独立保留测试集|Holdout cases
完整原生会话|Native sessions
定位验证样本|Evaluation cases
导入的轨迹|Imported traces
受控工具采集|Controlled captures
历史 OpenClaw|Historical OpenClaw
基础安全|Basic checks
全部状态|All statuses
基础策略违规|Policy violation
基础检查通过|Basic checks passed
证据不足|Insufficient evidence
载入开发验收案例|Load development cases
载入独立保留测试集|Load holdout cases
全选筛选结果|Select filtered traces
点击查看 · 勾选对比|Open or select to compare
可用轨迹|Available traces
正在读取轨迹…|Loading traces…
未选择轨迹|No traces selected
可选多条|Multiple selection
放入工作区对比|Compare selected
完整风险审查所选轨迹|Review selected traces
任务诊断所选轨迹|Diagnose selected tasks
轨迹风险工作区|Trace workspace
选择或导入轨迹，查看发生了什么、是否有风险以及证据。|Select or import a trace.
完整风险审查|Run risk review
任务诊断|Task diagnosis
展开或收起详情|Toggle details
轨迹展示方式|Trace view
完整会话|Session
状态表|State table
覆盖核对|Coverage
工具调用|Tool calls
实体关系|Entities
实体关系图缩放|Entity graph zoom
实体关系视图支持缩放|Zoom entity graph
缩小实体关系图|Zoom out
恢复实体关系图原始比例|Reset zoom
放大实体关系图|Zoom in
导出|Export
清空|Clear
节点颜色说明|Node status legend
规则 / 任务错误|Rule / task error
有证据的风险|Supported risk
输入警告|Input warning
疑似|Suspected
适用检查通过|Applicable checks passed
未评估|Not assessed
风险不等于任务失败|Risk and task failure are separate
轨迹画布|Trace canvas
每页|Per page
每页轨迹数量|Traces per page
上一页|Previous page
下一页|Next page
问题与调查线索|Findings
选择轨迹后展示|Select a trace
点击问题可定位节点|Select a finding to locate its node
问题分类|Finding categories
节点与诊断详情|Node and review details
依据与上下文|Evidence and context
轨迹详情|Trace details
选择轨迹或点击节点，查看它的完整信息。|Select a trace or node.
关闭详情，返回执行过程|Close details
返回执行过程|Return to timeline
详情内容|Detail tabs
事件 / 状态|Event / state
语义解析|Semantics
风险审查|Risk review
验收对照|Comparison
检查概况|Overview
事实原文|Facts and source
对照验收|Comparison
切换工作区|Switch panel
执行过程|Timeline
详情与诊断|Details
轨迹一|Trace 1
轨迹二|Trace 2
轨迹三|Trace 3
三条轨迹|Three traces
轨迹|Trace
轨迹列表|Traces
并排查看|Compare traces
并排查看这三条|Compare all three
查看全部轨迹|All traces
查看全部轨迹与旧功能|All traces and tools
查看当前轨迹接收数据|Current source data
任务|Task
用户要求|User request
Agent 公开消息|Agent message
工具返回|Tool result
公开消息|Message
工具|Tool
系统|System
用户|User
外部内容|External content
调用|Call
调用 ID|Call ID
调用 / Span 标识|Call / Span ID
导入步骤|Import step
导入调用步骤|Call import step
导入返回步骤|Result import step
原始记录位置|Source location
原始记录定位|Source location
原文定位|Source location
原文|Source text
来源未标注|Source not specified
来源待核对|Source unverified
未标注来源|Source not specified
未提供|Not provided
未指定|Not specified
未记录|Not recorded
尚未记录|Not recorded
模型判断|Model assessment
模型诊断|Model diagnosis
规则问题|Rule findings
安全风险|Safety risks
运行异常|Runtime errors
工具运行异常|Tool runtime errors
全部|All
全部问题|All findings
全部发现|All findings
规则 / 策略|Rule / policy
确定性|Confidence
状态|Status
结论|Conclusion
说明|Description
规则|Rule
策略|Policy
规则编号|Rule ID
检查|Check
检查来源|Check source
模型|Model
自动检查|Automatic checks
模型检查|Model checks
模型调查|Model investigation
自动风险规则|Automatic risk rules
风险发现|Risk finding
风险类型|Risk type
有证据支持|Supported by evidence
有证据支持的风险|Supported risk
风险线索 · 尚未证实|Unconfirmed risk lead
输入证据警告|Input warning
本项调查通过|Investigation passed
本项发现风险|Risk found
当前范围通过|Passed within scope
不适用|Not applicable
未支持|Unsupported
不支持|Unsupported
暂不支持|Unsupported
未配置风险策略|No risk policy
排队中|Queued
排队|Queued
等待运行|Queued
运行中|Running
正在分析|Analyzing
审查中|Reviewing
流程完成|Completed
流程已完成|Completed
审查流程已完成|Review completed
部分完成|Partially completed
审查失败|Review failed
执行失败|Failed
运行失败|Failed
已取消|Cancelled
取消|Cancel
超时|Timed out
已超时|Timed out
已中断|Interrupted
预算耗尽|Budget exhausted
违反当前策略|Policy violation
违规尝试|Violation attempt
有违规尝试|Violation attempt observed
发现违规尝试|Violation attempt found
当前检查未发现风险|No risks found by current checks
未授权操作|Unauthorized action
越权访问|Unauthorized access
信息外传|Data exfiltration
隐私泄漏|Privacy leak
提示注入|Prompt injection
执行了不可信指令|Untrusted instruction followed
受保护内容修改|Protected content modified
未经批准发送|Unapproved send
超出任务范围访问|Out-of-scope access
受保护配置修改|Protected configuration modified
超出任务授权范围读取|Out-of-scope read
未经批准产生外部操作|Unapproved external action
受限信息外传|Restricted data transfer
受限信息出现在可见上下文|Restricted data in visible context
不可信内容中的指令|Instruction in untrusted content
尚未诊断|Not diagnosed
尚未完整诊断|Not diagnosed
已选出根因候选|Root-cause candidate selected
选出了错误候选|Error candidate selected
不确定 / 未能定位根因|Uncertain / root cause not located
不确定，未能定位根因|Uncertain / root cause not located
未发现任务失败|No task failure found
当前证据未发现任务失败|No task failure in current evidence
违反|Violation
通过|Passed
未知|Unknown
仅怀疑|Suspected
候选|Candidate
本策略通过|Current policy passed
错误状态|Error state
客观记录|Observed record
已记录|Recorded
未声明工具状态|Tool status not declared
当前任务契约满足|Task contract satisfied
当前任务契约未满足|Task contract not satisfied
当前安全策略通过|Current safety policy passed
结构 / 未评估|Structural / not assessed
规则或模型判断有错|Rule or model error
存在安全风险|Safety risk
！规则 / 模型错误|! Rule / model error
！有证据的规则 / 模型问题|! Supported rule / model finding
▲ 安全风险|▲ Safety risk
▲ 有证据的风险|▲ Supported risk
△ 输入警告|△ Input warning
◇ 疑似风险|◇ Suspected risk
✓ 当前检查通过|✓ Current checks passed
✓ 适用检查通过|✓ Applicable checks passed
？证据不足|? Insufficient evidence
— 结构 / 未评估|— Structural / not assessed
定位此调用 ↗|Locate call ↗
定位这一次调用|Locate this call
定位公开消息|Locate message
定位调用|Locate call
定位事件|Locate event
查看证据|View evidence
查看事实与原文|View facts and source
关联事实|Linked facts
支持证据|Supporting evidence
反证|Counterevidence
缺少的信息 / 尚未验证|Missing or unverified information
未命名发现|Untitled finding
报告未提供解释。|No explanation provided.
报告没有提供解释。|No explanation provided.
报告尚未提供解释。|No explanation provided.
完整事实字段|Full fact record
该事实不在当前返回的 record 中。|This fact is not in the current record.
完整诊断|Run diagnosis
诊断记录|Diagnosis history
模型诊断队列|Diagnosis queue
完整诊断记录|Full diagnosis record
刷新分析结果|Refresh analysis
运行模型与预算（复用已有配置）|Model and budget
模型用量|Model usage
本次预算与配置（无密钥）|Budget and configuration
输入快照与来源映射|Input snapshot and source mapping
完整诊断结果原文|Full diagnosis result
支持事实 / 反证引用|Supporting and counterevidence references
支持事实|Supporting facts
其他候选比较|Other candidates
停止原因|Stop reason
模型原因说明|Model explanation
不确定性|Uncertainty
语义判定|Semantic verdict
模型修复建议|Suggested repair
诊断执行错误|Diagnosis execution error
最终选中候选（模型判断，非已证明根因）|Selected candidate (model assessment)
已记录完整流程|Full workflow recorded
已完成阶段|Completed stages
尚未开始|Not started
输入 SHA-256|Input SHA-256
策略 SHA-256|Policy SHA-256
本次适用并已执行|Applicable and executed
本次前提不足或不适用|Prerequisites missing or not applicable
缺少此规则支持的前提|Required prerequisites missing
缺少预期效果模型|Expected effect model missing
没有上一条调用可供比较|No preceding call to compare
调用目标与明确任务不一致|Call target differs from task
调用目标与明确任务一致|Call target matches task
当前选中实体|Selected entity
当前轨迹|Current trace
工具状态|Tool status
做了什么|Action
目标|Target
完整调用参数|Full call arguments
完整返回与工具状态|Full result and tool status
解析实体|Parsed entities
任务要求|Task requirements
安全策略|Safety policy
记录的用户任务与任务检查|Recorded task and task checks
这个节点有哪些发现|Node findings
这条轨迹有哪些发现|Trace findings
检查记录与适用范围|Checks and applicability
原版规则适用性|Original-rule applicability
检查原始记录|Raw check record
对应哪一次调用|Call location
调用实体|Call entity
原生 Tool Call ID|Native tool call ID
原生调用事件序号|Native call event sequence
完整来源映射|Full source mapping
完整引用信息|Full reference
记录字段|Record field
参数位置|Argument location
模型原始原因|Original model explanation
判断的限制与缺失信息|Limits and missing information
实体|Entity
任务要求|Task requirements
行动意图|Action intention
执行者|Actor
文件对象|File
复用原版规则|Reused framework rules
OpenClaw 任务规则|OpenClaw task rules
基础安全策略审计|Basic safety audit
自动风险行为规则|Automatic risk rules
模型风险调查|Model risk investigation
模型 TRACE 调查|Model TRACE investigation
工具运行记录|Tool runtime record
会话上下文|Session context
适用性检查|Applicability check
任务检查|Task check
读取范围|Read scope
外部操作批准|External-action approvals
数据来源|Data source
当前对话可见|Visible in current context
允许外发目标|Allowed destinations
明确禁止明文进入上下文|Plaintext prohibited in context
明确允许当前上下文使用|Use in current context allowed
权限边界 · 本条到底允许什么|Policy boundaries
完整引用|Full reference
证据编号|Evidence ID
字段|Field
事实引用|Fact references
还缺哪些信息|Missing information
本项结论之外的限制|Conclusion limits
可能的原因 · 候选，不是唯一根因|Possible causes
依据及限制|Evidence and limits
完整发现记录|Full finding record
尚无此项审查结果。|No review result yet.
检查依据|Check evidence
覆盖与证据限制|Coverage and evidence limits
调用总数|Total calls
已开展适用检查的调用|Calls with applicable checks
适用检查|Applicable checks
已完成检查|Completed checks
可观察的行为 / 消息步骤|Observable action / message steps
模型已调查步骤|Steps reviewed by model
模型未调查步骤|Steps not reviewed by model
实际检查了哪些行为|Review coverage
展开行为与适用策略|Actions and applicable policies
检查范围的原始记录|Raw scope record
本项通过|Passed
发现风险|Risk found
尚未检查|Not checked
审查范围|Review scope
安排调查|Route investigations
逐项核实|Investigate
汇总风险|Submit findings
引用与政策校验记录|Reference and policy validation
可见证据目录|Visible evidence catalog
引用解析次数|Reference resolutions
被拒绝的中间输出|Rejected intermediate outputs
被拒绝的输出及原因|Rejected outputs and reasons
选择的编号与还原结果|Selected references and resolutions
各调查实际可见的证据目录|Evidence visible to each investigation
减少重复上下文的收据|Context deduplication audit
运行未完成的原因|Why the run did not complete
取消本条风险审查|Cancel this review
已记录的阶段与证据查询|Recorded stages and evidence queries
本次运行保存的发现与完整结果|Saved findings and full result
本条风险审查进行中|Review in progress
本条未配置风险策略|Risk policy required
完整风险审查本条|Review this trace
风险项、原因与证据|Risks, reasons and evidence
正在读取本条已保存的风险报告…|Loading risk reports…
① 自动风险检查|1. Automatic risk checks
自动检查及适用范围|Automatic checks and scope
② 完整模型风险审查|2. Model risk review
模型调查发现|Model findings
模型阶段发现 · 完整流程未完成|Partial model findings
模型检查与覆盖|Model checks and coverage
阶段性调查线索|Intermediate investigation leads
本条风险策略 · 与旧版基础安全策略独立|Risk policy
来源与报告核验|Source and report verification
轨迹 ID|Trace case ID
风险定位验收|Risk comparison
案例 ID|Case ID
来源类别|Source type
是否构造|Constructed data
是|Yes
否|No
自动检查评测|Automatic-check evaluation
模型调查评测|Model-review evaluation
标准答案固定时间|Reference fixed at
基础案例|Base case
预先固定的位置|Fixed expected location
模型实际定位|Model location
标准状态|Expected status
模型状态|Model status
精确命中|Exact match
未发现|Not found
调用位置不同|Different call location
尚不确定|Uncertain
正确保留不确定|Uncertainty preserved
把缺证误判成确定|Unsupported certainty
尚无完整模型结果|No complete model result
构造修改与差异|Constructed changes
修改字段|Changed field
修改前|Before
修改后|After
完整修改记录|Full change record
完整的预先固定标准答案|Fixed reference answer
展开独立答案与修改差异|Show reference and changes
模型流程结束后可核对答案|Reference available after review
已知风险精确命中|Known-risk exact matches
范围外的风险判断|Findings outside reference scope
把缺证判成确定|Unsupported certainty
是否发现目标问题|Target finding detected
精确调用匹配|Exact call match
问题类型匹配|Finding type match
相关证据引用|Relevant evidence cited
独立评测原始记录|Raw evaluation record
待模型解析|Awaiting semantic parse
解析中|Parsing
本轮提取完成|Extraction completed
解析未完成|Parsing incomplete
模板解析|Template parse
模型解析|Model parse
已完成本片段提取|Segment extracted
模型语义解释|Model interpretation
尚未解析|Unparsed
语义未建模|Semantics not modeled
任务／权限约束|Task / permission constraint
已记录的陈述|Recorded statement
外部内容中的指令|Instruction in external content
行为含义解释|Behavior interpretation
授权陈述|Authorization statement
任务约束|Task constraint
用户任务要求|User task requirement
记录中的用户输入|Recorded user input
Agent 自身陈述|Agent statement
已记录消息|Recorded message
已记录内容块|Recorded content blocks
外部返回内容|External result
已记录操作|Recorded action
已提取 · 含未确定内容|Extracted · unresolved items
读取范围已配置|Read scope configured
保护配置已配置|Configuration protection enabled
已配置独立风险策略，展开核对适用范围|Risk policy configured
解析与审查|Parsing and review
结构已导入|Structure imported
进行中|In progress
待解析|Pending parse
尚未运行|Not run
解析与来源|Parsing and provenance
查看 / 编辑策略|View / edit policy
新审查进行中 · 当前展示上一份已保存解析|New review running · showing saved parse
来源角色|Source role
来源身份|Source authority
来源编号|Source ID
内容部分|Content section
原始字段|Source field
原文字符范围|Source character range
语气 / 确定性|Modality / certainty
语义提取|Semantic extraction
模板来源|Template source
模型提取 · 待对照事实|Model extraction · verify against facts
来源见引用|See source references
提取结果|Extraction result
核对原文与来源|Source and provenance
定位到记录|Locate record
案例|Case
当前解析版本|Parse version
尚无已保存模型解析|No saved model parse
版本来源|Version source
本次模型解析产物|Model-compiled result
已导入结构 · 待模型提取|Structure imported · model parse pending
当前作业阶段|Current job stage
原始导入覆盖与未支持记录|Import coverage and unsupported records
模型解析调用数未记录|Parse request count not recorded
待审语义片段|Semantic segments
已审片段|Reviewed segments
尚未审查片段|Unreviewed segments
覆盖导入步骤|Covered import steps
未审查步骤|Unreviewed steps
任务与权限约束|Task and permission constraints
已记录陈述及行为解释|Statements and behavior interpretations
完整缺口记录|Full gap record
保存的片段记录|Saved segment record
完整解析报告与预算记录|Full parse report and budget
允许读取|Allowed reads
禁止读取|Prohibited reads
取消本条诊断|Cancel diagnosis
关闭|Close
保存|Save
重新加载|Reload
刷新|Refresh
重试|Retry
返回|Back
打开|Open
展开|Expand
收起|Collapse
导入 SigNoz 或已有轨迹|Import SigNoz or saved traces
导入并查看|Import and view
任务与安全边界 · 可选补充|Task and policy (optional)
缺少任务记录时补充|Task text if missing from export
风险审查策略（留空表示尚未配置）|Risk policy (blank means unconfigured)
兼容旧 input.json 的基础安全策略|Basic policy for legacy input.json
我已核对，将此基础策略也应用于本批云端记录|Apply this basic policy to the imported cloud records
选择文件|Choose files
移除|Remove
文件|File
批量导入|Batch import
待导入文件|Files to import
尚未选择文件|No files selected
正在导入…|Importing…
导入中…|Importing…
随机抽取|Sample
抽样数量|Sample size
抽样范围|Sample scope
抽样|Sample
开始抽样|Draw sample
开始采集|Start capture
查看采集记录|Capture history
运行受控采集|Run controlled capture
采集结果|Capture result
采集中|Capturing
采集进行中|Capture in progress
模型配置|Model configuration
运行配置|Run configuration
风险审查队列|Risk review queue
取消运行|Cancel run
暂无运行记录|No runs
尚无诊断记录|No diagnosis history
尚未选择轨迹|No trace selected
加载中…|Loading…
读取中…|Loading…
正在读取…|Loading…
已载入|Loaded
导入完成|Import complete
操作完成|Done
未运行|Not run
已完成|Completed
待核实|Pending verification
解析|Parsing
审查|Review
证据|Evidence
摘要|Summary
详情|Details
原始数据|Raw data
模型调查结果|Model findings
自动检查结果|Automatic findings
自动规则|Automatic rules
正常|Normal
风险|Risk
语言|Language
界面语言|Interface language
构造记录|Derived record
导入记录|Imported record
隐私来源|Privacy trail
隐私来源与输出|Privacy sources and output
隐私审查|Privacy review
比较本组轨迹|Compare these traces
预期消息事件|Expected message event
实际消息事件|Actual message event
信息类别|Data category
报告包含受限信息|Restricted information in report
最终回答的明确引用与返回一致|Explicit final-answer quotations match the tool result
回答中可直接核对的原文引用／数值与对应返回一致；其他说明、任务全部要求和根因尚未由此验证。|Directly verifiable quotations and values in the answer match the corresponding result. Other explanations, the full task requirements and root causes have not been verified by this check.
依据：记录关联／明确提取|Basis: recorded association / explicit extraction
仅表示同一调用返回或从明确原文提取；不证明 Agent 的内部决策因果。|This links the result to the same call or extracts an explicit source statement; it does not prove the cause of the Agent's internal decision.
对应比较|Correspondence check
检查定位|Check location
移除敏感目录访问权限，并改用授权的数据来源。|Remove access to the sensitive directory and use an authorized data source.
英文|English
中文|中文
没有匹配的轨迹。试试清空搜索或调整筛选。|No matching traces. Adjust the search or filters.
请先选择轨迹。|Select a trace first.
一次最多比较 24 条轨迹。|Compare up to 24 traces at a time.
单次诊断最多提交 20 条|Submit up to 20 diagnoses at a time
对勾选的轨迹运行完整模型诊断|Run model diagnosis on selected traces
会话已接入 · 可查状态与覆盖|Session available
仅工具输入 · 未接入完整消息|Tool records only
有规则 / 模型问题|Rule / model findings
基础策略违规尝试|Basic-policy violation attempt
风险行为检查：未评估|Risk checks: not assessed
风险行为检查：暂无发现，见覆盖范围|Risk checks: no findings within reviewed scope
已记录·未评估|Recorded · not assessed
工具封装内部调用|Nested tool call
调用层级见详情|Call hierarchy in details
云端导出 · 原始字段可查|Cloud export
没有已保存的检查|No saved checks
没有适用检查|No applicable checks
检查未覆盖|Not covered by checks
策略不适用|Policy not applicable
部分检查通过|Some checks passed
检查通过|Checks passed
已解析|Parsed
尚未评估|Not assessed
已记录 · 未评估|Recorded · not assessed
结构节点|Structural node
无适用检查|No applicable checks
未覆盖|Not covered
未配置|Not configured
需要更多证据|More evidence needed
完整风险审查 · 所选轨迹|Review selected traces
查看全部|View all
来源信息|Provenance
来源与差异|Provenance and differences
轨迹对比|Trace comparison
模型审查|Model review
当前检查范围|Current check scope
风险结论|Risk assessment
工具运行状态|Tool runtime status
检查覆盖|Check coverage
已记录，未执行适用检查|Recorded; no applicable checks executed
调用代码|Call code
调用参数|Call arguments
返回内容|Result content
消息内容|Message content
状态详情|State details
原始记录|Source record
记录来源|Record source
原文与来源|Source and provenance
相关事实|Related facts
实体详情|Entity details
运行状态|Run status
来源详情|Provenance details
模型结果|Model result
风险详情|Risk details
审查记录|Review history
诊断|Diagnosis
自动|Automatic
手动|Manual
原始|Original
构造|Constructed
模型判断原因|Model explanation
任务与边界|Task and boundaries
节点|Node
路径|Path
操作|Action
原文字段|Source field
完整记录|Full record
参数|Arguments
返回结果|Result
名称|Name
类型|Type
时间|Time
耗时|Duration
数量|Count
页码|Page
顺序|Order
序号|Index
内容|Content
说明与限制|Notes and limits
未发现风险|No risks found
尚无结果|No result yet
查看原文|View source
完整消息|Full message
上下文|Context
调用层级|Call hierarchy
来源类别及记录|Source type and record
记录统计|Record statistics
筛选|Filter
问题|Finding
事实|Fact
关系|Relation
支持|Supporting
反例|Counterexample
工具返回错误|Tool returned an error
原生会话|Native session
云端轨迹|Cloud trace
未执行|Not executed
未验证|Unverified
语义状态|Semantic status
适用范围|Applicable scope
完成|Complete
失败|Failed
不确定|Uncertain
队列|Queue
帮助|Help
历史运行|Previous runs
审查协议|Review protocol
可选：仅当导出没有记录用户任务时，补充真实任务原文|Optional: provide the original task if missing from the export
原始未决检查|Original unresolved check
事实对应的原始会话事件；位置和摘要可在原文中核对。|Source session event for this fact; see the source for its location and hash.
该项检查通过|Check passed
检查发现问题|Issue found
检查证据不足|Insufficient check evidence
策略指定字段不可用|Configured field unavailable
工具检查尚未支持|Tool check unsupported
尚未配置检查|Check not configured
规则前提未提供|Rule prerequisites unavailable
本项不适用|Check not applicable
已记录，未单独评估|Recorded, not individually assessed
模型检查未通过引用校验|Model check not validated
检查结论存在分歧|Check conclusions disagree
存在问题，查看来源|Issue found; inspect its source
部分检查通过，仍有检查限制|Some checks passed; limitations remain
已执行的适用检查通过|Executed applicable checks passed
尚不能完成相关检查|Related checks remain unresolved
无出处关联|No source link
无依据关联|No basis link
无对应关联|No correspondence link
无检查关联|No check link
无适用的状态检查|No applicable state checks
消息接入|Messages imported
结构化事件|Structured events
仅保留原文|Source only
未支持内容|Unsupported content
已保留事件|Events preserved
已保留元数据|Metadata preserved
未观测内容|Unobserved content
原文（原始语言）|Source (original language)
检查前提|Check prerequisites
自动检查已完成|Automatic checks completed
仅部分检查通过|Some checks passed
全部适用检查通过|All applicable checks passed
未覆盖的检查|Uncovered checks
检查记录|Check records
自动结果|Automatic results
模型结果原文|Original model result
缺失证据|Missing evidence
已审查|Reviewed
尚未审查|Not reviewed
没有检查记录|No check records
模型发现|Model findings
记录|Recorded
查看检查详情|View check details
发现与检查|Findings and checks
检查限制|Check limitations
检查结果|Check results
检查明细|Check details
完整流程|Full workflow
待核对|Needs review
已导入|Imported
本次调用的证据不足|Insufficient evidence for this call
采集完整性尚未确认|Capture completeness unconfirmed
风险检查证据不足|Risk check lacks evidence
原文（保留原始语言）|Source (original language)
原始内容|Original content
自动规则不调用模型。完整风险审查先进行 PARSE 语义解析，再复用 MAP → ROUTE → TRACE → SUBMIT 和证据查询，分别核对风险及其限制。|Automatic checks run locally. Full review uses PARSE, MAP, ROUTE, TRACE and SUBMIT.
云端记录|Cloud record
构造副本|Constructed copy
比较三条轨迹|Compare all three
全部轨迹|All traces
精选轨迹|Featured traces
风险规则|Risk rules
原版规则|Framework rules
内部调用|Nested call
角色|Role
分析结果|Analysis result
依据|Basis
凭据|Credentials
出处|Source
向右|Right
向左|Left
对应|Correspondence
指针|Pointer
终点|To
起点|From
待导入|Ready to import
总时限|Time limit
新记录|New record
无记录|No record
未命名|Unnamed
未声明|Not declared
未统计|Not counted
事实版本|Fact version
会话摘要|Transcript hash
会话消息|Session message
会话记录|Session record
关联调用|Linked call
分析版本|Analysis version
判定理由|Reason
刷新结果|Refresh results
原始对照|Original baseline
原始序号|Original sequence
处理范围|Processing scope
实际状态|Actual status
实际调用|Actual call
审查策略|Review policy
对应原文|Corresponding source
导入覆盖|Import coverage
尚无记录|No records
尚未评测|Not evaluated
工具消息|Tool message
工具记录|Tool record
引用编号|Reference ID
形成结论|Submit conclusion
抽取数量|Sample size
整理证据|Map evidence
未知占位|Unknown placeholder
来源记录|Source record
构造变体|Constructed variant
查看详情|View details
模型分析|Model analysis
正在导入|Importing
正在进行|In progress
派生修改|Derived changes
状态属性|State property
直接记录|Directly recorded
结果比较|Result comparison
结论冲突|Conflicting conclusions
规则违反|Rule violation
规范轨迹|Canonical trace
解析结果|Parse result
解析详情|Parse details
记录范围|Record scope
评测范围|Evaluation scope
语义已审|Semantics reviewed
读取结果|Read result
调用详情|Call details
轨迹概况|Trace overview
选择线索|Route leads
部分导入|Partially imported
重新读取|Reload
问题详情|Finding details
一分钟上手|Quick start
事件 ID|Event ID
仅工具输入|Tool input only
任务诊断中|Task diagnosis running
关联未确认|Link unconfirmed
原文已保留|Source preserved
参数见详情|Arguments in details
已记录完成|Completion recorded
已配置凭据|Credentials configured
已随机抽取|Sampled
当前不可用|Currently unavailable
待语义解析|Semantic parse pending
抽取并查看|Sample and view
文件为空。|The file is empty.
无提取结果|No extracted items
无文本正文|No text body
有安全风险|Safety risk found
有错误判断|Error identified
未记录任务|Task not recorded
来源见详情|Source in details
检查不适用|Check not applicable
沿用已知值|Previous value
策略已配置|Policy configured
策略未配置|Policy not configured
结论与解释|Conclusion and explanation
范围未声明|Scope not declared
证据引用数|Evidence references
证据待核验|Evidence unverified
！错误判断|! Error identified
— 尚无记录|— No record
✓ 检查通过|✓ Checks passed
主要影响事件|Primary affected events
主要影响事实|Primary affected facts
任务诊断记录|Task diagnosis history
会话状态检查|Session state checks
修改前后差异|Before / after changes
先分清两件事|Task and safety outcomes
全组完整诊断|Diagnose all cases
关联导入步骤|Linked import steps
凭据是否可用|Credentials available
刷新审查状态|Refresh review status
原生事件序号|Native event sequence
原生会话文件|Native session file
原生消息接入|Native messages imported
发现注入问题|Injected issue detected
取消这条诊断|Cancel this diagnosis
受限信息外发|Restricted data sent externally
完整记录正文|Full record content
完整评测结果|Full evaluation result
完整诊断本条|Diagnose this trace
完整诊断进度|Diagnosis progress
定位能力验证|Location evaluation
实际导入步骤|Actual import step
审查尚未完成|Review incomplete
导入步骤编号|Import step number
尚未记录完成|Completion not recorded
开始受控采集|Start controlled capture
引用其他事实|Other fact references
引用相关证据|Relevant evidence cited
当前验证样本|Current evaluation case
总时限（秒）|Time limit (seconds)
暂无风险发现|No risk findings
最多工具查询|Maximum tool queries
最多模型轮数|Maximum model turns
未提供原记录|Source record unavailable
本条完整诊断|Diagnosis for this trace
本项验收通过|Evaluation passed
查看调用参数|View call arguments
模型作业状态|Model job status
模型直接发起|Direct model call
流程部分完成|Workflow partially completed
父调用 ID|Parent call ID
等待阶段记录|Awaiting stage record
解析后的事实|Parsed facts
语义部分完成|Semantics partially reviewed
载入完整会话|Load full session
返回正文缺失|Result body missing
重新诊断本条|Re-run diagnosis
问题类型正确|Correct finding type
预期导入步骤|Expected import step
风险审查预算|Risk review budget
风险行为审查|Risk behavior review
JSON 定位|JSON location
任务诊断排队中|Task diagnosis queued
保护配置被修改|Protected configuration modified
先选择一条轨迹|Select a trace
多轨迹风险比较|Compare trace risks
完整调用 ID|Full call ID
定位到同一调用|Same call located
实际 Span|Actual Span
审查队列与历史|Review queue and history
已提取结构事实|Structural facts extracted
已载入指定轨迹|Requested traces loaded
已载入验证样例|Evaluation cases loaded
形成于导入步骤|Created at import step
按来源记录核对|See source record
无预设故障位置|No preset fault location
最终选中的候选|Selected candidate
未提供原因说明|No reason provided
未记录解析缺口|No parse gaps recorded
本条诊断进行中|Diagnosis running
来源与覆盖范围|Source and coverage
来源与解析范围|Source and parse scope
来源与采集核验|Source and capture verification
检查前提未满足|Check prerequisites missing
检查版本与关联|Check version and links
检查的原始证据|Original check evidence
模型与运行预算|Model and budget
模型定位的步骤|Model-located step
模型配置与预算|Model and budget configuration
版本与能力范围|Version and capabilities
状态与证据指针|State and evidence pointers
环境 / 记录|Environment / records
由已有配置提供|From existing configuration
策略字段未提供|Policy field unavailable
规范化事件摘要|Normalized event hash
记录中直接关联|Directly linked in record
证据与检查记录|Evidence and check record
语义解析与来源|Semantic parse and provenance
语义解析的限制|Semantic parse limits
请核对出处事件|See source event
读取审查记录…|Loading review history…
调用信息不完整|Call details incomplete
返回正文未提供|Result body not provided
采集与覆盖说明|Capture and coverage
风险审查排队中|Risk review queued
验证范围与限制|Evaluation scope and limits
仅保留原文的事件|Events preserved as source only
原始接入核对记录|Source import audit
原生记录附带摘要|Hash included in native record
原记录未提供任务|Task absent from source
基于真实轨迹构造|Constructed from a real trace
审查状态暂不可用|Review status unavailable
尚未提供对照结果|No comparison result
已采集并自动检查|Captured and checked
意图（核对出处）|Intention (verify source)
打开保留的旧界面|Open previous interface
无可核实调用定位|No verifiable call location
未定位到具体调用|No specific call located
本地服务连接失败|Local service connection failed
本条完整修改差异|Full changes for this case
本条独立评测记录|Independent evaluation record
本次运行失败原因|Run failure reason
本项独立比较记录|Independent comparison record
来源资料勘误说明|Source correction note
查看完整检查结果|View full check results
查看本条模型诊断|View model diagnosis
流程尚未完整完成|Workflow incomplete
由确定性规则建立|Derived by deterministic rules
系统是否定位正确|Location match
载入定位验证样本|Load location cases
载入风险验收案例|Load risk cases
采集一组工具演示|Capture a tool demo
随机抽取已有轨迹|Sample saved traces
随机种子（选填）|Random seed (optional)
事实版本与来源原文|Fact version and source
保存策略并重新检查|Save policy and recheck
先分析，再核对答案|Analyze, then compare
参考答案与修改记录|Reference and changes
受限明文进入上下文|Restricted plaintext in context
可用（不展示密钥）|Available
含未支持内容的事件|Events with unsupported content
完整位置与原始记录|Full location and source record
尚无独立对照结果。|No independent comparison result.
展开答案与修改差异|Show reference and changes
已刷新轨迹与分析。|Traces and analysis refreshed.
已知范围无确定误报|No confirmed false positives in known scope
已载入完整会话示例|Full-session examples loaded
已载入工具验证样例|Tool evaluation cases loaded
已载入开发验收案例|Development cases loaded
当前分析映射未提供|Not in current analysis mapping
提取结构事实的事件|Events with extracted facts
无；该字段限制外发|None; this field limits external transfer
未经批准的外部操作|Unapproved external action
本条轨迹的安全边界|Safety boundaries for this trace
本项判断引用的证据|Evidence cited for this assessment
查看参考答案与差异|View reference and changes
没有保存支持证据。|No supporting evidence saved.
没有单独的差异记录|No separate change record
用已知修改验证定位|Evaluate known changes
缺失信息与尚未验证|Missing and unverified information
自动规则与安全审计|Rules and safety audit
解析审计与证据范围|Parse audit and evidence scope
调用 / Span|Call / Span
这个样本修改了什么|Case changes
预先固定的注入位置|Fixed injection location
风险审查队列与历史|Risk review queue and history
Agent 公开表达|Agent statement
Agent 最终答复|Agent final answer
云端 Span ID|Cloud Span ID
内部认知与未观测模块|Unobserved internal modules
原始定位 / 导入步|Source / import step
发现的证据与不确定性|Finding evidence and uncertainty
后续影响尚不能确认。|Subsequent effects are unconfirmed.
固定时间与完整性记录|Fixed time and integrity record
展开完整诊断记录 ↗|Open full diagnosis ↗
已载入独立保留测试集|Holdout cases loaded
当前分类没有发现项。|No findings in this category.
打开本轨迹的最新审查|Open latest review
未记录路径 / 目标|Path / target not recorded
未配置；不据此判越权|Unconfigured; access not assessed
查看事件原文与事实。|View event source and facts.
此内容类型尚未结构化|Content type not structured
轨迹名称需要是文本。|Trace name must be text.
选择一个事件或事实。|Select an event or fact.
部分审查状态暂不可用|Some review statuses are unavailable
SigNoz 云端记录|SigNoz cloud record
SigNoz 原始记录|SigNoz source record
先从左侧选择一条轨迹。|Select a trace from the library.
先在左侧选择一条轨迹。|Select a trace from the library.
全组分析结束后开放答案|References unlock after all analyses
完整诊断 · 选中轨迹|Diagnose selected traces
已载入真实云端验收记录|Cloud cases loaded
未关联到具体调用的发现|Findings without a specific call
未提供可验证的任务契约|No verifiable task contract
本对照没有预设确定风险|No preset confirmed risk
本条预先固定的标准答案|Fixed reference for this case
规则适用性（整条轨迹）|Rule applicability across trace
访问策略明确禁止的路径|Access to explicitly prohibited path
预期调用 / Span|Expected call / Span
审查中 · 显示上次结果|Review running · showing previous result
对照样本，无注入故障位置|Baseline; no injected fault location
对象 · 属性 / 状态|Object · property / state
构造副本 · 未实际执行|Constructed copy · not executed
构造数据 · 未实际执行|Constructed data · not executed
根因候选（保留不确定性）|Root-cause candidates
正在校验策略并重新检查…|Validating policy and rechecking…
此报告没有逐项检查记录。|No item-level checks in this report.
此项不适用 / 尚未验证|Not applicable / unverified
JSON 顶层需要是对象。|Top-level JSON must be an object.
Span / 原始记录标识|Span / source record ID
可留空；相同种子可复现抽样|Optional; the same seed reproduces the sample
含未支持内容块，原文仍保留|Unsupported blocks preserved in source
基础对照 · 没有注入故障|Baseline · no injected fault
导入覆盖、来源及未支持记录|Import coverage, sources and unsupported records
已有配置可用（不展示密钥）|Existing credentials available
当前筛选没有可抽取的轨迹。|No traces available in this filter.
正在读取已保存的诊断记录…|Loading saved diagnosis…
没有可显示的原生会话事件。|No native session events available.
点击上方按钮才会调用模型。|Use the button above to run the model.
独立评测 · 不是模型输出|Independent evaluation
真实 Agent 受控执行|Real controlled Agent execution
诊断执行错误，尚无可靠结论|Diagnosis failed; no reliable conclusion
请输入 1–24 的整数。|Enter an integer from 1 to 24.
轨迹级发现，无具体调用定位|Trace-level finding; no specific call location
运行时调用 · 层级未确认|Runtime call · hierarchy unconfirmed
这条轨迹还没有完整会话输入|Full session input unavailable
只表示证据关联，不证明因果。|Evidence link; not a causal claim.
本条轨迹的风险策略 JSON|Risk policy JSON for this trace
模型提交的原始原因与不确定性|Model explanation and uncertainty
策略需要是 JSON 对象。|Policy must be a JSON object.
规范轨迹文件超过 4 MB。|Canonical trace exceeds 4 MB.
请在轨迹池勾选至少一条轨迹。|Select at least one trace.
请提供匹配此轨迹的安全策略。|Provide a safety policy for this trace.
风险验收构造 · 未实际执行|Constructed risk case · not executed
与真实 Agent 接入的区别|Capture type
基础轨迹的来源见下方采集记录。|See capture record for the base trace source.
外部指令，执行情况需另行核对。|External instruction; execution unverified.
已取消诊断，保留已有分析结果。|Diagnosis cancelled. Saved results retained.
当前没有已注册的定位验证样例。|No registered location cases.
报告记录了可支持该效果的证据。|The report includes evidence supporting this effect.
轨迹与策略合计超过 4 MB。|Combined trace and policy exceed 4 MB.
选中的原生事件不在当前输入中。|Selected event is not in the current input.
一次完整诊断最多选择 20 条。|Select up to 20 traces for diagnosis.
以导入事件计；仅提取部分明确属性|Import events; explicit attributes only
基础策略需要是 JSON 对象。|Basic policy must be a JSON object.
尚未导入成功，请查看各文件提示。|Import incomplete. See file messages.
已记录结构化结果，展开查看正文。|Structured result recorded. Expand to view.
已记录返回事件，但返回正文缺失。|Result event recorded; result body missing.
引用原事实与原文，未添加因果边。|Original facts and source references.
本地分析标识（非云端 Span）|Local analysis ID (not a cloud Span)
本批有未导入记录，请核对下方说明|Some records were not imported. See details below.
采集已启动，完成后会显示新轨迹。|Capture started. New traces appear when complete.
风险策略需要是 JSON 对象。|Risk policy must be a JSON object.
高级编辑 · 风险策略 JSON|Advanced · risk policy JSON
以导入事件计；尚未形成更细语义状态|Import events; finer semantic state unavailable
完整诊断记录：阶段、查询与原始结果|Diagnosis stages, queries and raw results
已导出当前轨迹、原事实与分析结果。|Trace, original facts and analysis exported.
已清空工作区，保存的轨迹没有删除。|Workspace cleared. Saved traces retained.
当前没有已注册的这组风险验收案例。|No registered risk cases in this group.
未生成可分析轨迹，请查看导入核对。|No analyzable trace produced. See import audit.
未配置禁止；不能仅凭外发限制判违规|No context prohibition configured
本节点没有已执行且适用的行为检查。|No applicable behavior checks executed for this node.
Agent 陈述，未作为已验证事实。|Agent statement; not a verified fact.
一次完整风险审查最多选择 20 条。|Select up to 20 traces for risk review.
定位已保存的实体，不据此添加因果关系|Locate the recorded entity
已保存；解析覆盖与来源可在轨迹中核对|Saved. Parse coverage and source available in trace.
真实 OpenClaw · 受控运行|Real OpenClaw · controlled run
策略已保存；可重新运行完整风险审查。|Policy saved. Re-run the risk review to apply it.
仅包含工具调用与返回；会话消息未提供。|Tool calls and results only; session messages unavailable.
依据任务、轨迹、策略与证据调查风险行为|Review behavior against task, trace, policy and evidence
单文件超过 8 MB，请缩小导出范围。|File exceeds 8 MB. Narrow the export range.
尚无语义报告。运行完整风险审查后查看。|No semantic report. Run a risk review first.
未记录此类证据，不能据此排除其他解释。|No evidence of this type recorded.
来自事实参数的实体关系；连线不表示因果|Entity relations from facts; links do not imply causation
没有直接绑定此节点事实的原版规则评估。|No framework rule evaluation linked to this node.
没有记录反证；这不等于已排除其他解释。|No counterevidence recorded; alternatives remain open.
点击事件、状态或问题，信息会显示在这里|Select an event, state or finding.
选择或拖入 SigNoz / 轨迹文件|Choose or drop SigNoz / trace files
不可用；诊断会报失败，规则和安全结果保留|Unavailable; diagnosis cannot run. Existing checks remain.
可与前两项重叠；不作为规则违反或任务失败|May overlap; not a rule violation or task failure
完整诊断会依据任务、调用和证据逐步分析。|Diagnosis uses the task, calls and recorded evidence.
可选：粘贴这批轨迹适用的风险策略 JSON|Optional: paste risk policy JSON for these traces
尚无模型诊断记录。可在工作区诊断当前轨迹。|No saved model diagnosis. Run it from the workspace.
尚未运行模型风险审查。自动规则仍可先查看。|No model risk review yet. Automatic checks are available.
已有采集正在进行，可关闭窗口继续查看轨迹。|Capture in progress. You can close this dialog.
已读取保存的规则、安全审计及模型诊断结果。|Saved rules, audits and diagnosis results loaded.
请先选择本组的真实云端轨迹或两个构造对照。|Select one of the three featured traces.
选择轨迹，再点击调用或问题，查看它的证据。|Select a trace, then a call or finding.
部分轨迹分析尚未返回；不能据此判断无问题。|Some trace analyses are unavailable.
risk_policy 需要是独立策略对象。|risk_policy must be a separate policy object.
只表示文件内消息是否保留，不代表语义全部理解|Messages preserved; not a measure of semantic coverage
已请求取消，已保存的规则和安全结果仍可查看。|Cancellation requested. Saved checks remain available.
当前证据或调查结果不足，系统保留不确定结论。|Evidence is insufficient for a conclusive assessment.
服务将按真实 Trace 整理，保留原始字段|Grouped by Trace ID; original fields preserved
依据已声明的任务契约；工具成功不等于任务完成。|Assessed against the declared task contract.
只检查当前策略覆盖的范围，不代表整体绝对安全。|Assessment applies only to the configured policy scope.
已请求取消风险审查，保留自动规则和已保存结果。|Cancellation requested. Saved checks and results retained.
当前会话没有这条事实版本；保留引用，无法展开。|Fact version absent from session; reference retained.
本次最多导入 24 个文件，尚未提交任何文件。|Import up to 24 files at a time. No files submitted.
独立 input.json 的共同基础安全策略|Shared basic policy for standalone input.json
选择轨迹后，这里会说明它做了什么、发现了什么。|Select a trace to view its activity and findings.
从左侧选择轨迹，或载入“风险验收案例”开始查看。|Select a trace from the library.
报告没有可精确匹配的调用或消息，保留为轨迹级发现|No matching call or message; trace-level finding
本组仅用于开发验收，不能据此宣称泛化定位准确率。|Development cases; not a general accuracy estimate.
此实体无已关联发现；请看下面检查的适用性与结果。|No linked findings. See applicable checks below.
没有关联到工具返回，无法从当前记录确认执行结果。|No linked tool result; execution outcome unverified.
用于快速浏览已有记录。抽样不会生成新的执行记录。|Sample existing records.
这条提取结果引用任务或原始字段，请展开来源核对。|Expand the source to verify this extraction.
当前范围没有发现项。没有告警不代表所有检查均适用。|No findings within the assessed scope.
报告未能精确对应到调用或公开消息，保留轨迹级发现。|Trace-level finding; no exact call or message match.
未提供风险策略原文，不能假定其他轨迹使用相同策略。|Risk policy unavailable for this trace.
没有可关联到当前实体的检查记录，不能据此判为通过。|No checks linked to the selected entity.
当前筛选没有发现项。未评估或不适用的检查不会计为通过。|No findings match this filter.
本地服务未返回有效结果，请确认当前地址的服务正在运行。|Invalid response from the local service.
当前没有可展示的结构状态。原始消息仍可在完整会话中查看。|No structured states. Source messages remain in Session.
当前证据支持违规尝试；是否实际生效需结合返回和后续记录。|Evidence supports an attempt; actual effect remains to be verified.
此轨迹没有返回统一分析结果，请刷新分析；不据此标为通过。|Analysis unavailable. Refresh to retrieve results.
这个事件或事实没有适用的会话状态检查；不能据此标成绿色。|No applicable session state checks.
这条属于开发验收集，曾用于改进实现，不作为独立能力证明。|Development case used to improve the implementation.
基础对照样本没有注入目标替换故障；仍按真实任务和证据检查。|Baseline without injected target replacement.
尚未产生 SUBMIT 结果；规则检查和安全审计仍然有效。|No SUBMIT result. Saved rules and safety checks remain available.
模型选中的候选是需要核对的解释，不能当作已证明的唯一根因。|Model-selected candidate; not a proven unique root cause.
此视图仅看工具调用。完整消息和状态变化请用前面的三个视图。|Tool calls. Use Session or States for more context.
还没有模型诊断记录。点击“完整诊断”才会调用已配置的模型。|No model diagnosis. Run a diagnosis to begin.
下方是已保存的阶段线索，尚未完成汇总核验，统一保留为待核实。|Saved intermediate leads; final review is incomplete.
先看系统结果，再按需打开答案；失败、取消或不确定都如实保留。|References are available after the review ends.
分别核对原始记录接入、结构事实提取、未支持语义和未观测认知。|Import, structural extraction and semantic coverage.
尚无模型风险审查。可先查看自动规则，再点击“完整风险审查”。|No model review. Automatic checks are available.
报告未列出额外缺失项，但没有证明唯一根因或未记录的内部思考。|No additional gaps listed; unique cause remains unproven.
本批云端请求超过 16 MB，请减少文件或缩小导出时间范围。|Batch exceeds 16 MB. Reduce files or export range.
完整诊断每批最多 20 条，请减少选择；图谱仍支持 24 条。|Diagnose up to 20 traces per batch.
当前事件 / 事实是这项检查的对照证据，不是该检查定位的错误。|This event is comparison evidence, not the located error.
当前没有关联的发现。是否已评估、哪些规则适用，请查看检查记录。|No linked findings. See check records for coverage.
当前记录没有已解析的调用节点。切换“实体关系”查看结构与事实。|No call nodes. Open Entities to view structural facts.
报告未列出额外缺失项；这不代表已验证所有前提或已证明唯一根因。|No additional gaps listed; scope and evidence limits still apply.
本组分析已结束。点击后，独立查看标准答案与系统实际定位的对照。|Analysis complete. View the reference comparison.
这是保留的旧版结果；重新审查会另存一次运行，已有记录仍可核对。|Saved legacy result. Re-run to create a current review.
MAP / ROUTE / TRACE / SUBMIT 均完成|MAP / ROUTE / TRACE / SUBMIT completed
以下模型输出属于未完成流程中的已保存结果，不能当作完整审查成功。|Saved output from an incomplete review.
在已提供的任务契约和证据范围内，模型没有找到支持任务失败的错误。|No task failure supported within the provided contract and evidence.
模型在此步骤记录了错误判断。展开可核对原始原因、证据和不确定性。|Expand to inspect the model finding and evidence.
等待全组在相同配置下结束诊断。标准答案与修改位置不会参与模型分析。|Waiting for all reviews. References are excluded from model input.
运行后会补充错误位置、原因和证据。自动规则及安全审计结果始终保留。|Diagnosis adds error candidates, explanations and evidence.
“通过”只对实际执行且适用的检查成立。没有记录或不适用的项目不判绿。|Passed means the check was applicable and executed.
没有规则告警不等于全部安全。只有明确适用并已执行的检查，才显示通过。|Only applicable, executed checks can pass.
下列为已保存的阶段发现，保留当时状态；完整流程未完成，不计作定位成功。|Saved partial findings; the full review did not complete.
当前轨迹未配置独立风险策略，因此未开展这些风险检查；不能据此判为安全。|No risk policy configured; these checks were not performed.
任务语义候选：模型认为值得核查，但尚无结构化政策验证；不计作已确认风险。|Semantic candidate; policy verification pending.
先解析已记录的任务和内容，再审查多项风险。各阶段进度以本次保存记录为准。|Recorded parsing and review stages.
这个范围超过一次可完整抽取的集合，请使用“全部来源”配合名称搜索来抽样。|Narrow the selection using source and name filters.
风险审查保留多个发现；任务诊断沿用原版定位失败原因，两者不能混成唯一根因。|Risk review and task diagnosis are stored separately.
这会实际运行项目已有的本地工具演示，用于观察通过、违规尝试及证据不足等情况。|Run the local controlled tool demonstration.
导入步骤是统一输入里的编号；原生事件顺序与 Agent 内部认知步骤另行区分。|Import steps identify normalized records.
支持 JSON、JSONL 和文本文件；同一轨迹的多个 Span 可以一起导入|JSON, JSONL or text. Import related Span files together.
本条未配置独立 risk_policy；可载入风险验收案例或导入带策略的文件包|No risk_policy configured for this trace.
本次保存语义解析、逐行为审查范围和引用校验，区分输入警告、疑似与有证据的风险。|Semantic parsing, behavior coverage and reference validation saved.
编辑只影响本条轨迹。保存后重新进行自动检查；完整模型审查需要点击按钮另行运行。|Applies to this trace. Saving reruns automatic checks; model review is separate.
请使用真实任务允许范围，不能把模型猜测或 Agent 自称的批准写成确定授权。|Use verified task permissions and approvals.
基于已核验的 OpenClaw 轨迹修改独立副本，用于测试系统能否找到已知故障。|Independent variants of a verified OpenClaw trace.
执行本地受控工具，保存新的轨迹并自动审计。不会启动 OpenClaw Agent。|Run a controlled tool demo, then save and audit its traces. No OpenClaw Agent is started.
此对照没有预先注入的确定风险。只评价已知范围内是否误报，不能认定整个轨迹绝对安全。|No confirmed risk injected; false positives assessed within known scope.
策略已保存，自动检查已更新。原模型结果仍保留，请重新运行完整风险审查以使用新策略。|Policy saved and automatic checks updated. Re-run model review for the new policy.
请使用已转换的 OpenClaw input.json，包含 1–500 个步骤。|Provide a converted OpenClaw input.json with 1–500 steps.
这条是未修改的基础对照。来源类别及采集记录用于核验真实性；不默认基础轨迹没有问题。|Unmodified baseline; see capture records for provenance.
阶段完成表示流程结束；只有逐项检查记录才能说明覆盖范围，不能把未检查项目算作安全。|Item-level checks determine coverage.
此报告没有发现项。请继续核对适用检查、覆盖范围及证据缺失；不能据此认定整个任务安全。|No findings. See coverage and evidence limits.
读入轨迹会自动检查。只有点击完整风险审查或任务诊断才会调用模型；查看已有结果不会调用。|Imports run automatic checks. Model review runs only when requested.
这是构造测试数据，不是修改后实际执行的记录。已知位置用于独立核对，不进入模型诊断输入。|Constructed test data, not an executed run. Reference locations are excluded from model input.
这是保留的历史审查，尚未包含本次新增的语义解析与逐行为覆盖记录。已有发现和失败继续保留。|Historical review without current semantic and behavior coverage records.
“未观测”不能补成真实记忆、计划或反思，也不能直接判为错误。以下逐事件核对接入和语义范围。|Unobserved internal activity is not inferred or treated as an error.
先保存原文并完成结构解析及自动检查；点击“完整风险审查”后，才调用模型进行语义解析和风险调查。|Import preserves source and runs checks. Run risk review for model parsing and investigation.
本组是固定版本的小规模复测；样本数量有限且已有早期运行，不能据此宣称首次盲测或整体定位准确率。|Small fixed-version regression set; not an overall accuracy estimate.
拒绝表示某次模型中间输出没有通过校验，不表示被调查轨迹发生了违规。纠正成功和最终失败都会保留记录。|Rejected intermediate model outputs failed validation; they are not trace violations.
请点击主工作区的“查看 / 编辑策略”，明确允许范围与授权要求。也可以在导入文件包中提供风险策略。|Use View / edit policy to configure scope and permissions.
导入步骤是转换后的调用 / 返回编号，不等于原始 Agent 的执行步骤。原文只展示实际保存的内容。|Import steps are normalized call / result indices.
检查越权、未经批准的操作、信息外传和不可信指令等行为。工具成功不等于合规，没有告警也可以运行完整审查。|Review access, approvals, data transfer and untrusted instructions.
三件事分别判断：能否读取、能否在当前对话中看到、能否发到外部。禁止外发不会自动变成禁止本地读取或对话内使用。|Read, context-use and external-transfer permissions are evaluated separately.
这些是当前配置的检查边界。任务原文仍参与调查；尚未结构化的任务限制会标为待核实，不能靠一句引文冒充已验证政策。|Configured policy boundaries; unstructured task constraints remain subject to verification.
界面语言|Interface language
原始分析记录|Original analysis record
证据原文|Original evidence
原始事件与提取结果|Source events and extracted facts
原始内容已保留|Source content preserved
其全部语义尚未建模。|Its full meaning has not been modeled.
用户任务|User task
用户任务原文|Original user task
工具调用参数|Tool arguments
工具返回原文|Original tool result
消息原文|Original message
文件路径|File path
读取目标|Read target
文件存在状态（读取时）|File existence at read time
读取到的内容|Read content
最终回答|Final answer
调查步骤|Investigation steps
公开消息实体|Recorded message entity
模型风险判断|Model risk finding
疑似风险|Suspected risk
配置维护|Configuration maintenance
网页与信息提交|Web content and information submission
人数统计|Personnel count
风险副本（未执行）|Constructed risk variant (not executed)
真实基线|Recorded baseline
SigNoz 云端轨迹|SigNoz cloud trace
构造对照一|Constructed variant 1
构造对照二|Constructed variant 2
只减少重复原文和查询结束后不再使用的SQL说明；结构化结果、检索错误与证据限制继续保留。完整视图另存在本次运行的 model_evidence_views.jsonl。|Only repeated source text and unused SQL guidance are shortened. Structured results, query errors and evidence limits are retained. Full views are saved in model_evidence_views.jsonl.
独立规范输入使用下方策略；带 source + policy 的文件包使用自身策略。云端原始记录默认不套用其他案例的权限。|Standalone normalized inputs use the policy below. Packages containing source + policy use their own policy. Cloud records do not inherit another case's permissions.
真实 OpenClaw 轨迹使用已保存输入导入。实时 Agent 接入仍是预留接口，当前页面不会把工具演示标成 OpenClaw Agent 运行。|Import saved inputs for recorded OpenClaw traces. Live Agent ingestion is not connected. Controlled tool demonstrations are identified separately.
此内容类型尚未结构化|This content type has not been structured
范围未声明|Coverage not specified
查看事件原文与事实。|Inspect the recorded event and associated facts.
含未支持内容块，原文仍保留|Contains unsupported blocks; source content is retained
未提供风险策略原文，不能假定其他轨迹使用相同策略。|No source policy provided; policies from other traces do not apply automatically.
尚未运行模型风险审查。自动规则仍可先查看。|Model risk review has not run. Automatic checks are available.
被拒绝的输出及原因|Rejected outputs and validation reasons
选择的编号与还原结果|Selected reference IDs and resolved evidence
各调查实际可见的证据目录|Evidence catalogs visible to each investigation
减少重复上下文的收据|Context reduction receipts
真实采集记录|Recorded capture
事件 / 状态|Events / states
全部载入轨迹|All loaded traces
基于真实云端轨迹构造 · 未实际执行|Constructed from a real cloud trace · not executed
真实 SigNoz 云端导出|Real SigNoz cloud export
SigNoz 原始导出导入 · 来源凭据见原文|Imported SigNoz export · inspect source receipts
OpenClaw 会话格式导入 · 真实性见来源核对|Imported OpenClaw session · inspect provenance
来源核对 A · 读取报告|Source check A · read report
来源核对 B · 缺文件后读取备份|Source check B · read backup after missing file
来源核对 A|Source check A
来源核对 B|Source check B
读取报告|Read report
缺文件后读取备份|Read backup after missing file
外部内容包含可疑指令|Suspicious instructions in external content
报告包含受限信息|Restricted information in report
允许进入上下文|Allowed in context
允许进入报告|Allowed in report
禁止|Forbidden
允许|Allowed
证据检索|Evidence retrieval
已请求的证据查询已完成|Requested evidence queries completed
证据检索仍有缺口|Evidence retrieval has gaps
失败的查询尝试|Failed query attempts
读取、进入上下文、进入报告、对外发送分别按已配置的策略判断；其中一项被禁止，不自动代表其他项也被禁止。|Read access, context use, report inclusion and external transfer follow their respective configured policies. A restriction on one does not imply a restriction on another.
`.trim().split('\n');
  const catalog=Object.create(null);
  for(const entry of entries){const index=entry.indexOf('|');catalog[entry.slice(0,index)]=entry.slice(index+1);}
  window.ProDebuggerTranslations=Object.freeze(catalog);
  const count=(value,singular,plural=singular+'s')=>`${value} ${String(value)==='1'?singular:plural}`;
  window.ProDebuggerTranslationPatterns=[
    [/^删除轨迹：(.+)$/,(m,t)=>`Delete trace: ${t(m[1])}`],
    [/^将删除 (\d+) 条轨迹：$/,m=>`Delete ${m[1]} trace(s):`],
    [/^保留 (\d+) 条受保护或正在分析的轨迹。$/,m=>`Keep ${m[1]} protected trace(s) or trace(s) with an active review.`],
    [/^已删除 (\d+) 条轨迹，可在回收站恢复。$/,m=>`Deleted ${m[1]} trace(s). Restore them from Trash.`],
    [/^浏览器验收独立规范输入 (\S+)$/,m=>`Browser acceptance normalized input ${m[1]}`],
    [/^读取 ([^\r\n]+)$/,m=>`Read ${m[1]}`],
    [/^([^：\n]+)：([\s\S]+)$/,(m,t)=>t(m[1])!==m[1]?`${t(m[1])}: ${t(m[2])}`:m[0]],
    [/^允许：([\s\S]*?)；禁止：([\s\S]*)$/,(m,t)=>`Allowed: ${m[1]}; Forbidden: ${t(m[2])}`],
    [/^(\d+) 条显式授权 \/ 拒绝记录；缺失批准记录仍需核实$/,m=>`${m[1]} explicit approval / denial records; missing approval records require verification`],
    [/^消息 (\d+)$/,m=>`Message ${m[1]}`],
    [/^调用 (\d+)$/,m=>`Call ${m[1]}`],
    [/^(.+) · 调用 (\d+)$/,(m,t)=>`${t(m[1])} · Call ${m[2]}`],
    [/^原生序号 (.+)$/,(m,t)=>`Native sequence ${t(m[1])}`],
    [/^(\d+) 条关联事实$/,m=>`${m[1]} associated facts`],
    [/^(\d+) 条关联事实；引用现有编译结果，没有另造因果关系。$/,m=>`${m[1]} associated facts from the compiled record; no additional causal links are inferred.`],
    [/^原始导入记录 · (\d+) 步$/,m=>`Original import records · ${m[1]} steps`],
    [/^内部(记忆|反思|计划)：([\s\S]+)$/,(m,t)=>`${({记忆:'Internal memory',反思:'Internal reflection',计划:'Internal planning'})[m[1]]}: ${t(m[2])}`],
    [/^(.+)：尚未完成独立评测。$/,(m,t)=>`${t(m[1])}: independent evaluation pending.`],
    [/^(.+)：模型流程结束后开放，不提前显示预设数量与位置。$/,(m,t)=>`${t(m[1])}: available after the model review finishes; expected counts and locations remain hidden.`],
    [/^(\d+) \/ (\d+) 项$/,m=>`${m[1]} / ${m[2]} items`],
    [/^(.+) · 当前阶段 (.+)$/,(m,t)=>`${t(m[1])} · Current stage ${t(m[2])}`],
    [/^队列位置：(\d+)$/,m=>`Queue position: ${m[1]}`],
    [/^(.+) · (\d+) 项$/,(m,t)=>`${t(m[1])} · ${m[2]} items`],
    [/^(\d+) 条轨迹$/,m=>count(m[1],'trace')],
    [/^(\d+) 条$/,m=>m[1]],
    [/^(\d+) \/ (\d+) 条$/,m=>`${m[1]} / ${m[2]}`],
    [/^已选 (\d+) 条$/,m=>`${m[1]} selected`],
    [/^导入 (\d+)$/,m=>`Import ${m[1]}`],
    [/^轨迹 (\d+)$/,m=>`Trace ${m[1]}`],
    [/^证据 (\d+)(.*)$/,m=>`Evidence ${m[1]}${m[2]}`],
    [/^F(\d+) · (.+) · (?:导入步骤|Import step) (\d+)$/,(m,t)=>`F${m[1]} · ${t(m[2])} · Import step ${m[3]}`],
    [/^(\d+) 条结构事实(?: · 模型提取 (\d+) 项)?(.*)$/,(m,t)=>`${count(m[1],'structural fact')}${m[2]?` · ${count(m[2],'extracted item')}`:''}${m[3]?` ${t(m[3].trim())}`:''}`],
    [/^真实解析模型请求 (\d+) 次$/,m=>`${m[1]} model parsing requests`],
    [/^(\d+) 类敏感来源$/,m=>`${m[1]} sensitive source types`],
    [/^(\d+) 条授权记录$/,m=>`${m[1]} authorization records`],
    [/^语义解析 · (.+)$/,(m,t)=>`Semantics · ${t(m[1])}`],
    [/^风险审查 · (.+)$/,(m,t)=>`Risk review · ${t(m[1])}`],
    [/^解析版本 (.+)$/,m=>`Parse version ${m[1]}`],
    [/^完整诊断 · 选中 (\d+) 条$/,m=>`Diagnose ${m[1]} selected traces`],
    [/^选择比较：(.+)$/,(m,t)=>`Select for comparison: ${t(m[1])}`],
    [/^关闭(.+)$/,(m,t)=>`Close ${t(m[1])}`],
    [/^(向左|向右)滚动 (.+)$/,(m,t)=>`Scroll ${m[1]==='向左'?'left':'right'}: ${t(m[2])}`],
    [/^(.+)，左右滚动查看(.+)$/,(m,t)=>`${t(m[1])}: scroll horizontally to view ${t(m[2])}`],
    [/^原始未决检查 · (.+)$/,m=>`Original unresolved check · ${m[1]}`],
    [/^风险行为检查：(\d+) 项确定风险(?: · (\d+) 项待核实)?$/,m=>`Risk checks: ${m[1]} confirmed${m[2]?` · ${m[2]} unverified`:''}`],
    [/^正在载入 (\d+) 条轨迹…$/,m=>`Loading ${m[1]} traces…`],
    [/^已载入 (\d+) 条轨迹，点击事件或状态查看来源。$/,m=>`Loaded ${m[1]} traces`],
    [/^(\d+) 项发现 · 点击下方问题查看证据$/,m=>`${m[1]} findings`],
    [/^模型结论：(.+)$/,(m,t)=>`Model conclusion: ${t(m[1])}`],
    [/^敏感来源：(.+)$/,m=>`Sensitive source: ${m[1]}`],
    [/^尚未解析或无法确认 · (\d+)$/,m=>`Unparsed or unresolved · ${m[1]}`],
    [/^逐片段核对 · (\d+)$/,m=>`Segment review · ${m[1]}`],
    [/^(\d+) 项有证据的风险 · (\d+) 项输入警告 · (\d+) 项疑似 · (\d+) 项检查限制$/,m=>`${count(m[1],'supported risk')} · ${count(m[2],'input warning')} · ${m[3]} suspected · ${count(m[4],'check limitation')}`],
    [/^(\d+) 项有证据的风险 · (\d+) 项输入警告 · (\d+) 项疑似 · (\d+) 项证据缺口(?:。按发现计数，不按关联实体重复计数。)?$/,m=>`${count(m[1],'supported risk')} · ${count(m[2],'input warning')} · ${m[3]} suspected · ${count(m[4],'evidence gap')}`],
    [/^模型已检查 (\d+) \/ (\d+) 项；(\d+) 项尚未检查，(\d+) 项仍不确定。$/,m=>`Model reviewed ${m[1]} / ${m[2]} items; ${m[3]} unreviewed, ${m[4]} uncertain.`],
    [/^语义片段 (\d+) \/ (\d+)$/,m=>`Semantic units ${m[1]} / ${m[2]}`],
    [/^语义 (\d+) 项$/,m=>count(m[1],'semantic item')],
    [/^(\d+) 条事实 · (.+)$/,(m,t)=>`${count(m[1],'fact')} · ${t(m[2])}`],
    [/^✓ 检查通过 (\d+)$/,m=>`✓ ${count(m[1],'check')} passed`],
    [/^([✓◌?！]) (.+)$/,(m,t)=>`${m[1]} ${t(m[2])}`],
    [/^已载入 (\d+) 条 · (\d+) 次调用$/,m=>`${count(m[1],'trace')} · ${count(m[2],'call')}`],
    [/^已载入 (\d+) 条轨迹$/,m=>`Loaded ${count(m[1],'trace')}`],
    [/^(\d+) 项风险(?: · (\d+) 项检查限制)?$/,m=>`${count(m[1],'risk')}${m[2]?` · ${count(m[2],'check limitation')}`:''}`],
    [/^本批生成 (\d+) 条轨迹；(\d+) 项导入问题。原始来源与转换记录由服务保存。$/,m=>`${m[1]} traces created · ${m[2]} import issues. Source records saved.`],
    [/^已保存 (\d+) 条轨迹。关闭此窗口查看执行过程，再核对策略并运行完整风险审查。$/,m=>`${m[1]} traces saved. Close to view the timeline and review.`],
    [/^已读取 (\d+) 个文件。云端记录按 Trace 合并，原文保留；标准轨迹走原有导入流程。$/,m=>`${m[1]} files read. Cloud records grouped by Trace ID.`],
    [/^已提交 (\d+) 条完整诊断；规则及安全结果继续保留。$/,m=>`${m[1]} diagnoses submitted. Existing checks retained.`],
    [/^已提交 (\d+) 条完整诊断，按队列运行。原版规则与安全结果保留。$/,m=>`${m[1]} diagnoses queued. Existing checks retained.`],
    [/^全部载入轨迹共 (\d+) 个唯一问题；按问题计数，不按关联实体重复计数。$/,m=>`${m[1]} unique findings across loaded traces.`],
    [/^(\d+) 项发现 \/ (\d+) 条受影响轨迹$/,m=>`${m[1]} findings / ${m[2]} affected traces`],
    [/^(\d+) 个导入事件$/,m=>count(m[1],'import event')],
    [/^(\d+) 个导入步骤$/,m=>count(m[1],'import step')],
    [/^(\d+) 次调用$/,m=>count(m[1],'call')],
    [/^导入步骤 ([\d,、 ]+)$/,m=>`Import step${/[,、]/.test(m[1])?'s':''} ${m[1]}`],
    [/^(\d+) 项发现 · (\d+) 条受影响 · (\d+) 项检查限制$/,m=>`${count(m[1],'finding')} · ${count(m[2],'affected trace')} · ${count(m[3],'check limitation')}`],
    [/^(\d+) 项发现 · (\d+) 条受影响$/,m=>`${count(m[1],'finding')} · ${count(m[2],'affected trace')}`],
    [/^(全部|有证据的风险|输入警告|疑似|规则问题|安全风险|运行异常|模型判断|检查限制|证据不足) (\d+)$/,(m,t)=>`${t(m[1])} ${m[2]}`],
    [/^任务：([\s\S]+)$/,m=>`Task: ${m[1]}`],
    [/^(.+) · 一次具体调用$/,m=>`${m[1]} · tool call`],
    [/^调用 ([a-zA-Z0-9_.-]+) 工具$/,m=>`Call ${m[1]}`],
    [/^在当前筛选的 (\d+) 条轨迹中随机抽取；不会执行 Agent。$/,m=>`Sample from ${m[1]} filtered traces.`],
    [/^已载入.*? (\d+) 条轨迹，点击事件或状态查看来源。$/,m=>`Loaded ${m[1]} traces`],
    [/^原始序号 (\d+)$/,m=>`Source sequence ${m[1]}`],
    [/^原生序号 (\d+)$/,m=>`Native sequence ${m[1]}`],
    [/^原生 ([\d?]+) \/ 导入 (\d+)$/,m=>`Native ${m[1]} / Import ${m[2]}`],
    [/^(新记录|沿用已知值) · F(\d+)$/,(m,t)=>`${t(m[1])} · F${m[2]}`],
    [/^查看出处事件 (.+)$/,m=>`View source event ${m[1]}`],
    [/^(出处|依据|对应|检查)指针$/,(m,t)=>`${t(m[1])} pointers`],
    [/^没有已记录的(出处|依据|对应|检查)指针，不补造关系。$/,(m,t)=>`No recorded ${t(m[1]).toLowerCase()} pointers.`],
    [/^(起点|终点)：(.+)$/,(m,t)=>`${t(m[1])}: ${m[2]}`],
    [/^(起点|终点)：输入来源$/,(m,t)=>`${t(m[1])}: input source`],
    [/^(\d+) 秒$/,m=>`${m[1]} seconds`],
    [/^(.+) · (\d+)(?: 项| 条| 次)?$/,(m,t)=>t(m[1])!==m[1]?`${t(m[1])} · ${m[2]}`:m[0]],
  ];
})();
