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
