# 原规划编号故障的有限接续

原作者 `f8bc01ff9ffde2fd4e584e9f1ffe5912` 的原账本已完成 24/24 组。
首次零调用锁故障接续后，概念规划 `concepts-00-0001` 的两次真实响应均因 IndexError 失败。
原分组 schema 将编号限制在允许的最大组数内，没有将它限制在实际返回的数组长度内。
失败和原角色预算保持；不能重问这一步，也不能把失败响应写成 accepted 模型缓存。

新增入口 `wechat_decrypt_tool.course_textbook_one_based_writer`：

- 校验原消息、schema、模型摘要、两次失败、实际响应与 GPU 指标，选择最早完整原响应。
- 仅允许完整的 1..N 编号转换为 0..N−1，沿用已有稳定同序规则；不猜缺失组，不移动语义分组，不改教学文字。
- 真实选中原第一次响应的 5 组、141 个完整任务，新增模型调用 0。原第二次响应也留存。
- 后续从未调用的规划角色首次使用实际 group_count 分支，数组长度与编号范围一致。
- 原分段写作、不同权重复核、一次语义修订和整篇阅读角色保持；全部原始负面判断仍须处理。
- 私有稿完成以后使用本入口只读重建。原 fresh/unused-writer verifier 不知道新的独立编号回执，不适合作为本次接续的验收入口。

原失败的两课 controller、unused-writer guard，以及等待它的三课 controller，均保留失败状态。
本入口是单次显式新执行，不能重新运行已经开始的 worker 或 guard，也不能据此宣称三课队列已恢复。

## 本次登记与恢复

私有目录：
`output/course-knowledge/wiki/one-based-writer-continuations/f8bc01ff9ffde2fd4e584e9f1ffe5912/`。
执行合同、recovery、原始字节哈希、原失败状态、独立恢复回执和新 guard 均在此目录。
新代码已在真实 prepare 时冻结，运行中不得修改绑定模块。

独立输入副本：
`C:\Users\Administrator\Documents\CourseKnowledgeBackups\one-based-writer-inputs-20261110-051208`。
共 909 文件、45,978,918 字节；manifest SHA256：
`bafb9102223c4ee7e88723710262e1b9a1eef0cef0887c94643cc0f81cd40a14`。

恢复进程使用再次复制的源码，逐文件校验全部字节，禁止访问原归档、推理和获取 GPU。
44 个旧账本证明中的绝对角色路径仅在只读视图中一对一迁移，仍验证原始角色字节哈希，未改写证明。
真实有限注册表为 2 课程、659 段、368 帧，来自此前已恢复的原父任务输入；不是空数据库占位。
不复制运行时锁句柄，恢复程序建立自身锁；旧失败副本和失败回执保留。
没有恢复视频、模型、全量数据库或完整 Python 环境。

恢复命令（将当前目录设为该副本的 `restored/workspace`，将 PYTHONPATH 设为其中 `src`）：

```powershell
python output/course-knowledge/wiki/backup_one_based_writer_inputs_20261110.py verify output/course-knowledge D:\Project\WeChatDataAnalysis\output\course-knowledge f8bc01ff9ffde2fd4e584e9f1ffe5912 ..\..\manifest.json
```

原 worker 真实成功退出后，才能执行以下只读结果重建；它不调用模型：

```powershell
python -m wechat_decrypt_tool.course_textbook_one_based_writer --root output/course-knowledge --writer f8bc01ff9ffde2fd4e584e9f1ffe5912 verify
```

这仍是内部稿件步骤。全文覆盖、课件关联、编辑、图文、自测和实际阅读验收没有被省略。

随后在第 9 个内部单元 `section-0008-write` 出现真实图端点故障，作者停在 8/16，
原 guard 返回 1，依赖三课接续也停止。编号恢复本身仍已重放认证，不能宣称完整作者成功。
另见 [仅用原未用审查与修复的明确接续](course-graph-write-continuation.md)。
