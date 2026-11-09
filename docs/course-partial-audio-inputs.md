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

## 第三个排除来源的实际输入核验

03:28 对 `4fb7bdb5c845984c56e8fa11` 另做只读核验：保留的完整 AAC 文件字节、
534,363 个包、534,361 个解码帧、547,185,600 个样本及时间线与原 manifest 全部一致。
旁边的 `audio-copy-493y7ex3` 只含原 AAC 和修复副本，后者与正式 `audio.m4a` 字节相同；
全部留件保持，没有删除临时目录来绕过原队列的输入范围检查。
本次只核对原视频 stat，没有重新哈希原视频，不能将此称为原件全量重验。

三个排除来源均仍未注册，明确不在原 273 份新来源中。两份缺口音频分别生成
289、272 个独立窗口，所有样本恰好覆盖、每个窗口只属于一个恢复片段。
私有输入预检为 `output/course-knowledge/wiki/excluded-audio-input-preflight-20261110.json`；
新增 ASR、模型调用及数据库写入均为 0，未另启抽取控制器。

03:31 第三个来源的 AAC 输入副本也已实际独立恢复通过：
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/verified-excluded-audio-input-20261110-033117`。
303 文件、118,972,825 字节，manifest SHA256 为
`0aff27976a4d32ed8ac2c3119b25ec24bfe08ca336d022465c0664ed95db4582`。
从再次复制的源码、AAC、原 manifest 和预检收据重新核对全部字节、包、PCM 与时间线，
原根目录读取 0、模型调用 0。此副本不含原视频、修复留件、模型、SQLite 或完整环境；
原目录的两份修复留件和哈希记录仍保留，不将有限音频输入恢复当作全流程恢复。
实际恢复入口为副本中的 `backup_verified_excluded_audio_20261110.py verify`。

四个已注册的 pending 来源另有替代录制音轨，不属于上述三个未注册来源，
也没有被原 273 份队列重新选中；仍需单独核对其原恢复绑定与未完成转写，
不能将“已注册”当作“已转写”或“文章已完成”。

## 四个已注册替代音轨的实际核验

03:36 完成四份真实输入复核，来源为：

- `6c9fc957f13349c3e200bc16`
- `68ffd2103bdc5ffb0d1a8bc3`
- `dd922cfa90efa34b71333b0d`
- `0127af4205930cb669bd583f`

均保持 `pending`，已提交 segments、frames、notes 各为 0；该数字不能证明历史模型从未调用。
逐份重新哈希替代音轨与保留的损坏原音轨，核对两者全部 AAC 包、替代音轨的 PCM/绝对时钟，
并由原 `transcription_audio` 只读认证已有 `asr-audio-source.json`，禁止创建新绑定。
四份共保留 4,041,990 个包，替代音轨解码样本 2,069,490,432；输入文件复核前后哈希相同。
原视频和替代录制文件只核对 stat，未重新全文件哈希；损坏原音轨没有改称完整或有效。

私有收据为
`output/course-knowledge/wiki/registered-alternate-audio-input-validation-20261110.json`。
本次模型调用、ASR、数据库写入均为 0，没有另启来源抽取或改变已冻结队列。
这次是主目录输入核验，没有制作或声称四份材料的独立完整恢复副本。
七份特殊来源继续纳入全量剩余工作；未来必须遵守原绑定、历史请求及共享来源排他锁，
不能按全新来源刷新预算，也不能和原健康抽取竞争第二条控制器。
