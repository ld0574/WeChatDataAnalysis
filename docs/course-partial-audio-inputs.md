# 两份局部恢复音频的独立输入备份

2026-11-10 对两份带缺口的音频执行了真实输入核验和独立副本恢复，没有进行 ASR、
模型调用、数据库写入或修改旧文件。它们仍是全量任务中的明确待处理来源，
没有被当作完整音频放入 273 份新来源队列。

| 来源 ID | 保留 PCM 片段 | 缺口数 | 可读秒数 | 缺失秒数 |
| --- | ---: | ---: | ---: | ---: |
| `de68a35e4bf20ca2c1e6b498` | 5 | 5 | 7,150.5906875 | 1,729.696 |
| `3ae61f644efa2308f0b6aff4` | 4 | 4 | 6,750.6546875 | 2,609.6413958333333 |

核验重新哈希了保留 AAC、全部 9 份 PCM、样本数、绝对时钟来源和缺口清单。
独立恢复核对了 855,029 个保留 AAC 包；原根目录读取 0，模型调用 0。
这些结果不能证明原音频完整，也没有消除缺口。

成功副本位于
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/partial-audio-inputs-20261110-031933`。
315 文件、643,345,634 字节，manifest SHA256 为
`a9cb8a0f355a7dccceca1f401c2a761f22db183717e383ba5c611a1fd98f38b3`。
包含保留 AAC、9 份恢复 PCM、原缺口证据和执行源码；不包含原视频、模型或完整环境。
本次证据接口只依赖独立文件根和 checkpoint，不需要 SQLite，也没有创建占位数据库。

只读证据与实际恢复收据保留在本机：

- `output/course-knowledge/wiki/partial-gap-audio-input-validation-20261110.json`
- `output/course-knowledge/wiki/partial-audio-input-backup-20261110.json`
- 副本中的 `restore_partial_audio_inputs_20261110.py` 为实际使用的独立核验入口。

后续应使用既有 `course_gap_asr` 按每份片段分别转写、保留原视频绝对时钟，
明确标为 `transcribed_with_gaps`，不能拼接后伪装成完整音轨或填补缺失文字。
调度必须服从现有来源控制器与共享 GPU 锁；本次未启动第二条抽取队列。
另一个带验证音频和修复留件的来源 `4fb7bdb5c845984c56e8fa11` 仍独立待恢复，
未被本次两份音频备份或核验覆盖。原视频不得删除，全量 Goal 保持 active。
