# 未用最终编辑角色的逐任务来源映射

`course_textbook_task_mapped_editor` 仅用于尚未登记最终编辑格式、尚未调用的父稿单元。
原父稿、负面审核、全部原文及预算不变；一次编辑稿和一次不同权重模型的独立审核，
无结构或语义自动重试，不发布文章、不删除视频。

前一次 `section-0010` 的实际写作只关联了 30 个任务中的 15 个，原失败保持终态。
本入口不能用于它或已耗尽的 `section-0013`，也不重新准备这些单元来刷新额度。

## 首次解码的约束

正文先声明实际 2–12 段，解码分支约束完全相同的段数。
原 `coverage` 仍必须包含每个指定任务；每项的 `paragraph_sources` 由模型明确选择
实际段落编号及真正支持该任务讲解的主要原文。编号仅允许落在实际段落范围内，
引用只能从完整可见的该任务主要原文中选，禁止空引用和重复引用。
确认教学任务必须有映射；待审任务判断为非教学时必须为空，判断为教学时必须非空。

编译只把模型实际选择的关系投影为原段落引用和覆盖索引；不增加引用、不猜测知识归属，
不改任何正文。空段、重复段和没有真实任务来源映射的正文会拒绝。
这些是结构约束，不是语义证明；独立审核仍逐点对照完整原文和实际正文。
任务多不能成为删减原文或任务的理由。

请求、实际解码 schema、返回、GPU 回执和机械派生均保持可重放。
只读 verify 必须等待真实 worker 退出；缺缓存或任何绑定变化均拒绝，不允许调用模型或申请 GPU。
原父稿的问题继续保留，即使单元通过仍须整课覆盖、课件、图文、整合、自测和实际阅读验收。

## 真实准备和输入恢复

父稿 `c7258efff888d77a26e1234dd153ef7a` 的 `section-0012` 首次准备为
`d632f90a28b71b263f8bb4ee4d54c078`：48 个确认任务，38 份主要原文和 18 份相邻原文。
完整实际写作输入 26,074 tokens，输出预留 12,288，context 49,152；没有裁剪。
prepare 模型调用 0，原 9 项父稿问题保持。

准备后源码已冻结，SHA256：

`ed5a3da5850ec73ceafe66fa75db1193e3e3ca761f8e8ef50763f94a142b9dc7`

54 项相关合成测试和 Ruff 通过，包括真实请求格式、独立完整审核、遗漏/越界/无据引用拒绝、
只读重放、CPU 回退拒绝、格式与预算不可刷新、负面结果和原件保持。
测试不能代替这次真实模型结果。

独立输入副本：

`C:/Users/Administrator/Documents/CourseKnowledgeBackups/task-mapped-editor-inputs-20261110-055602`

603 文件、33,850,331 字节，manifest SHA256：

`5c4f6c82e32a0f4ad7e85e8095d470ad2887e89f25fc7d7903bbf59ac0ca88c0`

已由再次复制的源码实际 load，相同 48 个任务和完整原文，全部原角色未用；
原归档读取 0、模型调用 0。真实同父有限 registry 为 1 课程/311 段/192 帧，quick_check 通过，
不是空占位库、全量媒体、模型或完整环境恢复。

在副本 `restored/workspace` 内，把 PYTHONPATH 设为该副本的 src，
使用主虚拟环境执行副本内的 `backup_task_mapped_editor_inputs_20261110.py verify`，
依次传入 `output/course-knowledge`、原归档绝对路径、作业 ID、该副本 manifest 路径。
只恢复输入时不能改为运行 worker 或 guard。

05:57 已首次隐藏派发。实际 guard 为 `32336 / 1794261440.1369274`，
实际 worker 为 `35616 / 1794261442.133756`；05:59 核对存活并处于首次写作。
执行和私有记录位于：

`output/course-knowledge/wiki/final-editorial-section-jobs/d632f90a28b71b263f8bb4ee4d54c078/`

180W 设置、200W/70°C 保护、不自动热恢复和双盘各留 32 GiB 均保持。
旧失败、旧注册和新控制标记不清除；公开成品仍为 1 篇，全量 Goal 范围不变。

## 06:01 实际失败及独立终态恢复

真实 guard 和 worker 均已退出，guard 返回码 1。唯一写作返回是可解析 JSON：
实际 10 段，48 个指定任务键全部存在，但只有 36 个非空映射，其余 12 个确认任务映射为空。
实际段数分支恰好有 12 项 `minProperties` 违规，没有把空映射补成通过或生成成功缓存。
写作额度已用 1 次，独立审核调用 0；原 section-0012 登记和失败保持，不重新 prepare 或写作。

请求、响应、实际解码 schema 和 GPU 回执已重新认证。实际输入 26,090 tokens、输出 6,692，
生成时间 123.968 秒，context 49,152，显存驻留 7,128,869,764 字节。
这是一份被拒绝候选的服务指标，不是交付速度或语义通过。
实际字段顺序保留 paragraph_count 在前、coverage 在后，未观察到本次被按字母顺序重排。
Python 54 项测试不能证明服务端落实了每个 JSON Schema 关键字；
目前只证明本次实际响应违反非空约束，尚未直接证明本机转换器为何放过该规则。
后续未用编辑应先做实际规则兼容核验，不能继续靠换格式消耗课程额度。

终态副本：

`C:/Users/Administrator/Documents/CourseKnowledgeBackups/task-mapped-editor-terminal-20261110-060644`

615 文件、33,913,921 字节，manifest SHA256：

`2f6e74c3750f2cd0e84d9c42c9988b1ff34d8202b5111283b8c9f507e6f954bf`

已从再次复制的源码、完整输入、失败、请求/响应及真实有限 registry 实际重建相同 12 项违规；
原归档读取 0、新模型调用 0。它不是成功稿或完整环境副本。
在副本 `restored/workspace` 设好本地 src，执行其中
`backup_task_mapped_editor_failure_20261110.py verify`，依次传入归档根、原归档绝对路径、manifest。
私有 `terminal-coverage-diagnosis.json` 和 `independent-terminal-restore.json` 保留同一结论。
