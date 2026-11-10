# 原补充编辑剩余批次接续

原作业 `87e51e9142e9ab14315873679be8dd58` 的
[元数据接续](course-corpus-gap-metadata.md) 实际完成 10/24 批，随后在
`batch-0010-edit` 停止。该批唯一实际编辑为完整 stop，但一个新增段落仅 53 字符，
未达到已绑定 schema 的 80 字符下限。原编辑额度已花，该批 15 个目标继续未决；
不补写、不放宽 schema、不接受失败缓存，也不调用其尚未使用的审核。

这次新入口 `wechat_decrypt_tool.course_textbook_corpus_gap_remaining` 只接续原来独立、
未尝试的批次 0011–0023。原固定范围仍为 24 批、128 份来源、2,465 个目标子句，
包含全部 1,043 个原未决项和 93 份来源分类冲突，没有删去失败批次的义务。

## 启动前绑定的有限规则

- 零推理重放前 10 个已审核批次的 20 个原模型角色，保留原结果、负面审核和缺陷。
- 完整认证第 0010 批实际失败、请求、schema、响应及已花单次编辑预算；整批保留未决。
- 后面 13 批使用同一原编辑请求、模型、schema 和各一次编辑/审核额度，不刷新预算。
- 后续实际编辑仅有精确重复引用或未映射段落时，可以沿用既有无损元数据推导，
  由该批原未用审核检查不可信候选。正文和语义判断不由程序修写。
- 其他完整、已认证的结构无效编辑，整批保留未决，不调用该批审核；
  完整、已认证的无效审核也整批保留未决。均无自动重试。
- 传输、温控、输入变化或认证错误停止，不能当作可跳过的语义问题。
- 来源分类冲突只有在全部相关批次都经过审核并明确解除时才可能消除；
  缺少结果或保留未决的批次不能解除冲突。

`reviewed_batches` 与 `retained_unresolved_batches` 分开统计。
`accounted_original_batches` 包含二者，不能称为全部通过。
10/24 的旧进度、原编辑器、元数据接续、失败响应和全部预算保持原字节。
新状态写入：

`D:\Project\WeChatDataAnalysis\output\course-knowledge\wiki\corpus-gap-remaining-batches\87e51e9142e9ab14315873679be8dd58`

原角色的调用缓存和 attempt 仍写回原编辑目录，保证没有另一套可重复调用的额度。
新入口 prepare 成功后及运行中均冻结，不修改其绑定源码。
8 项新测试、18 项元数据接续测试及 27 项原编辑测试通过，Ruff 通过；
这些合成检查不代表课程内容通过验收。

## 实际独立输入与前缀恢复

副本：

`C:\Users\Administrator\Documents\CourseKnowledgeBackups\corpus-gap-remaining-inputs-20261110-103241`

1,385 文件、73,063,849 字节，manifest SHA256：

`8ab7b8bd96d6ef7ea6af446b05c4080e3e1c491d9c269f7f3c861511dbf8c5e8`

复制的源码实际执行新 load 和 inspect：零推理重建前 10 批、认证 20 个原模型角色、
认证第 0010 批的单次实际失败，并核对后面角色都未使用。
实际重建发现前缀合计有 **24 个未映射新增段落**，不只是最初第一批的 3 个。
未映射缺陷保留，不推测其对应目标或当作覆盖补齐。

前缀结果 SHA256：
`ec5c4b0e6ca5ecfcb25df7473275530ba5437e5aa9608a885f7e35aced70e049`

前缀各批结果 SHA256：
`e98f54bf3712f4ffea1decea69fa403a55ddb0e1cf12fd444dcbbe9e271decf0`

原归档读取 0、模型调用 0；恢复驱动禁止网络、SQLite、推理子进程和原根目录 fallback。
副本包含父稿、完整 55 批覆盖的原字节和此前结果恢复回执，未在本副本重新重放父稿及
55 批覆盖。它不含媒体、模型、全量数据库、完整运行环境或未来结果。

在副本 `restored/workspace` 中执行：

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/backup_corpus_gap_remaining_inputs_20261110.py verify `
  output/course-knowledge D:\Project\WeChatDataAnalysis\output\course-knowledge `
  87e51e9142e9ab14315873679be8dd58 ..\..\manifest.json
```

## 实际派发及待完成工作

10:34:21 首次隐藏派发 launcher `7204`。实际 guard
`17412 / 1794278061.9630704`、worker `8304 / 1794278067.2587314` 已核对存活。
10:39 左右正在 `batch-0011-review`，此前的 `completed_batches: 10` 为继承的
已审核前缀，不是这次新完成 10 批；0010 批仍保留未决。

只有实际 worker 和 guard 成功退出后，才能执行：

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
& .\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_corpus_gap_remaining `
  --root output/course-knowledge --job 87e51e9142e9ab14315873679be8dd58 verify
```

该 verifier 只读重放原前缀、新审核及全部保留未决批次，认证实际失败，禁止缺缓存时调用模型。
终态还需另做真实结果的独立备份恢复，不能用这次输入副本代替。

11:09 已实际启动[有限 CPU 收尾](course-gap-terminal-finalization.md)，
等待上述准确原 worker/guard 退出，之后才执行完整结果恢复与私有整合、整合恢复。
该控制器不占 GPU，不重启原任务，不新增角色或自动重试；派发不计为结果恢复通过。

即使原剩余角色全部结束，也只是私有补充候选；仍需处理未映射段落、整合全文、完整覆盖、
事实审核、课件、解释图、最多 5 道选择自测及实际阅读验收。公开文章仍为 1 篇，
原视频保留，全量 340 节音频、2 份无声来源及可恢复课件的 Goal 继续 active。
共享 GPU 锁、180W 设置、200W/70°C 保护、不自动热恢复及 C/D 各 32 GiB 保留线保持。
