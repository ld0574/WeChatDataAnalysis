# 原三课的守护退出修正

原批次 `a4eb4f64f5606c90f80424ce29238406` 的第一门课程已生成完整私有稿
`42b874e8debbb2216fc0964740bbdb5e`，4/4 单元，实际 writer 退出、guard 返回 0。
原 controller `14272 / 1794272083.6037016` 同时充当该 writer 的 guard；
它在校验时要求 guard 已退出，因此把自己的存活误认为前驱未退出，停止在原进度 0/3。
完整稿退出后由单独进程执行原 verify-writer，真实重建 4/4，仍有 3 项未决意见、模型调用 0。
不能把这个调度错误称为模型写作失败，也不能把完整私有稿称为质量通过或公开文章。

新入口 `wechat_decrypt_tool.course_textbook_ready_guard_exit` 保留原控制器及其两份失败、
完整第一份稿、原账本和全部已用额度；仅派发同一原批次第 1、2 项尚未运行的角色。
这些项的原账本调度组数为 22、24，不是读者章节数。
原首次计划、段落格式、编号图格式、每阶段结构尝试和每单元语义修订限制均保持。
第一门课程不再调用任何模型角色，原三课没有重新选择或另立预算。

每个后续 ledger/writer guard 使用独立 Python 子进程，父 controller 实际 join 后才校验。
成功返回必须同时满足正确 PID 与创建时间、guard 成功退出、实际 worker 成功退出和完整只读重建。
失败、暂停、替换 PID 或未退出均不能释放校验；同一 guard 不自动再启动。
原成功稿仍要覆盖检查、课件关联、图文、自测及阅读验收，全量范围和公开文章计数不变。

16 项新测试和 24 项原接续测试通过，Ruff 通过。
实际准备时先后发现原通用 unused 检查会错误包含已完成第一项、旧 preflight 字段名不符；
两次准备都没有注册执行或使用模型角色。原源码快照和失败保留于
`wiki/ready-guard-exit-preparation-history/a4eb4f64f5606c90f80424ce29238406`。
修正后的检查只针对实际剩余两项，已完成第一项的真实记录继续绑定与保留。
本模块在成功 prepare 后冻结；启动前必须真实独立恢复完整第一稿和剩余原输入。

私有执行目录：

`D:\Project\WeChatDataAnalysis\output\course-knowledge\wiki\ready-guard-exit-continuations\a4eb4f64f5606c90f80424ce29238406`

使用共享 GPU 锁、180W 设置、200W/70°C 保护、双盘各 32 GiB 保留线，不自动热恢复。
不重启健康来源任务，不清除旧暂停、失败、预算和标记；原视频继续保留。

## 实际独立恢复与首次启动

输入与第一份完整私有稿的独立副本：

`C:\Users\Administrator\Documents\CourseKnowledgeBackups\ready-guard-exit-inputs-20261110-100053`

775 文件、42,788,568 字节，manifest SHA256：

`e0942dc2f1b52d5a2c5d9c9a458455331930926f803546383d526912d83aefc2`

复制源码实际 load 新执行并完整重建原第一稿，同一 4/4 单元、3 项未决意见，proof SHA256：

`316ce1b05070b42bfb397471b1ddcde2a432b1bedbfdb003cfb67b627536ce84`

真实 5 份旧 group 记录中的绝对路径，仅按原相对位置及字节哈希在读取时重定位；
旧文件字节、正文、判断和额度没有改写。后两课共 1,144 份原始记录，全部原角色仍未用。
原归档读取和新增模型调用均 0，网络、SQLite 连接和推理子进程均被禁止。
离线运行时的模型元数据只取自已认证 manifest，不声明模型或运行环境已恢复。
保留原输入副本内真实有限 registry 的字节，没有在本副本重放其完整前驱或校验数据库行。
不包含未来后两课结果、源媒体、模型、全量数据库或完整环境。

在副本 `restored/workspace` 内执行：

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/backup_ready_guard_exit_inputs_20261110.py verify `
  output/course-knowledge D:\Project\WeChatDataAnalysis\output\course-knowledge `
  a4eb4f64f5606c90f80424ce29238406 ..\..\manifest.json
```

10:02 在成功恢复后首次隐藏启动 launcher `15516`，实际 controller、独立 guard 和 worker
必须另核对，不把派发计为新增完整稿。真实后续结果仍须分别重建并备份恢复。

实际 controller `34556 / 1794276171.20596` 已启动原第二门账本，guard
`26548 / 1794276175.2320533`、worker `22352 / 1794276177.8860729` 存活。
Popen 绑定的是 venv launcher `16476`，而非实际 guard；本冻结执行的退出身份校验因此仍有问题。
[实际 Windows guard 身份接续](course-ready-wrapper-continuation.md)保留当前健康账本，
待此执行真实记录预期身份错误后才继续原未用角色，不能重新运行本入口。
