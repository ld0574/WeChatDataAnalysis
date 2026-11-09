# 原修订越界映射的未用最终审核

## 真实失败和允许的接续

作者 `1dde57c07edb707b95bc3b9ad30e5939` 原 `section-0001-write` 的两次额度，
以及 [结构恢复](course-corpus-structural-resume.md) 的两次原 `repair` 额度均已消费。
修订真实输出 7 段不同、非空正文，11 项任务的段落映射超出实际数组。
完整两份请求、实际解码 schema、原失败、原始 stop 响应、不同模型 SHA 和 GPU wire 回执均保留。
本入口逐一认证原初审及两份失败，只选择最后一份完整真实修订，不拼接正文、不再调用 write/repair。

对每条真正越界的映射，全部 7 段仅作为明确标记的、未验证的正文查找范围，
供原尚未使用的 `section-0001-repair-review` 实际核对。
有效映射、教学分类、正文、图示、自测及旧失败均保留；引用修正也只是未验证元数据提案。
程序不会声称全部正文已经覆盖知识点，不创建失败 repair 的接受缓存。
负面最终审核照常进入文章问题清单，原完整原文覆盖义务及旧填充稿问题也继续保留。

## 后续原角色的解码约束

仅未来从未调用的原单元 write/repair 使用带 `paragraph_count` 的补充格式：
先选择 2–12 中一个真实段数，每个 oneOf 分支同时锁定精确数组长度和相应的最大段落索引。
这解决了旧固定十二段导致填充、可变段数却仍允许索引到十一的两种结构错误。
去掉这个计数标签仅是内部格式转录，真实 raw 响应和完整请求/schema/收据均独立保存。
原任务必填、非空/不同正文、有效教学不能空映射、原文/引用范围及显式非教学分支全部保留。
段数、语法通过或原最终审核通过，都不能代替整课覆盖、图文和读者验收。

补充契约、提案、守护和派生位于旧恢复目录下独立 `index-scope-review/`，
旧入口及运行中的抽取、覆盖代码未编辑。原预算不刷新，不新增语义修订或新模型角色。
未来原角色仍是每角色两次结构尝试；失败或温控暂停即停，不自动重试或热恢复。

## 本轮实际验证

79 项新增及相关测试通过，Ruff 通过。测试包括实际 future stage 解码适配、
计数/长度/映射边界绑定、空白/重复/漏任务拒绝、完整请求和 schema/wire/指标篡改拒绝、
真实原角色选择、失败 write/repair 零调用派生、负面最终审核和所有原覆盖义务保留。
11 月 10 日首次 prepare 已实际通过：7 段、11 项越界映射、原 1/14 前缀，新增推理 0。
准备后新模块冻结，SHA256 为
`f0118ee5f877a81f7042f2e3c7bad17a9f2d7bae5a9eb12c30bef467eb372f1c`。

```powershell
$env:PYTHONPATH='src'
$env:PYTHONUTF8='1'
$env:PYTHONUNBUFFERED='1'
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_corpus_index_review `
  --root output/course-knowledge --job 1dde57c07edb707b95bc3b9ad30e5939 load
```

原最终审核实际完成后，可用 `prefix` 零推理重建当前进度；整稿成功退出后用 `verify`。
prepare 和 guard 均只允许一次，缺真实缓存时只读重建拒绝推理。
GPU 设置 180W，200W 保护上限、70°C 暂停且不自动热恢复，C/D 各保留 32 GiB。
本入口不发布文章或删除视频，不启动原两课批次的第二份课程。

## 实际独立恢复与启动

输入副本 `C:/Users/Administrator/Documents/CourseKnowledgeBackups/corpus-index-inputs-20261110-010954`：
603 文件、41,708,838 字节，manifest SHA256
`4151ea3f805b227954133a9b83651d5861f8f16f1b37fd413b89b918e4fc14cf`。
从再次复制的代码、完整材料、原初审、两份真实失败/收据和有限真实 SQLite 实际执行 load：
通过原 1/14 前缀、7 段、11 项越界映射和两份失败认证；20 份旧 group proof 按精确哈希重定位，
没有回读原材料目录或新增推理。SQLite 沿用原有限 2/659/368 行，未扩成全量库。
副本不含模型、视频或完整环境。

首次 `corpus-index-inputs-20261110-010704` 因 Windows/POSIX 路径别名导致原进度与当前进度
指向同一备份目的地，触发 `whole_sources_immutable_backup`；失败和有限副本保留。
未覆盖该目的地，新适配器先统一路径键，再建立上述全新副本并实际恢复成功。
这只修复私有备份适配，不修改冻结执行模块、历史材料或角色预算。

在副本 `restored/workspace` 内令 PYTHONPATH 指向其 src，用主 venv 执行只读恢复：

```powershell
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/corpus-index-restore-20261110.py `
  output/course-knowledge 'D:\Project\WeChatDataAnalysis\output\course-knowledge' `
  1dde57c07edb707b95bc3b9ad30e5939
```

01:11 首次启动本入口 guard。启动器存活只证明已经派发，
须继续核对实际 guard/worker、最终审核缓存及后续未用角色的真实结果。
