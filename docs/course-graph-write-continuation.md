# 原图连线故障的有限接续

作者 `f8bc01ff9ffde2fd4e584e9f1ffe5912` 已认证完成 8/16 个内部写作单元。
随后 `section-0008-write` 两次原调用均耗尽，worker 与 guard 真实退出，guard 返回 1。
依赖它的三课接续 controller 也已停止于 0/3；不能将旧 controller 重启或改称成功。

实际原响应均完整 stop，9 段正文、28 个覆盖键，实际解码 schema 校验无错误。
只读重放准确复现 `textbook_diagram_unknown_or_unconnected_node`：
第一次图为 9 节点/9 边，1 个未知端点、1 个未连接节点；
第二次图为 7 节点/7 边，1 个未知端点、0 个未连接节点。
不能凭段数或 JSON 合法把失败候选当作合格文章。

新增入口 `course_textbook_graph_write_resume` 只使用原来尚未调用的审查、一次修复、
修复后审查，以及后续原未用单元和全文阅读角色。真实 prepare 已认证以下条件：

- 重放前 8 个单元与原概念规划，保留 1..N 规划恢复及后续实际组数语法。
- 对照原消息、实际 schema、输入绑定、模型 digest、GPU 回执、完整 stop 与失败记录。
- 保留两次原响应字节，选择最后一次作为明确不可信候选；只去除段数标签供原审查读取。
- 仅测量图缺陷，不改节点、边、说明文字或教学判断，不删除坏图以凑合验收。
- 原初审即使误判通过，也必须运行原未用修复；独立终审及所有负面意见保留。
- 编排阶段仅通过单独回执引用真实 repair/repair-review，不创建失败 write 的 accepted 缓存。
- 后续原未用角色仍通过原 recovered-writer 工厂；不增加角色或刷新任一原预算。

私有执行目录：
`output/course-knowledge/wiki/graph-write-continuations/f8bc01ff9ffde2fd4e584e9f1ffe5912/`。
准备时已冻结本模块和依赖，运行中不能修改。guard 子目录需在首次 run 前建立；
已有独立 guard 和共享 GPU/全课程锁控制，70°C/200W、磁盘预留及不自动热恢复保持。
已完成 108 项相关合成测试和 Ruff，测试结果不能代替真实修复、全文覆盖或文章验收。

成功退出后必须使用本入口的 `verify` 从真实原调用重建完整稿件，不能使用旧 one-based
verifier 代替新增修复回执。完整私有稿仍须全文覆盖、课件、图文、自测、编辑与真实阅读验收。
公开成品仍只有 1 篇；本接续、内部单元和输入备份都不计为新增成品。

## 独立输入恢复与实际派发

首份副本 `graph-writer-inputs-20261110-071059` 的严格离线验证被旧构造器的本机
模型摘要查询拦下。失败副本及私有 `input-backup-failure-*.json` 保留，未因此调用模型或刷新预算。
新的恢复驱动只从逐文件校验后的原 authoring manifest 读取模型摘要，不能据此验证已安装权重；
运行中的生产代码没有修改。恢复过程禁止网络和推理。

成功副本：
`C:\Users\Administrator\Documents\CourseKnowledgeBackups\graph-writer-inputs-20261110-071512`。
1,047 文件、47,314,960 字节，manifest SHA256：
`b6dda72465d7200ce44bae1a557d9de953b0c8ce9c66a5a147e4b0ed2b6e6a57`。
复制源码真实重建同一 8/16 前缀、1 个原负面问题、两次写作失败及选中的不可信图，
44 个原 group proof 仅按原字节哈希一对一重定位。
真实有限数据库为 2 来源/659 段/368 帧，quick_check 通过；原归档读取和新增推理均为 0。
副本不包含完整视频、模型、全量数据库或完整运行环境。

在副本 `restored/workspace` 将 PYTHONPATH 指向其中 `src`：

```powershell
python output/course-knowledge/wiki/backup_graph_writer_offline_inputs_20261110.py verify output/course-knowledge D:\Project\WeChatDataAnalysis\output\course-knowledge f8bc01ff9ffde2fd4e584e9f1ffe5912 ..\..\manifest.json
```

07:18 已首次派发独立 guard `23756 / 1794266308.476975`，guard 启动 PID 34420，
实际 Python worker 为 `16476 / 1794266309.6628845`。
这次单次运行只使用原未用角色；旧失败作者和依赖三课 controller 保留原失败记录。
派发和成功恢复输入都不是实际修复或完整文章完成的证明。

原未用初审已真实完整返回，27,838 提示/3,353 输出 tokens，42 个判断均为通过。
图机器校验依旧失败，不能把这些模型判断当作结构通过；原强制修复仍必须运行。
初审原响应与上述实测缺陷同时保留，当前仍不允许发布读者文章。

随后原修复角色第一次结构尝试仍因未知端点失败，记录未删除；第二次原尝试通过实际校验。
实际返回 2 段、28 个非空任务映射，图为 3 节点/2 边，没有未知端点、未连接节点或重复节点。
该响应为完整 stop，31,068 提示/2,954 输出 tokens，生成 55.464 秒。
这张新图由原模型修复角色生成，适配器没有增删节点或猜连线。

原独立终审 35 项均通过，真实 `original-role-result.json` 已保存并绑定 172 份原角色证据。
本次共消耗原未用初审 1 次、原修复 2 次结构尝试、原终审 1 次，未新增角色或刷新旧预算。
07:34 作者已实际推进到 9/16，在 `section-0009-write` 等待 GPU，guard 仍健康。
完整 16 单元、全文阅读、覆盖与编辑尚未结束；这些局部通过不能当作全课验收或新增公开文章。
