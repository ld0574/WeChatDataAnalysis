# 原两课批次的未开始尾项

## 原因与范围

原批次 `60ee5378015a2d2f2a221548ba19df87` 的控制器已因首个作者失败退出。
首稿现由 [映射审核接续](course-corpus-index-review.md) 继续，但这个作者不会自行启动批次的下一课。
新 `course_textbook_corpus_unused_tail` 只接续原第二项：

- 原完整清单：`8cc9d7fa6adb0097321555eddc1cb9af09dff64fd2bb5227fd889bfb8641ef18`。
- 原账本预检作业：`50c05f8919768b2f2ea9afccbbaf8fa3`。
- 原第二项尚无 progress、ledger、group proof、写作契约、模型尝试或 started 标记。

它不重新启动首稿、首个账本、旧控制器或任何已完成批次。
340 节音频、2 份无声来源和全部可恢复课件仍是最终范围；该一项是原有限队列的接续，不是缩小目标。

## 成功退出和真实材料约束

prepare 认证原批次完整来源、原两项预检、原失败控制器、首稿原初审/失败修订及真实最终审核，
绑定准确活跃 guard `34112 / 1794244301.568302` 和实际作者 `1604 / 1794244353.7139208`。
等待阶段每次核对 PID 和创建时间、原 guard 身份及暂停/停止标记。
状态文件写成功但进程还活着不能接续；失败、热暂停或 guard 身份更换都会停止，不自动重启。

只有两者真实成功退出、首稿全部 14 单元从真实响应和解码收据零推理重建后，
才保存新首稿 proof 并使用原第二项尚未调用的 ledger-worker。
首稿完整内部稿的负面问题全部保留，不能把私人稿件完成当作覆盖或读者验收。
第二项账本成功退出且原完整输入再次校验后，才首次 prepare 原作者。

第二项作者从第一个原写作角色就使用已经实测过的段数标签分支，
2–12 段的精确长度与覆盖索引上限绑定，完整原文、全部教学任务及独立审核保持。
不使用导致填充的固定十二段格式，不刷新原 recipe、模型尝试或语义修订预算。
写作和修订仍各最多两次结构尝试，每单元仍至多一轮原语义修订。
缺完整原文、失败、暂停和实际缓存不一致都不能自动降级或跳过。

所有新状态位于原批次 `wait-deadline-continuation/original-unused-second-course/`，
各阶段使用独立的私有守护目录；不覆写原 controller/progress/failure。
controller 只运行一次，存在 started 后禁止再次 run。
完整第二稿结束后还要实际 verify-writer，不发布读者文章或删除视频。

## 验证与独立输入恢复

71 项新入口及相关测试与 Ruff 通过，包括首项活跃时不接续、真实成功退出、
失败/热暂停/PID 复用拒绝、部分稿不得启动下一课、原第二项所有角色未用、
真实首写段数 schema/完整 packet、只读推理禁用、负面意见保留和原 item=1 调度。
首次合成测试中目录与契约夹具不完整已修正；真实任务没有因此启动或消费模型预算。

实际首次 prepare 已完成，新增推理 0；新模块已冻结，SHA256：
`8c020be7dce92173ffb0a1b3af522a511b06d157d25355a228b98b602b512413`。
独立输入副本为
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/corpus-unused-tail-inputs-20261110-013216`，
615 文件、41,849,173 字节，manifest SHA256：
`6b201ed176f0303936a9c50e030b6eec39d1fa48f811958be5e04ffe1ec93c73`。

从再次复制的代码、完整有限原文、原真实审核与角色响应和有限 SQL 实际执行 load/unused，
确认同一两课、同一未用第二账本，20 份旧 group proof 按原字节哈希一对一重定位，新增推理 0。
该实际恢复也重建并认证了首稿的原最终审核，而不只是解析其 JSON。
副本不包含未来完整两稿结果、模型、视频、全量数据库或完整环境。

在副本 `restored/workspace` 内令 PYTHONPATH 指向其 src，用主 venv 执行：

```powershell
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/corpus-unused-tail-restore-20261110.py `
  output/course-knowledge 'D:\Project\WeChatDataAnalysis\output\course-knowledge' `
  60ee5378015a2d2f2a221548ba19df87
```

01:34:35 首次派发 run，启动器 PID 31020，实际 controller 为
`6412 / 1794245675.1780086`。已核对真实进程和 `waiting_for_exact_recovered_first_writer`；
派发和等待都不代表第二项开始推理或文章已经完成。
180W 设置、200W 保护上限、70°C 暂停且不自动热恢复、C/D 各 32 GiB 保留线保持。
