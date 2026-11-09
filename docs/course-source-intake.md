# 已发布来源的首次整条原文输入

`course_textbook_source_intake` 在 CPU 上准备尚无整课输入的可用课程，沿用完整原记录、来源编号、
时间范围和画面哈希，不调用模型，不创建章节，不改写旧输入、预算、失败、课程目录或已交付文章。
全量范围仍为 340 节有声课、2 份无声来源及所有可恢复课件。输入准备不证明教学准确、覆盖或课件归属。

## 实际结果

2026-11-08，原冻结来源驱动只补了此前未建输入的 7 节，保留原有 47 节：
3,061 条完整原记录、1,388,191 字符，备份 25 文件、16,464,200 字节。
批次 `f660bdf79e7e427ce6e9a0c800327eb1ab720b30002afe378df5db769da8ad3e`，
副本位于 `C:/Users/Administrator/Documents/CourseKnowledgeBackups/ready-whole-lesson-inputs/` 下同名目录。
旧驱动未修改，原失败与已完成批次未重跑。

新的整条输入批次为 `6e008c48608422cd89f013516656745ac4c1ffb7e32d46649db10841c0398d9b`。
54 节当前可用来源中，保留 2 节已有整课输入和 1 节已交付文章，首次补齐其余 **51 节**：
18,516 条完整原记录、7,771,168 字符。调度只将整条原记录分组，不把调度组当作文章章节。
实际新增模型调用、成稿和发布均为 0；286 节来源尚未发布，所有课件的关联与未决项继续保留。

## 外层来源版本与教学输入分别认证

第一次预检因当前来源对象与旧输入的外层哈希不同而停止，尚未创建整课输入。
原代码、失败和错误码保留在 `wiki/source-intake-preparations-20261108/1794099332025694500/`。
随后完整比较了 44 节有差异的版本，全部原记录逐项相同，包括完整文字、顺序、来源编号、时间范围和画面哈希。
比较记录为 `wiki/source-intake-preparations-20261108/1794099389321687400-original-equivalence.json`。

新入口仍核对实际当前来源对象的字节 SHA，再完整投影其原文，与旧输入逐条比较。
只有全部原记录相同时，才能明确记录外层对象不同但教学输入相同；旧输入的版本、原文和哈希均不替换。
不声称原始导出字节相同。任一原文、时间范围或画面哈希变化、来源对象缺失或多份候选无法唯一选择，立即拒绝。
新创建的每份整条输入再次从父输入实际重建，全部旧输入文件的 SHA 在每步和终态核对不变。

17 项测试与 Ruff 通过，覆盖无模型调用、全量目录、无原文拆分/丢失、旧输入和预算保护、
外层版本变化的严格等价、文字/时间/画面哈希变化拒绝、错误成功声明拒绝、只读核对、停止标记和空间保留。
这不代替任何课程的语义审核。

## 使用与恢复范围

```powershell
$env:PYTHONPATH='src'
$env:PYTHONUTF8='1'
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_source_intake `
  --root output/course-knowledge run
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_source_intake `
  --root output/course-knowledge verify `
  --batch 6e008c48608422cd89f013516656745ac4c1ffb7e32d46649db10841c0398d9b
```

当前批次已完成。再次 run 没有新目标时会拒绝，不重复准备已验收输入。
verify 只读取固定目录、完整原文、来源对象及原先保留文件；不需要模型服务，也不运行 checkpoint、写作或发布。
输入和副本均包含私有材料，不应 git add；只提交代码、测试及本文档。

独立恢复只证明有限文字输入、来源映射及代码可再次实际核对。原始画面仅保留哈希关系，
未在本副本复制所有画面、视频、音频、数据库、模型或 Python 环境。运行依赖仍来自主虚拟环境，
不能称为全量归档、全处理环境恢复，或模型队列可直接迁移运行。

本批独立副本为
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/whole-source-intake-20261108-090115`，
488 文件、122,224,669 字节，manifest SHA256
`cbc095396813042945199b3f52a9e97d589e2355f7d044c15e821c14f85f6e11`。
已再次复制为 `restored/workspace`，逐文件核对字节 SHA，并从该独立源码实际 verify 全部 51 份新输入，
与原结果完全一致，新增推理为 0。它还保留旧输入哈希、完整当前来源对象、初次失败、原冻结驱动及新增七课的材料。
此副本的 verify 使用 `restored/workspace/src` 作为 PYTHONPATH，root 指向
`restored/workspace/output/course-knowledge`；Python 与已安装依赖仍使用主 `.venv/Scripts/python.exe`。

## 2026-11-10 新完成的三份来源

首次接入三个此前没有父输入的来源：`427195b4c6efeb4e281ed5cd`、
`6b35a96a8ceb2a9f76e5cf00`、`9f50e2b95d006a7f4db47bb8`。
新增整课输入批次 `3407e8dda9695eddfb24cf7031f7c3bcbda7e9fe503f0a77820be8a8146013bc`，
1,463 条完整原记录、527,590 字符；保留原有 53 节整课输入和 1 篇已交付文章。
当前可用来源 57/340，整课输入 56 节；这些输入不等于 56 篇文章。283 节尚无完成来源，
2 份无声来源及全部可恢复课件继续属于全量目标。

实际执行记录为 `wiki/new-source-intake-executions/20261110-three/`。
所有旧输入逐文件 SHA 不变，未调用模型、未创建账本、未发布文章；课件关联和语义检查仍未完成。
私有驱动 `wiki/continue_source_intake_20261110.py` 包含本次补齐和独立恢复方法，
本次 `run` 已执行，不得重跑；只读恢复使用其 `verify` 命令。

独立副本：
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/three-source-intake-20261110-062125`，
438 文件、103,667,717 字节，manifest SHA256
`9b0aba07852f9285b34ec0653761ade10dfef3eb89fee9c014b34df0339763e0`。
`restored/workspace` 中的源码已实际核对同一三份结果；审计钩子禁止读取原归档、数据库、网络和推理，
原归档读取与模型调用均为 0。恢复时进入该目录，以其中 `src` 为 PYTHONPATH，
使用主虚拟环境 Python 执行：

```powershell
& 'D:/Project/WeChatDataAnalysis/.venv/Scripts/python.exe' -B `
  output/course-knowledge/wiki/continue_source_intake_20261110.py verify `
  output/course-knowledge D:/Project/WeChatDataAnalysis/output/course-knowledge `
  3407e8dda9695eddfb24cf7031f7c3bcbda7e9fe503f0a77820be8a8146013bc `
  C:/Users/Administrator/Documents/CourseKnowledgeBackups/three-source-intake-20261110-062125/manifest.json
```

副本仅证明有限完整文字输入、来源映射及代码可重建；没有复制音视频、全部画面、模型、
数据库或 Python 环境，不能据此删除原视频。
