# 新完成来源的有限结果恢复

来源 `9f50e2b95d006a7f4db47bb8` 已由原控制器完成导出与内部来源目录发布。
此次结果副本包含原始转写、来源笔记、图、采样画面、实际发布对象、完成收据、
真实有限数据库行及恢复使用的源码；它不是新 Wiki 成品或全部知识库副本。

副本：

`C:/Users/Administrator/Documents/CourseKnowledgeBackups/completed-source-result-20261110-054702`

686 文件、85,386,048 字节，manifest SHA256：

`17dd038b497ed96681ac4c0d132a78bf723c6a03e29301114ee1e846fbe75daf`

再次复制后从副本源码实际运行原导出校验器和 Library：1 来源、328 段、183 帧、55 个笔记窗口，
逐项从真实行重建导出和发布对象；183 张画面共 36,806,696 字节，与发布媒体字节相同。
有限 SQLite quick_check 通过，原归档读取 0、模型调用 0。
保持实际发布对象 SHA256：

`d8fd2c832b08d8ae228ea0dc73ccb224302768fbf04c1be0de3eac8c3a381e0b`

副本的目录只作为原始上下文快照保留；未复制其他 56 份来源对象，不宣称完整目录阅读恢复。
不含原视频、音频、模型或完整运行环境，仍使用主虚拟环境依赖。
采样帧与来源导出一致也不表示完整画面覆盖、识别正确率或文章质量通过；源视频不能删除。

恢复时切到副本 `restored/workspace`，把 PYTHONPATH 设为其中 src，执行：

```powershell
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/backup_completed_source_result_20261110.py verify `
  output/course-knowledge 'D:\Project\WeChatDataAnalysis\output\course-knowledge' `
  9f50e2b95d006a7f4db47bb8 '<副本根目录>\manifest.json'
```

核验禁止原归档回退、网络、推理和外部媒体工具调用；只输出数量、ID、哈希和状态。
本机成功回执位于 `wiki/completed-source-result-backups/9f50e2b95d006a7f4db47bb8/`。
