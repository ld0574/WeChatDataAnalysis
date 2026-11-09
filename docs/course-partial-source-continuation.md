# 部分来源的单次剩余抽取

`course_source_partial_continue` 只接续已有来源 `2f239df40b6a630ea9e531a3`：
保留 316 段转写与 45 张已认证 OCR，处理其余 178 张已有画面和未用来源笔记窗口。
不重做 ASR、捕获画面或失败帧，不改变原模型和缓存策略。
另一份 `84ef110ea186f81ea9041ee0` 有明确终止失败，不适用本入口。

实际执行 `bcef1caa1905d02aa5a00d7602164fb1` 已准备并派发，
目录 `output/course-knowledge/wiki/partial-source-continuations/<job>/`。
它等待准确的原七来源队列 `53d6d7aaa1503cb83761080577a14bfe` 成功退出，
逐项验证七份完整导出后才获取共享来源锁；等待期间不占来源锁或 GPU。
前驱失败或暂停会保留并停止，不会自动重跑。来源单独登记防止重新 prepare 刷新额度。

继续使用共享 GPU 锁、现有独立守护、70°C/200W 与每盘至少 32 GiB 保护。
正常完成仍须核对原转写及成功 OCR 字节不变、全部画面状态、完整来源导出和发布对象。
现在的等待状态不等于剩余 178 张或文章已经完成。

输入独立副本：
`C:\Users\Administrator\Documents\CourseKnowledgeBackups\partial-source-inputs-20261110-064638`。
637 文件、58,955,372 字节，manifest SHA256：
`a4b2e23f9520dfc90ba66668cb5f5028d400bd69e280469f293b1631c79bc915`。
已从复制源码重建真实有限数据库，quick_check 通过：1 来源、316 段、223 帧、0 笔记。
223 张原画面和 45 张完整成功缓存均在；原归档读取 0、新增模型调用 0。
副本不包含音视频、模型、运行环境或未来前驱结果，不能视作完整归档恢复。

恢复位置为该副本 `restored/workspace`，PYTHONPATH 指向其中 `src`：

```powershell
python output/course-knowledge/wiki/backup_partial_source_inputs_20261110.py verify output/course-knowledge D:\Project\WeChatDataAnalysis\output\course-knowledge bcef1caa1905d02aa5a00d7602164fb1 ..\..\manifest.json
```

原始只读逐帧认证与已知例外见 [部分来源断点](course-partial-source-status.md)。
