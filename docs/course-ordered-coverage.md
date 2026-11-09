# 完整分句覆盖的有序数组试跑

## 范围与依据

第三课完整内部稿 `c2240cf9be918a77b74fb0a91f493568` 已实际重建 14/14 单元。
新覆盖入口保留 442 份完整原记录、216,702 字符、14,263 个无损分句及全部实际正文条目，
完整计划为 56 批；原稿 5 项问题继续保留。前三批只是有限实测，不能作为整课覆盖或文章验收。
全量 340 节音频、2 份无声来源和全部可恢复课件的目标不变。

完整覆盖的此前实测主要成本是长中间输出：第二课实际服务时间约 67.5% 用于生成。
11 月 10 日对第二课已经保留的 60 份候选 payload 进行了零推理的无损序列化对照：

| 指标 | 原逐键分配 | 有序数组 |
| --- | ---: | ---: |
| 完整候选分配分句数 | 15,278 | 15,278 |
| 同一 tokenizer 的序列化 tokens | 251,413 | 98,570 |

程序逐份还原两种表示，实际组成员、教学判断、文章依据和理由完全相同，序列化 tokens 减少约 60.794%。
这不是新的模型推理、实际响应 tokens 或端到端加速实测；模型可能改变分组，输入顺序表和复核也有成本，
不能据此宣称整课快 61%。原已完成批次没有重跑或改写。
观察文件 `wiki/performance-observations-20261110/1794242641728830600-ordered-arrays-lossless-serialization.json`，
SHA256 `ec6e52b0e78b397c29aa370d8d522d65a92653329f142ffbd7f2b9d4066d0149`；
同名精确副本已保存于 `Documents/CourseKnowledgeBackups/performance-observations-20261110/`。

## 实际格式和独立核对

`course_textbook_ordered_coverage` 只接受经原 bounded-paragraph 入口实际认证的完整稿。
原父稿唯一 `whole-manuscript-coverage-execution.json` 在首次使用模型角色前登记；
已有任何覆盖预算的父稿直接拒绝，不通过换 codec、配方或作业 ID 刷新原额度。

每个来源的 `ordered_clause_ids` 明确给出当前完整目标顺序，且进入实际模型请求及请求哈希。
同来源 `assignments[i]` 只指向该顺序第 i 个分句的组号。解码 schema 固定数组长度，
所有分句逐项分配；短句、代码、重复和非教学也不能省略。
程序拒绝缺来源、长度不符、错类型、未知文章条目、未用组号、乱序或非连续合组，
不代替模型判断覆盖、遗漏或非教学。

实际候选由 `course-defiant:q4` 给出，独立复核沿用 `qwen3.5:9b` 的不同权重；
完整原文、相邻语境和实际正文不裁剪。还原后的每组全部成员按原要求独立复核，
任何负面保留为未决，原稿问题与分类冲突不隐藏。顺序表、raw payload、真实缓存和无损派生均保存哈希。
真实请求、解码 schema、模型 SHA、完整 GPU 输入/输出回执继续用原 CoverageJob 核对。
每个原角色最多两次结构尝试、0 次语义重试，不添加写作角色或自动补造判断。

本轮 CPU 实际预检全部 56 个新候选请求，已包含完整原文、正文、有序分句表、实际提示词和格式说明：
最大输入 40,010 tokens、输出预留 12,288、最大请求 context 65,536，均容纳完整输入。
独立审核的真实候选还须在执行时按实际请求再次测量；不能把此预检冒充已发生的 GPU 回执。

## 有限执行与恢复

实际首次 prepare 已完成，覆盖作业为 `05bf20a9e03bff0d09c20bebe3fc463d`，新增推理 0。
准备后的模块已经冻结，不得编辑或格式化；原作者、旧覆盖和运行中的其他任务保持原代码与进程。
71 项新入口及相关回归与 Ruff 通过，包括完整顺序、所有成员、真实双模型/schema/GPU 回执、
实际零推理缓存重放、篡改拒绝、负面未决、原两次额度、父稿单一预算、局部稿拒绝和失败守护阻止尾队列。
这些合成测试不代表本次真实三批完成。

首次有限执行只跑原完整计划的前三批，结束保存 `finite_ordered_coverage_pilot_finished`。
`verify` 从实际缓存和模型收据只读重建该阶段，缺缓存时禁止推理或获取 GPU。
之后 `tail-guard` 只接续原第 4–56 批；先核对原 pilot 的成功退出 guard/worker，
只读认证前三批实际结果，并拒绝任何已经尝试的后续角色。
这是原同一计划和预算的未用尾部，不再次运行前三批推理。任一失败、暂停或温控触发均停止。
整课实际结束后使用 `verify-complete`；不得把有限三批成功当成完整覆盖。

```powershell
$env:PYTHONPATH='src'
$env:PYTHONUTF8='1'
$env:PYTHONUNBUFFERED='1'
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_ordered_coverage `
  --root output/course-knowledge --parent c2240cf9be918a77b74fb0a91f493568 `
  --tokenizer output/course-knowledge/wiki/whole-lesson-narrative-20261104/local-model-tokenizer.json load
```

当前绑定使用原 CLI 的 `__main__` 模块身份，上述操作和独立恢复均使用同一 CLI 身份。
直接 Python import 的模块名称不同，不能把导入得到的 bindings 字典直接代入原契约。
本轮已保留该备份预检失败；经核对仅名称不同、所有执行文件哈希一致，
独立恢复器使用 `runpy(..., run_name='__main__', alter_sys=True)` 实际重放原入口，
不改冻结源码、契约、响应或预算，也不把入口差异说成文件被修改。

私有 guard 在 `wiki/ordered-coverage-dispatch/<覆盖ID>/pilot/` 或 `tail/`，
不会覆盖全局守护或把控制器自己的 PID 当成上一个 worker。
不可变计划、模型请求/响应、派生和结果逐批备份到
`Documents/CourseKnowledgeBackups/whole-manuscript-coverage/<覆盖ID>/ordered-immutable-evidence/`；
这些增量副本不等于完整可移植环境。

GPU 保持 180W 设置、200W 保护上限、70°C 即暂停且不自动热恢复，C/D 各保留 32 GiB。
此模块不发布文章、不删除视频、不把自动覆盖当作事实、图文、课件、自测或用户验收。

## 独立输入恢复及实际首次启动

输入副本 `C:/Users/Administrator/Documents/CourseKnowledgeBackups/ordered-coverage-inputs-20261110-004747`：
706 文件、41,011,030 字节，manifest SHA256 为
`93a249aee11cb54e9c674c2fa17b1402d718d43d1d117b3d73eaea826fa0ff00`。
已从再次复制的代码、原请求/响应、完整材料和有限 SQLite 实际执行原 CLI 身份的 load，
重建 14/14 单元、56 批、14,263 分句、442 完整来源及原 5 项问题，新增推理 0。
13 份旧 group proof 仅按精确文件哈希重定位，有限 SQLite 为 1/302/140 行。
副本不包含模型、视频、全量数据库或完整环境；最初模块身份不匹配的失败也保留。

11 月 10 日 00:50 首次启动 pilot-guard，实际 guard 为 `34828 / 1794243046.4786568`；
00:54 已完成 CPU 认证并进入原第一批候选角色。启动器、venv 转发进程和实际 worker
分别保存身份，不能把启动成功或存活的转发进程算作模型阶段完成。
后续须以真实阶段缓存、模型 wire 收据、成功退出及只读重建作为三批实测结果。

## 01:04 实际三批完成

原前三批已完成 768 分句，75 项未决；guard 成功退出，返回 0。
真实共 8 次模型调用、7 个接受的结构阶段、1 次结构拒绝，原第二次尝试随后通过；拒绝记录保持。
独立模型语义负面并未因此改成通过，三批完成也不能代表全部 56 批完成。

真实 wire 响应合计服务 498.330 秒，其中加载 54.757 秒、prefill 191.161 秒、生成 250.567 秒，
总输出 11,980 tokens；这些是实际模型指标，不含共享 GPU 等待、CPU 重建和备份。
这是另一课的有限实测，不是同一材料对旧格式的受控速度对照，不能宣称整课加速多少倍。
独立只读重建及完整结果副本验证完成后，再由原 `tail-guard` 接续第 4–56 批。

指标文件 `wiki/performance-observations-20261110/1794243999067649900-actual-ordered-pilot-metrics.json`，
SHA256 `5eaa4eb71e1aaa1f652e4691bbb344216997f94bed23ffff2b2187036d9e0b7f`；
同名精确副本已保存在 `Documents/CourseKnowledgeBackups/performance-observations-20261110/`。

实际结果副本 `C:/Users/Administrator/Documents/CourseKnowledgeBackups/ordered-pilot-results-20261110-010504`：
manifest SHA256 `3e9030f3304d8520c6672b7680b94be4b007eb582d6391ad52310d004655edf0`。
实际从独立源码、完整父稿材料、真实候选/审核请求和回执，零推理重建 3 批、768 分句、
7 个实际阶段及原 5 项问题；13 份 legacy group proof 按原哈希一对一重定位。
该 manifest 有 1,041 条目；路径键 Windows/POSIX 别名审计证实仅为 744 个不同文件、
41,618,151 字节，297 个别名均指向相同真实字节。原 manifest 未修改，
审计收据另存 `manifest-path-audit.json`，不能把别名条目数当作不同文件数。
副本保留完整有限输入/结果和有限 SQLite，不含模型、视频、全量库或完整环境。

结果副本 `restored/workspace` 内设置 PYTHONPATH 为其 src，可只读复现：

```powershell
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/ordered-pilot-result-restore-20261110.py `
  output/course-knowledge 'D:\Project\WeChatDataAnalysis\output\course-knowledge' `
  c2240cf9be918a77b74fb0a91f493568 `
  output/course-knowledge/wiki/whole-lesson-narrative-20261104/local-model-tokenizer.json
```

01:09 已首次派发原 `tail-guard`，01:10:55 实际 guard 开始守护原尾队列。
它仍须在真实 worker 内重建前三批，再使用原第 4–56 批角色，不重复前三批推理。

## 01:25 第六批失败，尾部不可按成功状态接续

原尾部实际新增完成第 4、5 批，累计 5/56 批、1,280 分句。
第 6 批 `batch-0005-body-0000-review` 的两次原结构尝试随后耗尽，
实际错误均为 `requires_coverage_assignments_contiguous_used_source_groups`。
实际 worker 和 guard 已退出，guard 返回 1；不能继续使用此前的 running 状态。

两份响应均正常 stop。零推理形状检查确认：该批 18 个来源、256 分句中，
16 个来源各只输出一个组，254 个分句的数组值在真实 groups 数组之外，另 2 个索引有效。
还含部分不单调序列。最初口头将整批 256 句当作越界数，已按完整响应纠正。
这不是无成员空组可删除的问题；不能把所有编号改成 0，假称原模型已逐句作出覆盖判断。
两次原 review 预算已消费，未创建接受缓存，没有重跑提案或复用成功批次。
独立 verifier 和后续第 7–56 批尚待合法接续，失败材料与完整目标范围继续保留。

形状诊断 `wiki/performance-observations-20261110/1794245546072881000-ordered-failed-assignment-shapes.json`，
SHA256 `f31139ef7e3c82f18441276c1c3c066fcf7c15b005cafda177c125512f5c639b`，
精确副本在原 performance-observations 备份目录。
01:37 已实际零推理重建前五批：1,280 分句、75 未决、11 个真实接受阶段。
同时认证第六批两份完整失败的实际请求、schema、模型摘要和 GPU wire 收据，
并重新复现同一结构错误；原 stopped 状态、失败及两次额度均保持，没有生成接受缓存。
只读适配仅将导入时模块键改为原 CLI 的 `__main__`，每个冻结源码哈希仍严格相同。
证明为 `wiki/ordered-stopped-prefix-proof-20261110.json`；
这不是全部覆盖或语义验收。

01:46 已完成新增完整有限结果副本的实际独立恢复：
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/ordered-stopped-results-20261110-014349`。
规范化路径清单为 776 个不同文件、42,072,517 字节，manifest SHA256：
`afdac4e2d4d5716b4c6af62923899d1074cc74b20ed468d65125a538fb52fd42`。
从再次复制的独立代码、完整父稿有限材料和真实请求/响应重建同一前五批及两次失败；
13 份 legacy group proof 只按原哈希一对一重定位，禁止原根目录回退，新增推理 0。
不含模型、视频、全量库或完整环境；旧副本、原失败和未决保持。

副本 `restored/workspace` 内将 PYTHONPATH 指向该副本的 src，可只读复现：

```powershell
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/ordered-stopped-result-restore-20261110.py `
  output/course-knowledge 'D:\Project\WeChatDataAnalysis\output\course-knowledge'
```

原尚未调用的第六批独立审核及第 7–56 批，已准备为
[未用审核与边界尾部](course-ordered-boundary-tail.md)，通过实际独立输入恢复后首次派发。
原两次失败、254 个无效映射的待核义务和全部原预算保持，不生成接受的失败提案缓存。
