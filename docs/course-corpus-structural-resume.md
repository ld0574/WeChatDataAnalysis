# 原两课作者的空白与重复段落恢复

## 实际原因

批次 `60ee5378015a2d2f2a221548ba19df87` 的首个作者
`1dde57c07edb707b95bc3b9ad30e5939` 完成 1/14 单元后，
`section-0001-write` 原两次结构额度耗尽。11 月 10 日实际零推理重建该前缀，
逐一核对两份真实请求、完整 stop 响应、解码 schema 和 GPU 回执。

固定 12 段的旧格式导致实际填充：第一次有三组重复段落，仅 9 段不同；
第二次最后三段为空白，仅 9 段非空正文。二者均通过原解码 schema，
随后被正文唯一性或非空检查拒绝。旧引用适配器把非引用的 ValueError
重新包装成 `requires_fresh_authoring_only_primary_citation_metadata_proposed`，
因而第二次没有获得准确的结构反馈。这不是截断、网络或引用本身的故障。

旧代码、原状态、失败、两次响应和预算均保留；没有去重正文、删除空白后伪造成功写作缓存。

## 有限接续

新增独立 `course_textbook_corpus_structural_resume` 先认证原前缀和两份真实失败，
只选择最后一份完整原响应作为未信任审核输入。原 `review`、一次 `repair`、
`repair-review` 仍必须实际执行，即使初审通过也不能跳过修订。
这些以及后续原单元/整篇阅读都是原角色，每角色两次结构额度，不新增写作角色、不刷新 recipe。

只对尚未调用的修订和后续写作使用补充格式：2–12 段实际非空正文，不强制固定 12 段；
保留全部教学任务必填、显式非教学分支、完整原文、引用范围及独立模型审核。
解码 schema 拒绝空白文本和完全重复对象；实际校验继续拒绝重复文本及指向实际数组之外的映射。
真实非引用错误直接保留，不再掩盖成引用提案问题。只有原指定的主引文元数据错误允许提出
未验证的引用提案，不能改正文、教学判断或知识点。

原失败单元的最终修订在编排中明确映射到实际 repair/repair-review，保存派生收据，
不创建失败 write 的接受缓存。负面意见和整课原文覆盖义务保留；另外明确记录
`untrusted_padded_write_requires_complete_original_coverage`，修订通过不代表完整文章验收。

补充契约、旧状态和新 guard 位于原作者目录外的
`output/course-knowledge/wiki/corpus-structural-resumes/1dde57c07edb707b95bc3b9ad30e5939/`，
避免变化的守护状态混入原不可变备份。已运行的源文件和旧作业绑定均未修改。

## 实际核验与操作

61 项新增/关联测试及 Ruff 通过，包括实际解码规则、真实调用适配、错误反馈、
两份原失败/真实 wire 收据逐项认证、请求/schema/响应/指标篡改拒绝、
强制原修订、负面终态保留、已用角色拒绝和整课覆盖义务保留。
首次测试的合成 attempts 目录缺失已修正；真实任务未因该测试失败而启动或消费预算。

本轮实际 prepare 一次，认证原 1/14 前缀，新增模型调用 0；准备后的新模块已冻结。
后续只读输入核对命令：

```powershell
$env:PYTHONPATH='src'
$env:PYTHONUTF8='1'
$env:PYTHONUNBUFFERED='1'
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_corpus_structural_resume `
  --root output/course-knowledge --job 1dde57c07edb707b95bc3b9ad30e5939 load
```

首次执行入口为 `guard`；它使用私有守护、共享 GPU 锁，失败或热暂停即停，不自动重试。
已完成实际原修订后可用 `prefix`，整稿成功退出后用 `verify` 实际零推理重建；
缺缓存时禁止推理。不得再次 prepare、重跑 guard 或对旧失败 write 调用 worker。

180W 设置、200W 保护上限、70°C 暂停且不自动热恢复、C/D 各 32 GiB 保留线不变。
此入口不发布文章或删除来源。第二份课程仍保持原未用身份，没有随本修复另行启动。

## 本轮实际独立恢复与启动

输入副本：
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/corpus-structural-inputs-20261110-002844`，
585 文件、41,455,146 字节，manifest SHA256：
`a258cc16f2e003e1241f764c73c1e3de7b6133c6f9a824cab1ed29314fa6074c`。
从再次复制的独立代码、原材料、原角色响应及有限 SQLite 实际执行 load，通过原 1/14 前缀、
两份失败、原请求和 wire 收据核对；新增推理 0。20 份 legacy group proof 仅按原字节哈希
严格一对一重定位，旧字符串、文件和契约未改写，也不回读原工作目录中的角色文件。
有限 SQLite videos/segments/frames 为 2/659/368 行，quick_check 通过。
不包含模型、视频、全量数据库或完整环境。

在副本 `restored/workspace` 内把 PYTHONPATH 指向其 src，使用主 venv 只读恢复：

```powershell
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/corpus-structural-restore-20261110.py `
  output/course-knowledge 'D:\Project\WeChatDataAnalysis\output\course-knowledge' `
  1dde57c07edb707b95bc3b9ad30e5939
```

00:31 首次启动后，实际 guard `31708 / 1794241882.3268125`、作者
`10116 / 1794241930.7191763` 均健康；00:34 为 `section-0001-review` 等待共享 GPU。
这是原尚未使用的初审，原两次 write 没有重跑，不能计作修订已经完成。
准备及启动后新模块 SHA256 均为
`0c2c95040a443ca3d3ff68661f6b6c14a3404319aa6c5f66c03273b751edbc00`。

## 00:44 实际修订失败，原额度已耗尽

原 `section-0001-review-01` 已实际完成。原修订两次均正常 stop，输出 7 段不同、非空正文，
但 coverage 中的段落编号超过实际 7 元素数组。实际校验均记录
`requires_corpus_structural_mapping_indices_within_actual_array_length_7`。
第一次修订输入/输出为 34,050/5,756 tokens，第二次为 34,062/5,756 tokens。
这两次修订额度已消费，不得重复调用或刷新；最后响应仍为未信任候选，不创建修订接受缓存。

00:44:36 入口保存 `wiki_stage_attempt_budget_exhausted_section-0001-repair`，
实际作者与 guard 已退出，guard 返回 1。原 writer 的 generating 状态未及时更新，
应以新 failure、guard 终态和真实 PID/创建时间共同判断，不能继续报告它健康运行。
独立 `section-0001-repair-review` 仍未使用；若接续，仅能认证原真实初审和两份失败，
保存原正文/映射及失败原因，让原未用审核角色核对显式未验证的映射提案，保留完整原文覆盖义务。
尚未实施该接续，也没有启动第二份课程。
