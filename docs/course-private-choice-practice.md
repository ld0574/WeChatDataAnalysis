# 未发布整课稿件的选择题

`course_textbook_private_practice` 从已经完整组装且实际退出的整课作者获取私有阅读投影，
不再要求文章先进入读者目录才能出题。它先零模型重建完整来源、原角色、实际请求与 GPU 回执，
绑定文章 SHA、父稿快照和代码。部分写作前缀不能作为输入。

出题复用 `course_textbook_practice_build.Job` 的同一作业身份、模型和原角色：
`choices-write`、`choices-review`，必要时使用唯一的 `choices-repair` 和 `choices-repair-review`。
每个角色最多两次结构尝试，没有额外语义循环，也不刷新旧作业额度。题数为 1–5，默认提示词要求
五题、优先三道单选与两道多选。支持引文仍由程序绑定到实际文章小节，模型不能编造引文。

私有结果只写入 `wiki/textbook-choice-quiz-jobs/<作业>/private-practice.json`，不会调用
`practice.store`、写读者目录或创建 `<文章SHA>.current.json`。通过的测验也不赋予正文覆盖、
课件关系、编辑或用户验收。读者端仍沿用提交全部选项后才显示答案与解析的现有行为。

## 当前第二课入口

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_private_practice `
  --root output/course-knowledge `
  --parent c7258efff888d77a26e1234dd153ef7a `
  --tokenizer output/course-knowledge/wiki/whole-lesson-narrative-20261104/local-model-tokenizer.json `
  --adapter disposition --section section-0007 prepare
```

`prepare` 只认证输入和登记原作业，不调用模型。首次绑定后执行文件不可再编辑或格式化。
其他原始整稿用 `--adapter original`，仅经原接续恢复的整稿用 `continuation`。
第二课必须使用 `disposition`，保留第十五单元的实际映射恢复链。

待全文覆盖与实际修订确认正文稳定后，才将末尾命令换成 `guard` 生成私有选择题，避免为仍会改写的
正文消耗出题预算。守护使用共享 GPU 锁、70°C 暂停且不自动热恢复、200W 保护上限和双盘各
32 GiB 保留；健康的同槽守护或 supervisor 停止标记会阻止启动，原守护记录逐字备份。

成功后可用 `verify` 从原缓存只读重建题目、程序绑定来源、独立复核、完整请求、解码 schema、
实际模型及 GPU 回执。缺缓存时禁止推理，任何改动都会拒绝认证，不重写原进度或刷新预算。
内部题目、正文及审核理由不输出到终端。

## 发布边界与验证

此入口不提供发布命令。最终阅读版需由实际完成的全文覆盖、事实、课件关系和编辑核验入口发布；
正式接入测验时必须与最终文章 SHA 逐字匹配，不能以此私有结果调用低层 `store_book` 绕过验收。
正文有实际修订后，旧题库仍绑定旧稿，不能自动跟随新稿。

本轮私有流程、选择题、映射恢复和覆盖只读验证共 60 项合成回归及 Ruff 检查通过，包括：
完整私有流程和零推理重建、不提前发布、一次修订后的负面终态保留、模型/请求/schema 篡改拒绝、
缺缓存拒绝和停止/健康进程保护。这些测试不代表第二课真实测验已生成。

成功的私有结果与原模型证据增量备份到
`Documents/CourseKnowledgeBackups/private-choice-practice/<作业>/`；这不是完整模型环境副本。
