# 完整原文作者的段内知识点覆盖与有限恢复

`course_textbook_point_mapped_resume` 恢复已真正停止的 fresh 整课作者。
原 frozen writer、fresh recipe、失败记录、缓存和预算均不改写。
此入口只生成内部稿，不能发布、删除视频或宣告 Goal 完成。

## 观察到的实际故障

作业 `c2240cf9be918a77b74fb0a91f493568` 在 7/14 内部单元后停止。
第八单元 `section-0007-write` 的两次完整响应分别只有 5、6 个段落，
却把 11 个知识点依次映射到 0–10；这是实际越界，不是统一一基编号。
两次原响应和错误均保留，禁止偏移、截断或把越界编号 clamp 到末段。
原 worker `2024 / 1794095459.2981565`、原 dispatch `10560 / 1794087035.9188654`
已退出；原 guard 为 returncode 1。

前七单元已从实际完整请求、schema、响应和 GPU 回执零推理重建，仍有一项未决。
原 prefix proof SHA 为 `ad8507954932938a52828d3676bf783b30df7d8c43419de01bd2ee9d33c89316`。
真实检查确认本单元的 review、repair 和 repair-review 尚未使用。

## 恢复方式

1. 再认证原计划、原任务分配、前七单元、两次失败的真实请求及解码 schema、GPU 回执。
   选择最早完整响应作为**未信任审核输入**，不伪造成功的 write cache。
2. 使用原尚未使用的 review，由不同本机权重检查完整原文和失败候选。
3. 无论首次事实审核是否通过，都强制使用原唯一 repair。
   解码格式改为每个实际段落包含合法 `point_ids`，coverage 保留 reason 和适用的 teaching 判断。
   程序按实际数组位置派生索引；不猜测或更改模型指定的知识关系。
4. 原 repair-review 对修订稿及全部完整原文独立复核，负面结果保留。
   该稿只是私有候选，完整来源覆盖、课件、阅读编辑和自测验收仍需完成。
5. 主作者的该单元明确映射到实际 repair 和 repair-review，不新增模型调用。
   后六个尚未调用单元的 write/repair 同样使用段内格式，避免再次输出不存在的段落编号。

每角色结构尝试上限仍为 2，每单元语义修订仍为 1；不更换 recipe 刷新额度。
段内 ID 受真实任务 enum、唯一性和完整结构约束。空教学映射、错误教学排除、越界引用、
缺少主要原文支持、重复正文、非法图等仍拒绝；引用元数据建议依旧不构成事实证据。
原始段内输出与派生稿分别保存，独立审核明确收到未验证元数据，不能由程序标为通过。

## 执行与复核

```powershell
$env:PYTHONPATH='src'
$env:PYTHONUTF8='1'
$env:PYTHONUNBUFFERED='1'
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_point_mapped_resume `
  --root output/course-knowledge --inventory 46f76c56fece32f33aff60f7768f8f4317d8b9ff0b8e4d586cddca54099d51a8 `
  --ledger-job 6039c4d9ff107162b617c0295ff15549 `
  --tokenizer output/course-knowledge/wiki/whole-lesson-narrative-20261104/local-model-tokenizer.json prepare
```

prepare 一次后，用相同模块的 `--root ... --job c2240cf9be918a77b74fb0a91f493568 --tokenizer ... load`
认证恢复输入。实际执行只能经 `guard`；`prefix`、`verify` 为零推理重建。
verify 仅在完整作者真正退出并组稿后使用，prefix 也必须已有实际原三角色结果。
worker 与私有 guard 都有一次性标记，失败后不重复运行相同命令。

恢复资料在原作业内的 `point-mapped-resume/`：execution、原停止状态、原角色结果、
显式编排映射及私有 guard。标准 guard 和原失败文件保留不覆盖。
180W 设置、200W 保护上限、70°C 暂停且不自动热恢复、C/D 各 32 GiB 保留线继续执行。
用户本次明确确认恢复 GPU；原温控暂停历史不删除。

40 项相关测试及 Ruff 通过，包含真实段落位置、合法 ID、排除与缺失覆盖、原引用范围、
原始输出不变、强制修订、独立负面保留、只读缺失拒绝、hook 异常还原和原 fresh 预算检查。
测试使用合成数据；真实模型处理的质量仍由实际完整证据与审核决定。
冻结适配器 SHA256：`2edbfee1fbb5ed3bf837f82a2fca94105b17280117c77b19562c942bc27babb8`。

## 实际输入恢复检查

第一份有限副本保留了旧账本的绝对路径校验失败，没有删除或冒充成功：
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/point-mapped-inputs-20261108-092531`。
随后增加只读恢复包装器，仅将原 group proof 中的角色文件绝对路径一对一定位到新目录，
逐份校验原字节哈希，不修改原 proof、内容、schema、预算或语义结果。

成功副本：`C:/Users/Administrator/Documents/CourseKnowledgeBackups/point-mapped-inputs-20261108-092734`，
541 文件、34,676,195 字节，manifest SHA256
`2b42812345a800f38cbfba046267a38440c210949a57edc21a69b1ca433f7d06`。
再次复制的独立源码及材料已实际 load，重建相同 7 单元及原失败；13 份旧 group proof 完成严格路径核对。
有限 SQLite 实际保留 videos/segments/frames 的 1/302/140 行，quick_check 通过。
不包含全量数据库、媒体、模型或独立运行环境，仍依赖主 venv 与当前本机模型目录。

恢复时在 `restored/workspace` 中使用以下只读命令，不运行 guard/worker/prepare：

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
$env:PYTHONUTF8='1'
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/point-mapped-restore-20261108.py `
  output/course-knowledge 'D:\Project\WeChatDataAnalysis\output\course-knowledge' `
  c2240cf9be918a77b74fb0a91f493568 `
  output/course-knowledge/wiki/whole-lesson-narrative-20261104/local-model-tokenizer.json load
```

09:28 启动器为 `4472 / 1794101310.043406`，其实际私有 guard 为
`25828 / 1794101310.197748`。guard 的 worker 启动器为 `28284 / 1794101343.2365217`，
实际 Python worker 为 `12336 / 1794101343.2760408`，已进入 `section-0007-review` 的共享 GPU 等待。
输入恢复通过只证明可以准确继续，不代表修订、完整组稿或课程质量已通过。

原 writer 的 24 次真实响应累计请求内耗时 1,581.530 秒，其中模型加载 145.644 秒、
prefill 258.855 秒、输出生成 1,174.039 秒；实际输入/输出为 445,371/63,419 tokens。
原进程开始到写作失败约 73.5 分钟，请求内约 26.4 分钟，其余包含共享 GPU 等待、
CPU 校验与备份，不能全部归因于等待，也不能把请求速度当作全量端到端课程 ETA。
本次格式更改与保留已完成单元用于减少无效重写；真实提速需在后续实际完成后比较。

## 09:38 后续实际失败

原第八单元 review 已实际执行，repair 两次完整响应通过新解码 schema，
但三项有效教学没有段内标注，转换回原覆盖结构时被拒绝。
这不是恢复成功：原两次 repair、ValidationError 和私有 guard 的 returncode 1 均保留，未再写作。
原 guard/worker 已退出；剩余原最终审核的有限去向及新的实际守护见
[遗漏标注提案与原最终审核](course-omitted-mapping-review.md)。
