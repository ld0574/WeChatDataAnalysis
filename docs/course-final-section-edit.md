# 完整原稿之后的独立单元编辑版

`course_textbook_final_section_edit` 为完整原稿中的实际负面单元制作独立编辑提案。
这是最终编辑阶段的一份私有版本，不重跑原作者、不更新原作者缓存，不清除原审核或预算。
本篇、此单元的最终编辑登记只能建立一次；更换模型、提示或配方不能获得第二份编辑额度。
每次仅一份提案和一次不同本机模型的独立复核，结构失败同样消耗该角色，无语义自动修订。

## 本次输入及问题范围

父稿 `c7258efff888d77a26e1234dd153ef7a` 的 16 个单元已完整组装，原 9 项问题继续保留。
7 个单元存在负面审核：6 个主要是覆盖问题，`section-0013` 另有段落事实、图示及可读性问题。
本次只编辑后者，包含 38 个任务点，其中 3 个为待审原文卡。
原上下文的 34 份完整目标来源及 36 份相邻来源约 17,511 tokens，均保留完整文字。
实际请求还包含旧候选和负面意见；token 预算在加入这些字段之后测量，不能用截断适配。

准备先用原完整重建入口核对作者、实际原始请求、响应、恢复凭证及负面审核，再登记新编辑阶段。
该旧稿需要显式 `disposition` 恢复适配，与现有完整覆盖任务一致。
旧缓存读取器的 `repair-*` 前缀还匹配 `repair-review-*`；本入口读取完整父稿重建已认证的精确
修订稿文件，并同时检查其 SHA 在父稿证明内，不修改旧读取器或伪造通过状态。
前两次 CPU 准备失败分别来自缺少原恢复适配和该前缀问题；它们发生在登记及模型调用之前。
状态保留于 `wiki/final-editorial-preparations-20261108/`，不删除或改写为成功。

新 schema 在首次调用前限制可引用来源、引用唯一性和待审任务的互斥去向：
教学必须有段落，非教学必须 `teaching=false`、`paragraphs=[]`。排除判断仍需原文支持和独立审核。
新正文由 `qwen3.5:9b` 提出，`course-defiant:q4` 独立核对全部目标、正文、图和内部例题。
审核不带旧负面理由，完整原文保持相同；两个模型的真实 digest、GPU context、请求、解码 schema、
输入 token 和实际返回均保存并重建。不能把账本、旧稿或候选理由当事实。

## 范围与保护

- 原作者、覆盖、来源抽取及 PDF 进程与绑定文件不改动。
- 使用私有目录的独立 guard，模型调用继续等待共享 GPU 锁。
- 180W 当前配置，200W 保护上限，70°C 暂停且不自动热恢复，C/D 各保留 32 GiB。
- 暂停及 supervisor 停止标记保留，传感器/磁盘预检失败和运行失败都不自动重启。
- 结果始终是私有单元提案，不写父稿，不更新课程阅读目录，不发放发布或删除视频权限。

提案即使通过，也不清除原稿的其余覆盖问题。还需核验完整来源覆盖、41 条未决课件文字关系、
7 张图、整篇整合及最终最多 5 道单/多选自测，并实现适用于后续课程的出版证明；
不得用首课专用的 editorial proof 宣称第二课完成。

准备命令如下；一次性登记成功后不能重新准备同一父稿/单元来刷新额度：

```powershell
$env:PYTHONPATH='src'
$env:PYTHONUTF8='1'
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_final_section_edit `
  --root output/course-knowledge prepare --parent c7258efff888d77a26e1234dd153ef7a `
  --section section-0013 --adapter disposition --recovery-section section-0007 `
  --tokenizer output/course-knowledge/wiki/whole-lesson-narrative-20261104/local-model-tokenizer.json
```

执行与零推理重建分别使用 `guard --job <ID>`、`verify --job <ID>`。
verify 必须等待真正 worker 退出，重新构造请求和解码 schema、匹配精确尝试/模型/缓存与 GPU 收据，
重新计算候选及负面审核，检查父稿证明内全部文件不变；缺缓存时禁止获取 GPU 或调用模型。
终端只允许状态、数量、ID、SHA 和路径，所有正文、原文和审核理由留在本机。

16 项测试及 Ruff 通过，涵盖原稿保护、完整来源送入、不同模型审核、负面和结构失败预算保留、
不发布、证据及真实请求/schema 变更拒绝、只读禁止模型及 GPU、CPU 回退拒绝、温控/磁盘、
原控制标记和其他标准 guard 保护、异常脱敏，以及互斥去向和引用唯一性的首次解码约束。
这些测试不代替真实提案或整课验收。

## 实际提案终态与有限恢复

实际作业为 `fe57e38ca15a8097968bf15feba2833a`，完整编辑输入为 25,098 tokens，
未裁剪目标、原文、相邻原文、旧候选或负面意见。真实 writer `2428 / 1794097299.7149189`、
guard `24224 / 1794097298.6296415` 均已退出，原提案没有通过。
唯一一次请求实际 prefill 25,114、输出 4,313 tokens，context 49,152，输出生成 78.176 秒；
从 worker 启动到响应回执约 341.554 秒，包含调度与加载，不能只算推理时间。

解码 schema 通过后，严格检查拒绝了 `requires_private_composition_primary_original_support_in_body`：
37 个任务点在其原覆盖段落中缺少主要原文引用。原始正文共 4 段，覆盖映射将全部 38 点指向第 0 段，
这一映射是否充分仍需真实语义复核，不能靠补引用判断正确。
原 `editorial-write-01.json`、真实响应、失败、原父稿 9 项问题和一次性登记均保留；不重新写作。

输入副本：`C:/Users/Administrator/Documents/CourseKnowledgeBackups/final-editorial-inputs-20261108-082115`，
538 文件、30,795,386 字节，manifest SHA256
`95f9f0100e63d6e7ff337b86748a283627ce53a779f5d7e088a7f1652be96c06`；
独立副本实际 load 通过。终态副本：
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/final-editorial-result-20261108-082726`，
553 文件、31,807,538 字节，manifest SHA256
`fa70ef72965a4d02710f2e2dac45a90fc288c27ee9d99a3369a551c2fbbaa22b`。
后者含本课有限 SQLite registry：1 个来源、311 条转写记录、192 帧，共 504 行；不含全量视频或运行环境。
原终态收据的 `restore` 字段为 null，只记录了失败输入的独立 load，不能称为成功稿的 verify。

## 同一提案的引用元数据复核

`course_textbook_final_section_citations` 独立重建原请求、模型、真实响应、GPU 指标和解码 schema，
只接受上述确切引用缺陷。沿原覆盖映射提出 1 段的引用补充，逐字段证明正文、图、题目及覆盖映射不变。
补充只是假设，不作为事实支持。原 writer 失败文件与状态不改写，也不伪造 accepted write cache。
实际引用提案位于原作业内的 `citation-review/proposal.json`，新入口只能消耗尚未使用的
`editorial-review-01.json`，模型仍为不同权重的 `course-defiant:q4`；禁止重试、语义修订或更换 recipe 刷新额度。

```powershell
$env:PYTHONPATH='src'
$env:PYTHONUTF8='1'
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_final_section_citations `
  --root output/course-knowledge --job fe57e38ca15a8097968bf15feba2833a prepare
# prepare 只能执行一次。后续实际守护与只读重建：
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_final_section_citations `
  --root output/course-knowledge --job fe57e38ca15a8097968bf15feba2833a guard
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_final_section_citations `
  --root output/course-knowledge --job fe57e38ca15a8097968bf15feba2833a verify
```

新的 9 项测试及 Ruff 通过，覆盖原失败不变、不同模型、完整原文、原一次复核额度、负面保留、
CPU 回退拒绝、请求/schema/正文/映射改动拒绝及只读重建禁止模型和 GPU。仍无文章发布或删源权限。

实际复核 worker `21324 / 1794098294.9159698`、guard `2112 / 1794098292.3504815` 已正常退出。
该次复核完整输入 22,159 tokens，实际 prefill 22,175、输出 3,337，context 32,768，
输出生成 67.456 秒。47 项中 43 项为负面：正文 4、覆盖 35、图 1、概览 1、小结 1、题目 1。
结果为 `same_final_proposal_review_unresolved`。正常退出只表示复核执行完成，不能表示稿件通过。
此提案不采用，原稿 9 项问题继续保留，不自动再写作或复核。

引用复核输入副本为
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/final-citation-inputs-20261108-083702`，
556 文件、31,852,375 字节，manifest SHA256
`9170dd837382ae8dccda1e7741eff8fffed1a8325f065b7d7d282da454fed1e8`；
从独立源码和材料实际 load，提案 SHA 为
`2630b9d2f0d9365ea4317ac59d3b963023f52bcf22d1641b93b3ab95dfb8a2ac`。
终态副本为
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/final-citation-result-20261108-084007`，
576 文件、31,893,906 字节，manifest SHA256
`847656c0b898c8b100b9e29facb43a2bacf2e10ada05f1d0af2f566c7ee031e3`。
再次复制后的独立源码、原始请求/响应/审核及有限数据库实际执行 verify，得到完全相同的 43 项负面，
新增模型调用为 0。有限 registry 仍为 1/311/192 行，quick_check 通过；不是全量归档或运行环境备份。

恢复时使用该目录下 `restored/workspace/src` 作为 PYTHONPATH，并把上述 verify 命令的 root 指向
`restored/workspace/output/course-knowledge`。依赖仍使用原主虚拟环境；不得改为 guard、worker 或 prepare。
只读核对会重新认证代码、提案、完整父稿证明、真实请求、解码 schema、GPU 回执与审核，不获取模型锁。
