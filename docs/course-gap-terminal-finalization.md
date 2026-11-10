# 原补充任务结束后的 CPU 收尾

这是一份有限、一次性的本地执行计划，不增加模型调用、不创建定时自动化，
不重启或中断任何健康 worker。它只等待[原剩余补充批次](course-corpus-gap-remaining.md)
中准确的 guard 和实际 worker 退出，按顺序处理：

1. 确认同一 PID/创建时间，实际 worker 和 guard 都退出、原任务 complete、guard 返回 0。
2. 零推理重放全部原批次，分别认证已审核批次与保留未决批次。
3. 从实际完整结果创建有限独立副本，用复制源码真实重建结果，禁止原根目录回读、
   网络、SQLite、推理子进程和新增模型调用。
4. 真实结果恢复回执通过后，运行[私有正文整合](course-gap-assembly.md)。
5. 再单独备份并独立重建真实整合草稿；保留原稿、原位置、全部候选和未决义务。

执行代码及两个恢复驱动留在私有本地目录：

`D:\Project\WeChatDataAnalysis\output\course-knowledge\wiki\finish_corpus_gap_remaining_20261110.py`

`D:\Project\WeChatDataAnalysis\output\course-knowledge\wiki\backup_corpus_gap_remaining_results_20261110.py`

`D:\Project\WeChatDataAnalysis\output\course-knowledge\wiki\backup_corpus_gap_assembly_20261110.py`

执行状态位于：

`D:\Project\WeChatDataAnalysis\output\course-knowledge\wiki\corpus-gap-finalizations\87e51e9142e9ab14315873679be8dd58`

首次 prepare 真实认证原 worker `8304 / 1794278067.2587314`、guard
`17412 / 1794278061.9630704` 均健康，绑定原 execution、源码和两个驱动的原字节哈希。
11:09:45 隐藏派发 launcher `10704`；之后实查实际 controller
`29164 / 1794280185.8713732` 存活，状态为
`waiting_for_exact_original_gap_worker_and_guard_exit`。
没有把 launcher 误作实际 controller 或 guard。

入口、驱动和整合模块从 prepare 起冻结。任何原任务失败、温控/用户暂停、输入变化、
认证或实际恢复失败均保留记录并停止，不自动重试或热恢复。
等待期间不占用 GPU 锁；结果备份检查 C 盘双副本仍高于 32 GiB，沿用原保护。

此处记录的是已启动的后续收尾，尚不证明完整结果恢复或整合成功。
必须核对实际 `result.json`、每阶段回执及两个新独立副本，不能把派发当作通过。
即使全部收尾成功，也只得到私有草稿；全文编辑、覆盖缺陷、课件、解释图、
最多 5 道选择题和实际阅读验收仍须完成，不计为公开文章或 Goal 完成，不删除原视频。

## 实际 CPU 收尾已完成

11:33:19 controller 写入真实成功终态后退出，未产生失败记录，也未调用模型。
已实查其不再存活；原 worker/guard 也已退出且原 guard 返回 0。
23 个已审核批次、1 个保留未决批次及 47 个实际原角色的完整结果已重放并独立恢复；
108 个原正文项及 23 个插入段落的真实私有整合稿也已再次独立恢复。
两份副本、manifest、范围和重复验证命令见
[完整补充结果](course-corpus-gap-remaining.md)及[私有整合稿](course-gap-assembly.md)。
原归档回读和新增推理均 0。没有把父稿/55 批覆盖的字节包含称为本次再次完整重放，
也没有把此成功终态计为语义验收或新公开文章。
后续实际位置依赖诊断另保留了 7 个组、85 个目标的新整合覆盖义务。
