# 原补充编辑失败后的未用审核接续

原 [24 批最终补充编辑](course-corpus-gap-editor.md) 的实际作业
`87e51e9142e9ab14315873679be8dd58` 在第一批首次编辑后停止，完成保持 0/24。
实际 worker `25516 / 1794273822.5716796` 和 guard `23676 / 1794273820.979033`
均已退出，guard 返回 1。原因是 `wiki_stage_attempt_budget_exhausted_batch-0000-edit`，
不是 GPU 未启动或模型请求一直无返回。

唯一实际编辑是完整 stop JSON：输入 37,832、输出 2,191 tokens，原返回 SHA256：

`9126c170e9f62c9d43c877f2cf51803b8472bc5ee13c443cb4cc41ca3fd6231e`

原实际请求与解码 schema 都已认证。第一处错误是一个新段落的 citations 中存在完全相同的
重复项；精确去重后，原候选校验又发现三个新段落都没有被任何目标映射引用。
不能称原候选结构已通过，也不能程序猜测引用关系、删除段落、接受失败编辑缓存或再写一遍。
第一批独立审核及后面 23 批的原编辑/审核角色仍未用。

## 固定的接续规则

新入口 `wechat_decrypt_tool.course_textbook_corpus_gap_metadata` 仅移除完全相同的
citation 列表重复项，保留原引用集合、顺序、正文、标题、位置、判断和全部组成员。
三个未映射段落以 `unmapped_addition_ids` 明确保留；这不是接受原候选。
原未用独立审核收到带 `untrusted_metadata_normalization` 的完整候选，继续检查事实和判断。
原未决覆盖、分类冲突、父稿意见和未映射缺陷均不能机械转成通过。

后面 23 批仍按原先每批一次编辑、一次另一权重审核运行，无第二次写作或语义修订。
在启动前明确绑定：后续实际首次编辑若只出现同类精确重复或未映射段落，必须先认证其
原请求、schema、完整实际响应和已花额度，再由该批原未用审核核对不可信候选。
其他结构、正文、来源、边界或引用错误停止；不允许借新执行刷新额度。
原失败 progress、guard、attempt、response 和旧预算保持原字节，进度写入新的接续目录。

18 项新合成测试与 27 项原编辑测试通过，Ruff 通过；不等于课程内容验收。
成功 prepare 后本入口与其全部绑定模块冻结。

私有目录：

`D:\Project\WeChatDataAnalysis\output\course-knowledge\wiki\corpus-gap-metadata-continuations\87e51e9142e9ab14315873679be8dd58`

首次 prepare 在诊断出未映射段落时失败、尚未注册。
旧源码快照及该次失败保留在 `wiki/corpus-gap-metadata-preparation-history/87e51e9142e9ab14315873679be8dd58`，
未获得或使用新角色。

## 实际独立输入恢复

副本：

`C:\Users\Administrator\Documents\CourseKnowledgeBackups\corpus-gap-metadata-inputs-20261110-094434`

1,303 文件、72,700,102 字节，manifest SHA256：

`fab565e5cc63326de8148b109a2150f212fd2318513dd36fc25101ae75930b05`

再次复制的源码真实执行新入口 load 和 inspect，重建同一原失败终态、完整响应、
精确转换和三个未映射缺陷；同一 24 个原编辑请求及第一批未用审核的 schema、输入、
请求哈希与预算均一致。审核实际输入 40,872 tokens、输出预留 8,192、context 65,536。
原归档读取 0、模型调用 0，所有剩余角色仍未用。
恢复驱动禁止网络、SQLite、推理子进程及原根目录 fallback。

包含完整父稿及已结束 55 批覆盖的原字节和先前实际独立恢复回执；没有在本副本重新执行
父稿和 55 批覆盖的全文结果重放，不将字节复制称为新的全部结果重建。
不含未来编辑结果、媒体、模型、全量数据库或完整环境。

在该副本的 `restored/workspace` 中执行：

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/backup_corpus_gap_metadata_inputs_20261110.py verify `
  output/course-knowledge D:\Project\WeChatDataAnalysis\output\course-knowledge `
  87e51e9142e9ab14315873679be8dd58 ..\..\manifest.json
```

## 派发与剩余验收

09:46 在独立恢复通过后首次隐藏派发 launcher `4052`；实际 guard、worker 和完成量须独立核对。
随后已实查 guard `12144 / 1794275160.4142275` 和实际 worker
`26644 / 1794275165.6363077` 存活，已完成 1/24，进入 `batch-0001-edit`。
第一批结束不表示三个未映射段落已经解决，全部候选仍为私有未验收材料。
共享 GPU 锁、180W 设置、200W/70°C 保护、不自动热恢复和两盘各 32 GiB 保留线不变。
原健康来源任务未重启，历史暂停与失败不清除。

实际成功终态后必须通过新入口 `verify` 重放；原编辑入口不能重放这次失败首次编辑，
不得用创建一个假的成功缓存来让旧入口通过。真实结果仍须另做独立备份恢复。
接续结果即使结束也只是私有补充候选；全文整合、未映射段落去向、课件、解释图、
最多 5 道选择题和实际阅读验收仍待完成。公开文章仍为 1 篇，来源删除和全量 Goal 完成均未允许。
