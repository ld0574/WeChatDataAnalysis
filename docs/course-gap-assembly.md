# 补充审核结果的私有正文整合

`wechat_decrypt_tool.course_textbook_gap_assembly` 是零推理的 CPU 入口。
它在[原剩余补充批次](course-corpus-gap-remaining.md)实际成功退出、完整重放，
且真实独立结果恢复通过后，才允许创建一份新的不可变私有草稿。
没有新编辑、语义修订、角色额度或公开文章发布。

它保持原完整私有稿及全部元数据，按原有 A 编号重建原生段落/图示位置，
逐批认证候选及独立审核与原结果一致，只有同时满足以下条件的新增段落才进入正文：

- 新段落本身通过实际独立审核。
- 至少一个明确引用该段落的 covered 分组已经实际解决，包含该组全部成员及所需新增段落。
- anchor 指向确切存在的原正文位置；`new_section` 保留为待编辑安排的新主题。
- 正文不是原文完全相同的重复段落；完全相同的新增段落只安排一次，保留各批原记录。

未映射、独立审核未通过、没有已解决 covered 分组或尚无明确位置的全部段落，
保留完整正文、原引用、批次编号和机器原因，不改写、删去或推测对应关系。
一个批次整体保留未决时，其段落不进入正文。审核批次与未决批次继续分别统计。
原未决覆盖、来源矛盾、父稿意见及未完成的图文/课件/选择自测/实际阅读验收均保持。
整合草稿不能计为可读文章成品或质量通过，不能用于允许删除视频。

产物位置：

`D:\Project\WeChatDataAnalysis\output\course-knowledge\wiki\corpus-gap-assemblies\<原补充作业 ID>\private-draft.json`

该 JSON 同时保留完整父稿、按原位置排列的正文块、全部已审核补充段落、
未放入正文的段落和原整轮状态，全部留在私有 output，不提交 Git。
命令只输出数量、哈希、状态和路径，不输出正文或审核理由。

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
& .\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_gap_assembly `
  --root output/course-knowledge --job 87e51e9142e9ab14315873679be8dd58 build
```

将 `build` 改为 `verify` 只读重建并比较已保存的完整草稿，禁止新增推理。
第一次 build 后冻结该入口，再做实际草稿的独立备份恢复。

## 当前验证边界

11 项合成测试和 Ruff 通过：原稿/元数据保持、准确插入、未映射和负面审核隔离、
整组未解决、待定新主题、原文与跨批精确重复、未知位置、变动的父稿与审核均有覆盖。
正在运行的真实作业上，只读核对了全部 108 个原正文项及原生路径，
24 个上下文中的完整旧正文与原私有稿完全一致，原数据及父稿哈希一致，新调用 0。
这只是实际位置与输入诊断，尚未运行完整整合、完整结果恢复或阅读验收。
另有检查确认：没有真实独立结果恢复回执时不得创建草稿；只读 verify 不补写缺失草稿，
build 不覆盖已有草稿，已保存内容发生变化时 verify 必须拒绝。

## 实际整合稿和结果恢复

原 24 批的实际角色全部结束并完成独立结果恢复后，本入口首次真实运行成功。
保留 108 个原正文项及完整原稿元数据，实际放入 23 个补充段落；
74 个已审核候选段落的另外 51 个完整保留待处理。其中 41 个未映射，9 个新主题待安排，
原因可能重叠，不能按这两个数字相加推断其余缺陷。4 项父稿问题保持。

私有整合稿 SHA256：

`74eb046aa2a9c148df5119ef1d64d943ceda0bd3d4fb4a20e63a785cfe3cd669`

实际独立副本：

`C:\Users\Administrator\Documents\CourseKnowledgeBackups\corpus-gap-assembly-20261110-113046`

1,488 文件、74,294,103 字节，manifest SHA256：

`18d3175d3b0391c480181c4fb20e65817fe2ad3c63b8528c82e82b308889bf26`

复制源码真实重建全部原补充角色结果及同一完整整合稿、所有保留段落和意见；
原归档回读 0、新模型调用 0。副本包含父稿和 55 批覆盖的原字节与先前回执，
没有在此次重复执行它们的全文重放，也不含媒体、模型、全量库或完整环境。

在该副本 `restored/workspace` 中执行：

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/backup_corpus_gap_assembly_20261110.py verify `
  output/course-knowledge D:\Project\WeChatDataAnalysis\output\course-knowledge `
  87e51e9142e9ab14315873679be8dd58 ..\..\manifest.json
```

## 整合后不能直接继承原候选覆盖

零推理诊断逐批重建并核对 23 份原候选的精确 SHA256、原分组和整合稿真实正文成员。
发现 7 个原已解决 covered 组依赖未进入正文的段落，涉及 85 个目标；其中 50 个
曾属于最初的 1,043 项未决，另外 35 个属于原来源冲突处理范围。
不能把原候选的 545 项未决直接称作整合稿缺口。
这 85 项与 545 项的并集是 **630 个尚未建立的覆盖义务**，并不表示 630 项确认遗漏，
也没有通过本诊断建立其余全文的事实或阅读验收。
53 份原来源冲突及全部父稿、课件、图文、自测与阅读义务继续保留。

实际诊断位于：

`D:\Project\WeChatDataAnalysis\output\course-knowledge\wiki\corpus-gap-placement-obligations\87e51e9142e9ab14315873679be8dd58\actual-placement-coverage-dependencies.json`

报告 SHA256：`97ca5243cc1ae2d3db2251ea76a39ef8402291afd8498a209c6017316201d33b`。
诊断未修改冻结的整合稿、原覆盖、候选、引用或任何预算。
整合稿仍为私有材料，公开文章仍 1 篇，不得据此发布、删除视频或完成全量 Goal。

### 位置依赖诊断的单独实际恢复

诊断已另做完整有限副本并从复制代码实际只读重建，原归档回读 0、新推理 0：

`C:\Users\Administrator\Documents\CourseKnowledgeBackups\corpus-gap-placement-audit-20261110-113603`

1,492 文件、74,306,340 字节，manifest SHA256：

`4fefb2c2af6943cebf759e8800770918465415b52c9f6c507108fdf50d99de6a`

该副本包含前面已单独恢复的整合稿及完整补充角色原字节，本次只重新执行位置依赖诊断，
没有将字节包含称为再次整合稿/47 角色完整重放，也没有恢复媒体、模型、全量库或完整环境。
恢复驱动禁止网络、SQLite、推理子进程和原根目录 fallback。
在该副本 `restored/workspace` 内执行：

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/verify_corpus_gap_placement_20261110.py `
  output/course-knowledge D:\Project\WeChatDataAnalysis\output\course-knowledge `
  87e51e9142e9ab14315873679be8dd58 ..\..\manifest.json
```
