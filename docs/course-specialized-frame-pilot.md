# 专用 OCR 草稿的有限原图核对

这是独立的 PP-OCRv5 试点，不是 Windows OCR 试点的重新运行。旧作业
`72d2abc1d62c22befc894c212652d442` 的三帧负面、四次实际模型调用、注册和预算继续保留。
失败帧 6881 及其两个恢复作业也继续保留。没有用新配方刷新旧 GPU 角色。

## 新输入与实际 CPU 准备

新作业 `d21160908829551e97c79eb84cc96830` 只包含来源
`84ef110ea186f81ea9041ee0` 的 6892、6936、6980 三张完整画面。
准备时精确查询本来源的帧及注册，核对 pending、空 model、无旧 OCR cache；
还排除已尝试/已就绪帧的相同 RGB 像素别名。三张原图和标准 PNG 均不同。

实际 CPU 工具沿用冻结的 `tools/course_specialized_ocr.py`，不修改原环境。
本次三张返回 73、44、79 行，共约 38.547 秒；全部低置信度行、原始四边形与置信度保留。
输入和输出位于 `output/course-knowledge/wiki/specialized-ocr-20261108/fresh-frame-drafts`。
它们不是已核对的课程证据，也不是新增可阅读文章。

新适配器明确使用 `PP-OCRv5-server/RapidOCR-3.10.0/ORT-1.28.0-CPU` 来源，
不伪造 Windows `words` 收据，不制造旧 Qwen 成功缓存。只复用冻结 Windows 试点的
纯请求、schema 和响应检查函数；没有替换其模块全局变量或改写任何绑定源码。
实际 GPU 请求包含完整原图、所有草稿行、低分及原坐标，禁止裁剪上下文来适配预算。

## 一次性预算与运行

每帧最多纠错一次；只有完整文字及图示标志均为 true，才进行另一次原图核对。
两次均使用本机 `qwen3.5:9b` 相同权重，不是跨模型或人工审核。
结构错误、服务失败及语义否决全部保留，不自动重试。
试点没有数据库写入或文章发布入口；支持数只能作为后续接入的参考。

```powershell
$env:PYTHONPATH='src'
$env:PYTHONUTF8='1'
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_frame_specialized_pilot `
  --root output/course-knowledge guard --job d21160908829551e97c79eb84cc96830
```

上面的一次性启动已于 07:45 实际执行，不可再次运行。准确 guard 为
`8412 / 1794095114.5620613`，实际 worker 为 `21388 / 1794095115.892054`。
本试点使用自己目录内的 guard 状态与日志，未占用四个已有标准 guard 文件。
模型请求仍等待共享 GPU 锁；180W 配置、200W 保护、70°C 暂停且不自动热恢复，
C/D 各保留 32 GiB。已有控制标记须备份并保留，不能用冷却或失败来刷新预算。

真正 worker 退出后，只读重建命令为：

```powershell
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_frame_specialized_pilot `
  --root output/course-knowledge verify --job d21160908829551e97c79eb84cc96830
```

输出仅包含状态、数量和耗时。所有文字、请求、响应与审核理由留在本机私有目录，不能提交到 Git。
源码在准备后冻结，SHA256 为
`dbac0794467052895955de613389f462172ad70446c6bbbea5c8da6d0a1efc4c`。
17 项测试及 Ruff 通过，覆盖低分保留、旧角色/相同像素排除、负面保留、只读重建、
输入变更拒绝、故障脱敏、温控及双盘预检、暂停标记和其他 guard 保护。

## 已验证的有限输入恢复

输入及代码备份：

`C:/Users/Administrator/Documents/CourseKnowledgeBackups/specialized-frame-inputs-20261108-074456`

267 文件、27,328,330 字节，清单 SHA256：
`e24b398c30a80ae1e252c40452b8d3fae8bd74db67893cc6c45d3b31c8baf7eb`。

实际再次复制到独立 `restored/workspace`，逐文件核对 SHA，然后由该副本的 Python 源码
在原虚拟环境中零推理 load，重建相同三张图、全部草稿、注册、排除记录及 tokenizer。
这仅验证有限输入和代码恢复；未声称包含全量数据库、原视频、所有模型或完整运行环境。
专用 OCR 两个模型及 21 项离线 wheel 的独立安装/实际运行恢复见
[专用 OCR 离线草稿](course-specialized-ocr.md)。

## 速度证据及后续范围

第二课现有完整覆盖作业 `4c7009067249cc972b363551ea0244c8` 的一次只读采样显示，
73 份实际请求收据累计约 2,760,642 输入 token、218,549 输出 token；模型报告总服务时间
约 6,783 秒，其中生成约 4,585 秒、预填充约 1,713 秒、加载约 468 秒。
这些是服务收据统计，不等于 worker 墙钟时间，也不代表整课验收完成。

后续课程需要同时减少逐字视觉生成和重复长正文输入。不能修改正在运行的绑定任务；
必须在未来新任务的原始预算内，验证分组和来源映射没有遗漏，再比较实际总耗时及质量。
本次 CPU OCR 时间与旧 GPU 试点不构成同输入、同验收标准的提速倍数。

## 实际终态与独立重建

约 07:55，三帧实际 worker 和 guard 均已退出。只读重建结果为 **0 supported / 3 unresolved**，
数据库写入数为 0。四份真实模型响应的服务时间共约 98.499 秒，worker 墙钟约 601.977 秒；
后者包含共享 GPU 等待、预检和本地处理，不能全算作模型推理。

- 6892 的完整文字标志为 false，且纠错列表重复使用行 ID，按结构错误保留。
- 6936 的完整文字、完整图示标志均为 false，未继续消耗复核角色。
- 6980 的纠错完整标志为 true，但第二次原图核对否决像素支持、无无据文字及代码布局三项。

没有追加这些角色的重试。原 Windows 试点也再次零推理重建为 0/3，原注册和负面未变。
专用 OCR 的合成校准改善没有转化为本试点的真实原图核对通过，暂不批量接入或发布。
这些结果也不能区分原始 CPU 草稿问题和视觉纠错新增问题；没有真实人工逐字标准答案，
不能据此宣称专用 OCR 的真实课程错误率或把失败归因于某一个组件。
后续先推进已有文章覆盖及未决项，若继续研究识别链路，须先做已知答案的受控校准，
不能继续在本批失败帧上更换配方刷新原 GPU 预算。

终态及有限数据库行备份：

`C:/Users/Administrator/Documents/CourseKnowledgeBackups/specialized-frame-result-20261108-075527`

294 文件、27,373,525 字节，清单 SHA256：
`e6fd34556693c653a3a83fea84193fd8ad55164bf9c01a318f5d6c77a08ef2f6`。

该副本包含精确代码、三张原图/PNG、全部 CPU 草稿、实际模型收据、注册、一次性预算、
失败及终态，并单独保存本来源和三帧的四条数据库记录。实际再次逐文件复制到
`restored/workspace`，使用恢复的源码、输入和有限数据库零推理 verify，得到完全相同的
0/3、四次角色启动和服务耗时。此验证使用原 Python 虚拟环境，不是独立安装完整环境；
有限数据库不是完整知识库，不能替换生产数据库。恢复核验方式为：

```powershell
$env:PYTHONPATH='<该备份目录>\restored\workspace\src'
Set-Location -LiteralPath '<该备份目录>\restored\workspace'
D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_frame_specialized_pilot `
  --root output/course-knowledge verify --job d21160908829551e97c79eb84cc96830
```
