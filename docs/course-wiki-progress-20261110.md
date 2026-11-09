# 2026-11-10 实际续跑记录

本轮恢复前检查了原 PID/创建时间、状态与实际回执；11 月 8 日的旧进程均已结束，
没有将旧的 running 文件当成健康任务。沿用用户确认的 GPU 恢复授权，保留旧暂停和失败记录。
全量目标仍为 340 节音频、2 份无声来源和全部可恢复课件；完整读者文章仍为 1 篇。

## 第三课完整内部稿及结果恢复

作者 `c2240cf9be918a77b74fb0a91f493568` 已于 11 月 8 日 11:16 完成 14/14 单元，
原守护实际成功退出。本轮零模型调用实际重建全部 14 单元，253 个确认点和 16 项待核原任务保持。
快照 SHA256 为 `6b7585d9ce9e664c75e68d8e52e2a572563e8c4261b4fde507adc388a1d41cae`。
仍有 5 项问题：一个单元语义审核、完整原文覆盖、课件关系及两条原失败映射的覆盖义务；
尚未验收或发布，不计成第二篇成品。

本轮完成结果的有限副本为：
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/bounded-paragraph-results-20261110-001044`。
697 文件、36,506,799 字节，manifest SHA256：
`cb0e8c6f68b1a3b9e012e9ebc0b6ae02f8cf6ee92150f6d44e874e89966eceaf`。
已从再次复制的独立代码、原请求/响应、材料及有限 registry 实际重建同一 14/14 单元和 5 项问题，
新增推理为 0；13 份 legacy group proof 按原字节哈希严格重定位，有限 SQLite 1/302/140 行。
不包含模型、视频、全量数据库或完整环境；原失败副本保持。

## 来源尾队列恢复

原来源批次 `d174d73105df5bae92521df78af26386` 于 11 月 8 日完成并发布 1/3 份来源后停止。
实际故障为 `requires_source_batch_prior_guard_exited_successfully`：全局 guard 在控制器内运行，
记录的是控制器自己的 PID；第二份来源前将仍存活的自身误判为未退出的前一个 worker。
原批次、完成来源、失败和已消费预算均未修改。

新增独立入口 `course_source_tail` 只接续原计划从未尝试的两份来源。
它核对连续已完成前缀、真实导出与目录成员、原文件、来源身份、音频输入和绑定代码；
每份新来源使用私有 guard，以实际成功 worker 返回和来源校验作为完成条件。
不再用全局 guard 的控制器 PID 判断下一份能否运行，不自动重试或热恢复。

本轮实际 prepare 一次，复用原已完成 1 份、选中原未尝试 2 份；20 项相关测试和 Ruff 通过。
新控制器 `27400 / 1794240557.613498`、实际来源 worker
`32352 / 1794240558.7794082` 已于 00:09 启动；00:15 核对均仍健康，正在首份 ASR 模型加载。
这是来源抽取，尚不等于完整文章。沿用 Qwen ASR float32 / batch size 4、原 OCR/笔记、
共享 GPU 锁、实际导出校验和来源发布流程；运行中的代码已冻结。

新状态位于原批次 `unattempted-tail/`，每份有 `item-00-guard/` 等独立守护目录。
有限契约和代码副本位于
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/source-tails/d174d73105df5bae92521df78af26386`；
尚不是独立完整结果或环境恢复。

## 其余终态及未决

七张 notebook 原图任务 `d2338270c476ef71753983f3a9d4e3c7` 已完成 7/7。
本轮实际 verify 重建：2 项有支持、5 项未决、新模型调用 0。
原图由相同 Qwen vision 权重自审，没有不同权重的独立像素复核，不能据此宣称图文验收通过。

PDF 尾控制器于 11 月 8 日 14:28 记录已完成所选 21–27 批；第 24 批实际 64 页、0 errors。
还须核对各批次真实校验及备份收据；没有重新跑 OCR，也不把这七批等同全部课件。

两课接续批次 `60ee5378015a2d2f2a221548ba19df87` 已完成首份 20/20 组账本，
首个新作者 `1dde57c07edb707b95bc3b9ad30e5939` 在 1/14 单元后结构失败退出。
`section-0001-write` 两次均正常 stop、完整解码，但被外层引用提案校验拒绝；
本轮继续只读诊断底层真实错误。未重跑已耗尽的 write，未刷新预算或启动第二份课程。

第二课完整覆盖的 2,112 未决分句、68 份分类冲突、原稿 9 项问题和拒绝的末节提案保持。
课件关系仍有 41 项未决。内部稿、完成抽取和审核统计均不冒充发布验收。

GPU 设置 180W，200W 保护上限、70°C 暂停且不自动热恢复、C/D 各 32 GiB 保留线不变。
00:15 GPU 约 47°C，C 57.29 GiB、D 41.74 GiB。3003/3005 服务健康，未重启。
所有私有正文、识别材料和模型理由保留本机，未写入公开文档或对话；原视频保持。

## 00:34 写作实际接续、图片结果恢复及 PDF 收据核对

对新作者两份真实失败进行了零推理诊断：固定 12 段的第一份响应含三组重复段落，
第二份响应最后三段为空白；外层把真实错误掩盖成引用提案错误。
新增独立入口只使用原尚未调用的 review、一次 repair、repair-review 和后续原单元，
允许实际 2–12 段非空、不同正文，保留全部教学任务、完整原文和覆盖义务。
61 项相关测试及 Ruff 通过，实际 prepare 和独立输入恢复均成功，原失败/预算未修改。

实际新 guard `31708 / 1794241882.3268125`、作者 `10116 / 1794241930.7191763`
均健康，`section-0001-review` 等待来源 ASR 的共享 GPU，尚未完成原修订。
输入副本 585 文件、41,455,146 字节，有限 SQLite 2/659/368 行，20 份旧 group proof 严格重定位。
路径、manifest、只读恢复命令与范围见 [原两课结构恢复](course-corpus-structural-resume.md)。

七图完成结果已从独立源码、原图片/完整文字、真实模型响应和有限真实 SQLite 再次执行 verify：
7/7、2 项有支持、5 项未决、模型调用 0；仍没有不同权重像素复核，不代表读者验收。
成功副本为
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/notebook-image-full-results-20261110-003330`，
627 文件、92,468,748 字节，manifest SHA256：
`8819a91d5be05330761f451b7cd101b2dc4437f5f8f684dc1eddb7f44f6c875d`。
有限 registry 为 1/311/192 行，另含 7 份实际 OCR revision、7 段实际 OCR document 和 7 份历史 metadata。
原始输入副本未包含 SQLite；第一次适配误把清单数组当字典，第二份有限副本因缺 SQL 输入恢复失败。
原失败和副本保留，没有回读主库冒充独立恢复；新成功副本补入了真实有限 SQL 输入，不含全量库或模型。

在成功副本 `restored/workspace` 内设置 PYTHONPATH 为其 src，用主 venv 执行原图片模块：

```powershell
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  -m wechat_decrypt_tool.course_textbook_notebook_image_semantics `
  --root output/course-knowledge `
  --inventory 68c6398d1b05c82fb32869397831b15cfff29ee2fa9ec1f2ff303ee4c51f6de7 `
  --tokenizer output/course-knowledge/wiki/whole-lesson-narrative-20261104/local-model-tokenizer.json verify
```

PDF 21–27 批逐份核对实际页校验、结果校验、备份校验及磁盘 manifest：
6×64 + 36 = 420 页次，所选范围均实际通过，0 failed，7 份备份 manifest SHA 全部一致。
没有重新 OCR；这些是所选批次的完整性验证，不能代表全部课件或语义准确率。

来源首份 ASR 已到约 5,400/8,200 秒，仍为原健康控制器及 worker，没有重启。
GPU 约 54°C，180W 设置，C 56.94 GiB、D 41.74 GiB；无新暂停或停止标记。
完整读者文章仍为 1 篇，全量 Goal 保持 active。
