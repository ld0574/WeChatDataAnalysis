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
