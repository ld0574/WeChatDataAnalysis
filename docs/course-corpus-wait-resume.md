# 原覆盖等待到期后的有限接续

2026-11-08 10:45 核对：第二课完整覆盖原作业
`4c7009067249cc972b363551ea0244c8` 在 58/61 批后因共享 GPU 等待到期退出。
原 guard `15712`、worker `7628` 已退出，旧失败、响应和每角色预算全部保留。
`DraftJob.slot` 的锁等待截止点固定在执行初始化后的 21,600 秒，实际退出时
worker 已运行 21,768.633 秒。这不是累计纯排队时长或模型响应失败，
也不能算该批语义审核通过。

## 覆盖的实际接续

独立私有 preflight 实际只读认证 58 批、14,848 分句、下一候选的 6 组和
123 个模型阶段，新增推理为 0。第 58 批提案已有真实缓存，但其独立核验与
第 59、60 批未使用任何尝试。接续沿用原 assignments 入口、角色与预算，
仅在保留旧失败后启动一次新的执行，不刷新模型预算或增加角色。

原作业的新 guard 为 `1420 / 1794105155.4953897`，实际覆盖 worker 为
`25476 / 1794105156.4767072`。启动请求、preflight 及日志位于本机
`output/course-knowledge/wiki/coverage-deadline-resume-20261108/`；
私有驱动为 `wiki/coverage-deadline-preflight-20261108.py`，不进入 Git。
驱动与收据已另行按内容哈希复制到
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/coverage-deadline-resume-20261108`。

10:45 实际为 60/61 批、15,360/15,534 分句，正在最后一批独立核验。
全部处理结束后仍须由原 assignments 入口完整重建；批数不是覆盖通过数或文章验收。

## 两课尚未开始角色的接续

批次 `60ee5378015a2d2f2a221548ba19df87` 原控制器 `6336` 与旧 guard
布置 watcher `31884` 因原覆盖失败退出，0/2 课，尚无 ledger/author 模型调用。
保留其 plan、started、progress 和所有失败；不改写为成功。

新模块 `course_textbook_corpus_wait_resume` 的补充执行绑定准确替换 guard/worker
和私有 preflight 的原字节哈希，等待两者成功退出后调用原 assignments 完整重建，
生成实际 `predecessor-proof.json`，然后才使用原来两个未调用的账本和作者。
前驱暂停、身份变化、失败退出或已开始角色都会拒绝接续；不自动热恢复或循环重试。

新首次作者从第一个原写作角色就使用固定真实段落数和有界 coverage 索引，
全部教学任务仍必填，完整原文、独立模型复核和整条覆盖义务保持。
另存 `bounded-first-execution.json` 绑定格式与批次；作者 guard 位于不可变材料之外，
无需移动已有 guard 或另开符号链接 watcher。

新增模块 SHA256：
`1323977b1916a5eb71d05a09466f135f5f53eaa2bc36f60025cdf8e83e040c2b`。
12 项新测试和原 batch 测试合计 30 项及 Ruff 通过；实际 prepare 仅运行一次。
控制器 `17760 / 1794105564.7409887` 存活，10:45 仍等待前驱；
状态和所有新失败位于原批次 `wait-deadline-continuation/` 子目录。

```powershell
$env:PYTHONPATH='src'
$env:PYTHONUTF8='1'
$env:PYTHONUNBUFFERED='1'
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_corpus_wait_resume `
  --root output/course-knowledge --batch 60ee5378015a2d2f2a221548ba19df87 load
```

完整写作退出后的零推理重建使用该模块 `first-verify`，并传准确 inventory、ledger。
执行中的控制器会自行完成相同重建；不要竞争前驱非阻塞锁，或据孤立状态文件宣称通过。

## 输入恢复边界

独立副本为
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/corpus-wait-inputs-20261108-103813`，
322 文件、37,652,841 字节，manifest SHA256：
`d3349fa52b1ab28d37da39c68fa195efa25759a67b088cba6c4579aca58879a2`。
已再次复制并从独立源码和材料实际执行 load，认证相同两课和补充执行，新增模型调用 0。
有限 SQLite 的 videos/segments/frames 为 2/659/368 行。
在副本 `restored/workspace` 中把 PYTHONPATH 指向其 src，使用主 venv 和上述 load 命令。
不含前驱的完整覆盖、全量数据库、模型、媒体或完整环境；不是结果恢复或质量验收。

## 七图依赖接续

旧七图 dispatcher `23372` 随原前驱失败退出，原七图角色未调用，旧记录保留。
新私有 watcher `wiki/notebook-images-after-wait-resume-20261108.py` 已实际核对输入和未用角色，
身份 `21820 / 1794105745.8391075`，10:45 仍等待准确替换覆盖与两课控制器实际重建证明。
它逐一重核证明依赖的哈希，避免重复抢前驱重建锁；随后使用私有图片 guard，
不覆盖全局 coverage guard，再运行原七图角色及实际 wire/原图收据重建。

状态位于七图原 dispatch 目录的 `coverage-wait-continuation/`。
驱动及六份元数据已按哈希复制到
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/notebook-image-wait-resume-20261108`。
图片模型自审仍使用同一权重，不能称为独立模型像素验证。

本次全部为私有稿和来源核对，完整可阅读文章仍为 1 篇。
课件文字仍有 41 项关系未决，第二课原末节 43/47 的拒绝保留。
全量 340 节音频、2 份无声来源及可恢复课件的 Goal 继续 active。
180W 当前设置、200W 保护上限、70°C 暂停且不自动热恢复、两盘各 32 GiB 保留线不变。

## 11:04 实际完成、依赖接续与独立恢复

原覆盖已完成 61/61 批、15,534 分句，替换 guard 与实际 worker 均成功退出。
两课控制器实际调用原 assignments 入口完成全文重建，认证 129 个模型阶段；
实际证明 SHA256 为 `432e776aedfc31fadb038cd3e1490b88a6d2688f52254b5b9110aafe082d2929`。
10,686 分句得到正文覆盖支持，2,736 分句为经核验非教学，2,112 分句仍未决，
另有 68 份来源分类冲突；0 个确认缺失不等于没有遗漏，未决仍须处理。
原稿 9 项问题继续保留，全文审核执行完成不能作为语义验收或发布凭证。

两课控制器已进入原第一课账本 `5eab10f12c4940c996f952ee2ff0ee2c`，
原实际 worker `7296 / 1794106062.0951996` 正常使用原 group 角色，未重启。
七图实际 worker `9788 / 1794106064.684986` 已完成 2/7，继续原图片审核。

完成结果副本为
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/finished-coverage-20261108-105930`，
1,401 文件、70,929,639 字节，manifest SHA256：
`f0f9feb4f7f36de5c3d22fd222132ef6473e0d006fb6790c888b9ce5d9d284ef`。
从再次复制的独立源码、父稿、原文、原模型请求/响应和有限 registry 实际重建出完全相同证明，
新增模型调用 0。有限 SQLite 为 1/311/192 行，quick_check 通过。
25 份 legacy group proof 的路径与 2 份绝对快照依赖按 manifest 原字节哈希严格一对一重定位，
未修改原契约、模型缓存、正文、审核或旧绝对路径字符串；没有回读工作目录中的旧快照。

最初两个独立目录恢复分别因原快照绝对依赖进入路径集合、映射适配器另行加入绝对快照而失败，
原副本 `finished-coverage-20261108-105048`、`finished-coverage-20261108-105401`
和诊断继续保留，不把它们计为成功恢复。
私有恢复脚本仅在独立进程中转录运行时依赖位置，不改运行中的冻结模块。
本副本不含模型、媒体、全量数据库或完整处理环境。

在成功副本 `restored/workspace` 内设置 PYTHONPATH 为其 `src`，使用主 venv 执行：

```powershell
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/finished-coverage-restore-20261108.py `
  output/course-knowledge 'D:\Project\WeChatDataAnalysis\output\course-knowledge' `
  c7258efff888d77a26e1234dd153ef7a `
  output/course-knowledge/wiki/whole-lesson-narrative-20261104/local-model-tokenizer.json `
  wiki/full-course-batches/60ee5378015a2d2f2a221548ba19df87/wait-deadline-continuation/predecessor-proof.json
```

这是只读全文重建，不获取 GPU，不恢复或重跑任何模型角色。

11 月 10 日实际终态已另行核对：首份账本 20/20 组结束，首个作者在 1/14 单元后因
`section-0001-write` 的两次原结构失败而停止；第二份课程尚未开始。
不能继续将此前 11:04 的健康状态用于当前进度。详见
[11 月 10 日续跑记录](course-wiki-progress-20261110.md)。
