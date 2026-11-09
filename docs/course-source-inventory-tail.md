# 全量目录中未尝试来源的有限接续队列

全量范围仍是 340 节音频、2 份无声来源及全部可恢复课件。
11 月 10 日的目录快照只发布了 56/340 份内部来源笔记，不是 56 篇完整读者文章。
直接核对注册库与真实文件后，340 节音频的实际分区为：

| 分类 | 数量 | 处理 |
| --- | ---: | --- |
| 已注册，包括失败、未完成及当前健康任务 | 63 | 不作为新来源重跑，继续保留原状态与预算 |
| 原来源计划已占用、尚未注册 | 1 | 等待原健康尾队列完成 |
| 已有额外输出，需要单独恢复 | 3 | 明确保留例外，不覆盖残留、不新建重复处理 |
| 未尝试且无其他计划占用 | 273 | 纳入新有限队列 |

目录快照中有 278 项 `unregistered_lesson`，但目录在当前抽取期间没有刷新；
不能把旧目录代码当作实时注册库数量。上表以实际注册 ID、原计划占用和磁盘状态为准。
2 份无声来源继续保留原流程，不能算作音频队列完成。

`course_source_inventory_tail` 在当前健康尾队列结束后处理全部冻结的 273 个新来源。
每份都先核对仍未注册、来源文件 size/mtime、既有音频输入和完整原计划；
沿用本机原 ASR/OCR/笔记流水线、Qwen ASR float32 / batch size 4 和现有知识模型。
每份使用独立私有 guard，真实成功退出、完整导出验证、内部来源目录与 HTTP 核对后才计入完成。
任意失败、暂停、温控或空间保留触发即停止，不自动重试、恢复或重新分配已有预算。
不删除视频，不发布 Wiki 文章，不把未完成来源排除出 Goal。

等待对象是原来源尾控制器 `27400 / 1794240557.613498`，而不是可复用的裸 PID。
必须等它真实退出，原两份完成清单、每份导出、目录成员和私有 guard 成功回执全部相符，
才会获取原 `finite-source-batches.lock`。当前健康抽取不被重启或抢占。
等待期间也保留所有新控制标记；70°C 暂停且不自动恢复、200W 保护、180W 设置及 C/D 各 32 GiB 不变。

## 已验证的实际准备与派发

31 项新入口及来源回归、Ruff 已通过，包括完整分区、原计划占用、失败和残留排除、
真实来源 stat、准确前驱退出、PID 复用/失败/暂停/不完整回执拒绝、逐来源校验发布、
一次性执行和新暂停记录保留。首次合成夹具缺守护目录已修正，未影响真实任务或消耗模型额度。

02:13 首次实际 prepare 成功，新增推理 0；计划为
`77c3d6065a1e87a1e3eac5835e62b189`，273 个新来源、67 个显式保留项。
模块在准备后冻结，SHA256：
`aba94564e14c60326f647295eadef6e66fc036d9f2a1ade1a7f8f3eef485bb1f`。

执行元数据副本及实际独立核验：
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/inventory-source-tail-metadata-20261110-021515`。
309 文件、8,119,511 字节，manifest SHA256：
`fdce85ddcef04b5254319f974c38b7d9d36ff1994c3953ff7104bae1b15aa444`。
副本含独立源码、完整目录/契约、原占用计划、65 个实际注册 ID 的有限 SQL 投影。
从再次复制的副本实际 load，核对全部 340 个成员的唯一分区、无注册/占用冲突及原 273 份媒体 stat；
禁止原根目录 JSON 回退，新增推理 0。
这是代码和执行元数据核验：原媒体、音频和模型字节仍依赖现有绝对路径，
没有恢复完整输入、全量数据库、未来结果或完整环境，不能宣称完整资料已可移植恢复。

02:15:54 首次派发，venv launcher 为 `13200 / 1794248154.719188`；
实际 controller `12264 / 1794248154.7592847` 已核对存活，状态
`waiting_for_exact_existing_source_tail`，完成新来源为 0/273。
状态目录：`output/course-knowledge/wiki/inventory-source-tails/77c3d6065a1e87a1e3eac5835e62b189/`。

副本 `restored/workspace` 内将 PYTHONPATH 指向其 src，可只读复现元数据核验：

```powershell
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/inventory-tail-metadata-restore-20261110.py `
  output/course-knowledge 'D:\Project\WeChatDataAnalysis\output\course-knowledge' `
  77c3d6065a1e87a1e3eac5835e62b189
```

队列运行后的逐份结果副本在 `Documents/CourseKnowledgeBackups/inventory-source-tail-results/`。
3 份残留、已注册失败/缺页及后续文章/覆盖/课件/阅读验收继续是全量未完成工作。

03:37 只读细化了上述例外，没有变更原队列或新建抽取控制器：
3 个未注册来源中，1 份完整 AAC 与原修复留件已重新认证且输入副本独立解码通过，
另外 2 份缺口音频的独立输入副本和全部 561 个不跨缺口窗口已核验。
已注册来源中另有 4 个 pending：原替代音轨、损坏原包和已有转写绑定均重新校验，
但均没有已提交转写、OCR 或笔记，不能按已完成处理。
七份特殊来源的准确范围与证据边界见 [音频输入核验](course-partial-audio-inputs.md)。
原健康来源此时 OCR 已完成 179/183 帧，还未完成笔记/完整导出和后继来源；
新 273 份队列仍在等待准确前驱，不把等待或预检计入来源完成。
