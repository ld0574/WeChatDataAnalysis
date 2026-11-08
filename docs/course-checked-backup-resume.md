# 已审核写作结果的备份接续

`course_textbook_checked_backup_resume` 接续作业 `c2240cf9be918a77b74fb0a91f493568`。
它解决运行状态文件变化与不可变材料备份冲突，不新增模型角色或重新审核已通过的结果。

## 实际故障与修复边界

原第八单元的 `section-0007-repair-review` 已实际完成，21 项检查、0 项负面。
随后重放前七单元缓存时，`whole_sources_immutable_backup` 拒绝覆盖已备份的
`point-mapped-resume/omitted-mapping-review/guard-progress.json`：旧副本是运行中的状态，
实际 guard 状态随后发生了变化。原 guard `23008 / 1794102595.2668436` 及
worker `22828 / 1794102628.8097687` 已退出，returncode 1；原故障和副本保留。

新接续在独立进程作用域内替换备份函数，未编辑冻结作者、审核适配器或原备份。
静态原文、请求、响应、审核及代码仍按原路径不可变保存；任何变化仍拒绝。
以 `progress.json` 结尾的运行状态读取一次，使用同一份字节的 SHA256 保存独立版本，
不覆盖旧状态，不会因两次读取之间发生更新而将内容与哈希错配。
备份函数退出作用域后还原；原每角色额度、两次失败写作、两次失败修订均未刷新。

接续先只读认证真实最终审核，再使用原完整 14 单元计划，重放已有缓存并继续未用角色。
三项遗漏点的范围提案仍保留整条原文覆盖义务，21 项审核通过不代表整课验收。
不会发布私有稿或删除源视频。

## 实际执行

6 项测试与 Ruff 已通过。冻结适配器 SHA256：
`df6f160f7651470026c783d07e980bb50bffbd12a1de4b3d7978978095651dc5`。

prepare、guard 均已实际执行一次，不重新启动。
10:05 核对：实际 Python guard `24480 / 1794103375.2291036`，
实际作者 `23128 / 1794103410.5511036`，guard 的直接子启动器为 `15660`。
作者已从 7/14 推进到 8/14，正在 `section-0008-write` 等待共享 GPU，
证明已越过先前备份冲突；后六单元和整课验收尚未完成。

10:10 已实际只读重建 8/14 个单元、保留 1 项未决，新增模型调用为 0。
快照为 `wiki/whole-manuscript-snapshots/b21a838308d6c89f29e61d9150514960ee10b110392c38d6a4c205e177a2628f.json`。
这比仅查看进度文件多核对了原请求、实际响应、审核和缓存链，仍不允许读者发布。

guard 的状态和失败位于原作业的
`point-mapped-resume/omitted-mapping-review/versioned-backup-continuation/`。

只读核对使用以下参数。`prefix` 检查实际已完成单元，完全退出后才能使用 `verify`：

```powershell
$env:PYTHONPATH='src'
$env:PYTHONUTF8='1'
$env:PYTHONUNBUFFERED='1'
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_checked_backup_resume `
  --root output/course-knowledge --job c2240cf9be918a77b74fb0a91f493568 `
  --tokenizer output/course-knowledge/wiki/whole-lesson-narrative-20261104/local-model-tokenizer.json prefix
```

70°C 暂停且不自动热恢复、200W 保护上限、180W 当前设置、C/D 各 32 GiB 保留线保持不变。
本次接续不清除原温控记录、supervisor-stop 或预算标记。

## 已实际验证的有限输入恢复

副本：`C:/Users/Administrator/Documents/CourseKnowledgeBackups/checked-backup-inputs-20261108-100127`。
583 文件、34,909,271 字节，manifest SHA256：
`797cbf8bde28369453f35e8873e21605617763dc57161520bbbed5082cb993ca`。

已再次复制并从独立源码实际 load，认证原七单元、最终审核、三项覆盖义务及原故障。
13 份旧账本 group proof 只按原始字节哈希进行一对一路径重定位，未改写证据。
有限 SQLite 的 videos/segments/frames 为 1/302/140 行，quick_check 通过。
副本不含视频、音频、完整原画面、全量数据库、模型或完整运行环境，仍依赖主 venv。

在副本 `restored/workspace` 中，将 PYTHONPATH 设为其 `src`，执行：

```powershell
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/checked-backup-restore-20261108.py `
  output/course-knowledge 'D:\Project\WeChatDataAnalysis\output\course-knowledge' `
  c2240cf9be918a77b74fb0a91f493568 `
  output/course-knowledge/wiki/whole-lesson-narrative-20261104/local-model-tokenizer.json load
```

这是已验证的输入恢复，不能用它声称作者终态恢复或文章验收成功。
完成后须另做 results 副本并实际独立 verify；不从输入副本启动模型作业。

## 后续停止

10:11 核对新 guard 和作者已退出：第九单元 `section-0008-write` 的原两次结构尝试耗尽。
这次原因是任务缺少段落标注，与已解决的运行状态备份冲突不同。
原八单元仍通过实际重建，原失败和额度保留。后续只使用原未用审核及修订角色，
详见 [漏标写作的原修订额度接续](course-bounded-paragraph-resume.md)。
本文件 10:05 的存活记录为当时状态，不代表作者仍健康或已经完成。
