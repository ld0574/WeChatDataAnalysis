# 本地课程知识库

独立于微信归档运行，默认数据在 `output/course-knowledge`。课程音视频、文字、截图和问题不提交到云服务。首次安装依赖和下载 Ollama 模型需要联网，下载完成后课程处理使用本地文件和 `127.0.0.1:11434`。

## 当前模型

- 转写：本机 GPU 上的 Qwen3-ASR-0.6B，FP32。沿用隔离的 GPU 工作进程，不回退到 CPU 小模型。
- 截图文字、课程摘要、概念关系和问答：本机 Ollama 的 `qwen3.5:9b`。
- 搜索：SQLite FTS5，中英文关键词检索；无向量模型。问答在零命中时会先改写检索词，生成内容必须引用已存在的证据。

引用存在检查不能证明模型的每一句解释都正确。摘要和图谱是待核对草稿；历史课程中的 API、模型价格和版本不能直接作为当前事实。

## 操作

在项目根目录用 PowerShell 执行：

```powershell
# 完整流程：讲义入库 → 内容去重 → 全部课程处理（可重跑续接）
.\tools\course-knowledge.ps1 -Mode Build

# 更换课程来源或原视频改变时，先重新生成媒体清单
.\tools\course-knowledge.ps1 -Mode Inventory

# 查看持久化进度
.\tools\course-knowledge.ps1 -Mode Status

# 对已生成的 inventory.json 做完整 SHA-256 去重；相同标题不会合并
.\tools\course-knowledge.ps1 -Mode Prepare

# 提取保留在原位置的 PDF、课件、代码、笔记和 ZIP 内文本
.\tools\course-knowledge.ps1 -Mode Resources

# 处理全部去重课程：GPU 转写 → 抽样画面 → OCR → 摘要与关系
.\tools\course-knowledge.ps1 -Mode Run

# 本地浏览器页面，监听 127.0.0.1:3002
.\tools\course-knowledge.ps1 -Mode Serve

.\tools\course-knowledge.ps1 -Mode Search -Query '环境变量'
.\tools\course-knowledge.ps1 -Mode Ask -Query '漏选 PATH 怎么办？' -VideoId '课程编号'

# 当前窗口提交后暂停；已提交片段不会重复写入
.\tools\course-knowledge.ps1 -Mode Pause
.\tools\course-knowledge.ps1 -Mode Resume
# Resume 只解除暂停标志；再次 Run 才会继续处理
```

处理和模型问答共用 GPU 锁，处理时仍能进行普通搜索。要进行交互问答，先暂停批处理并等待当前请求结束，避免同时装载两套模型。停止处理后锁会自动释放。失败时保存错误与已提交的片段，再次运行会继续；恢复时仍需从头解码音频，但不会重复转写已提交窗口。

ASR 可用 `-AsrBatchSize 4`（CLI 为 `--asr-batch-size 4`）调整同一 GPU 模型的批量，默认 2。归档目录可保存 `processing-settings.json`，其中 `asr_batch_size` 为 1、2 或 4；显式参数优先。`run-plan.json` 记录本次实际加载的清单指纹、来源数量、批量和 FP32 精度。批量 4 遇到 CUDA 显存不足时，以同一模型、同一 FP32 精度把未提交窗口按批量 2 重试，后续本课继续使用 2；不会遗漏或重复提交分段。本机的 100 秒匹配样本实测批量 4 约快 1.9 倍，四轮识别原始文本相同；这不是整个课程质量或总耗时的保证。

本地模型接口遇到临时 5xx 错误时最多请求三次，间隔 1 秒和 3 秒，每次重试前重新确认模型为本地权重。永久错误仍会保留失败状态，截断结果继续采用分区处理，不能当作成功文字。

Ollama 的 `prediction aborted, token repeat limit reached` 也会返回 500，但重复相同请求无法解决特定画面的重复生成。程序单独识别这个错误，直接转入保留重叠区域的图像分区识别；不把丢失或重复的输出写成有效证据。原失败画面的实测为整图报错、上下两个重叠分区均正常完成。

媒体标签中的旧编码使用替换字符读取，课程音视频仍按正常解码规则处理。标签编码异常不代表视频已损坏；不要仅凭 UTF-8 元数据错误排除课程。修复清单后需要重新运行 Prepare，以便补回之前排除的路径；已完成且大小、修改时间一致的完整哈希会复用。首尾解码只证明媒体可读，整段覆盖仍由后续转写和校验确认。

下载器遗留的 `.downloading` 文件默认不作为完整视频。经过完整文件哈希、音频连续解码和声明时长验证的来源可登记到 `recovered-media.json`，后续 Inventory 会保留这些明确验证过的来源；文件大小或修改时间改变时列为错误，不会沿用旧验证。它们还需经过正式转写、画面和引用验收，不能把音频验证直接当作整个课程已完成。

讲义按原始内容哈希、提取器版本和归档内路径保存不可变的提取版本。修正字体解码或升级提取器后，搜索切换到新版本，旧证据编号和文字仍可追溯；较短的新提取结果不会残留旧版本尾部。完成的同版本资源重跑时会跳过解析。库结构迁移使用独立文件锁串行执行，避免多个命令同时迁移。

旧版本留下的 Mac 元数据或 Jupyter 检查点索引可按经过审核的候选 ID 清单执行 `retire-ignored-legacy --source '课程原始根目录' --audit '候选审计 JSON'`。命令逐条复核来源范围、旧提取器和现有忽略规则，只退出当前搜索；原文件、文字、证据 ID 和历史引用保留。普通课程内容和其他来源根目录会被拒绝处理。

PDF 使用文件流读取（上限 128 MiB）；PPTX/DOCX 直接读取容器内 XML（上限 512 MiB），不把全部内嵌图片加载进内存。普通文本仍限制 16 MiB。ZIP 中因单文件或累计预算跳过的资源会逐项记录，原文件保留；空白 PDF 页面和课件视觉页会列入待 OCR 清单。这些限制不代表素材已经完整归档。

仅补处理之前跳过的来源：

```powershell
$env:PYTHONPATH = 'D:\Project\WeChatDataAnalysis\src'
.\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_knowledge --root output/course-knowledge courseware 'D:\迁移\知乎 AI 大模型应用开发' --only-report output/course-knowledge/courseware-index-report.json
```

补处理结果另存 `courseware-supplement-report.json`，不会覆盖完整来源报告；之后再次执行 Resources 可将整个讲义库迁移到新提取版本。

PDF 图像页使用本地 Poppler 渲染，再交给本地 GPU 模型转录和描述图示。原图保存在 `document-visuals`，页面编号、原始 PDF 哈希、渲染器和模型摘要一并记录；来源证据页面可直接查看原图。模型输出仍是待复核草稿。文字因 PDF 更新失效时，对应旧 OCR 会退出当前搜索，历史原图和引用继续保留。相同页面的缓存按提取版本与模型摘要复用，处理时与视频任务共用 GPU 锁，GPU 忙则留待续跑。

```powershell
.\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_knowledge --root output/course-knowledge pdf-ocr --source 'D:\迁移\知乎 AI 大模型应用开发' --report output/course-knowledge/courseware-index-report.json --report output/course-knowledge/courseware-supplement-report.json
```

`pdf-ocr-report.json` 记录成功、失败、范围限制和 GPU 延后状态。空白页和带图示页面应对照渲染结果核对；PDF 解析出现省略警告时也需补查。该命令不渲染 PPTX，课件图示页仍需另行处理，不能据此认定所有视觉资料已覆盖。

可加 `--wait` 排队等待 GPU，并用 `--poppler 'pdftoppm.exe 的完整路径'` 指定本机渲染器。每页之间释放 GPU 并检查暂停标志，同类 PDF OCR 命令用进程锁防止重复运行。`pdf_targets` 保留提取器明确报告的省略页码，不能仅补空白页面而忽略部分提取的页面。

安装了本机 PowerPoint 时，可只读、隐藏窗口打开课件，禁用宏，按课件原有比例将选中的视觉页导出 PNG。带有宏或外部数据链接的输入会单独报错；原课件不保存修改，导出后再次核对原始哈希。已有校验过的图片可续用，图片被改动时会停止复用并报错。

```powershell
.\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_knowledge --root output/course-knowledge slides-render --source 'D:\迁移\知乎 AI 大模型应用开发' --report output/course-knowledge/courseware-supplement-report.json
```

`slides-render-report.json` 保存来源、页码、原始哈希和图片哈希。渲染完成表示图示已保留，后续仍须进行本地视觉识别和复核，不能等同于图示已进入文字搜索。文档入库使用写事务并对 SQLite 忙状态重试；PDF 识别结果先缓存，入库失败时重跑可复用推理结果。

批量渲染共用一个 PowerPoint COM 会话，逐份关闭本任务打开的只读课件，整批结束后再释放应用引用和 COM 初始化。这样避免在多份课件之间反复初始化、销毁 COM 会话。已有用户演示文稿不会被关闭；渲染中断后可按图片清单核对哈希并复用已完成页面。

将校验过的课件图片加入搜索：

```powershell
.\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_knowledge --root output/course-knowledge slides-ocr --render-report output/course-knowledge/slides-render-report.json --wait
```

`slides-ocr-progress.json` 与 `slides-ocr-report.json` 分别记录 GPU 等待、识别、入库和失败数量。相同图片、提取版本、模型摘要和参考原文复用本地缓存，图片哈希改变时拒绝复用；推理先缓存，数据库写入失败可续跑。视觉补充保存独立来源证据和原图，仍须复核。`--wait` 等待其他任务释放 GPU；新版本 Run 在课程边界给等待的识别任务让出 GPU，识别也在页面之间给转写留出调度机会。旧版运行进程需先在检查点暂停再重启，才会使用新调度代码。

图谱生成先校验真实引用和概念名称；名称不一致的关系单独用固定概念枚举重试，不能通过添加无证据概念来凑关系。Run 使用独立进程锁防止重复启动，等待 GPU 时继续响应暂停请求。

语音、截图、摘要和课件索引共享独立的数据库写入锁，短事务在执行前获取 SQLite 写锁；获取失败时关闭连接、退避后重试，事务正文不会被自动重放。读取继续使用 WAL 快照。大量嵌套课件分批提交并在批次间让出写入机会，防止并行导入让单课处理因写锁超时被跳过。已提交片段和证据版本保留，失败阶段可从断点补跑。

批处理在 ASR 完整阶段结束后释放 GPU，CPU 抽帧不占 GPU 锁。随后每个截图识别和摘要窗口分别排队、释放 GPU，让 PDF、PPTX 和无声演示共享同一个本地视觉模型，不必等待一整节课的所有识别完成。语音模型仍只在独占 GPU 时加载；进度区分准备、模型加载、推理和等待，避免把实际推理显示成停在等待。

两个无声演示可保留完整原片并加入搜索：

```powershell
.\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_knowledge --root output/course-knowledge silent-archive --inventory output/course-knowledge/inventory-unique.json
```

先执行 `resources-archive` 保存原片。无声演示逐帧解码校验整段时长，再以 1 秒间隔抽取不同画面交给本地 GPU 识别；原片 SHA-256、解码帧数和采样限制保存在每课 `silent-demo.json`。完整运动画面仍来自字节一致的原片，抽样文字不能代表全部视觉信息。网页和离线复核页均提供完整原片播放器；网页支持分段请求，损坏的归档原片会明确返回校验失败。原视频缺失时，已归档演示仍可播放和查询。备份包含对应媒体对象，并验证数据库媒体引用。

密集代码画面输出被截断时，不把部分文字标为成功：程序沿纵向分区，保留整行宽度和重叠边界，逐区重新识别；每区单独让出 GPU。来源图片哈希、模型摘要、分区坐标与已完成输出保存在本课 `frame-ocr-cache`，断点恢复复用完成分区。重叠文字可能重复，结果会明确标记区域；无法在限定细分层数内完成时继续报错，并保留完整原图和成功分区。截断输出仅保存在本机诊断文件中，不进入搜索证据。

所有课程模型请求在发送资料前检查 Ollama 模型确为本地安装的权重，拒绝远程模型标记；仅仅使用 localhost 地址不足以满足资料不外传的要求。

已保留的独立教学图片和 ZIP 内图片可先盘点，再用本地视觉模型加入搜索。两个命令均只读取 `resources-manifest.json` 对应的归档对象，原课程目录缺失时仍可运行：

```powershell
.\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_knowledge --root output/course-knowledge image-audit
.\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_knowledge --root output/course-knowledge --model qwen3.5:9b image-ocr --audit output/course-knowledge/teaching-image-audit.json --wait
```

盘点分别列出静态候选、动画、矢量图、小尺寸资产和读取限制，静态候选不会被误报为已识别。相同字节的静态图片只推理一次，同时保留每处来源引用；重跑复用完整分区缓存和已有证据版本。识别前核对容器与图片 SHA-256，保留原始分辨率，按 EXIF 方向展示，并将透明背景合成白底便于阅读。生成图片预算 4 GiB，另留至少 32 GiB 空间。原文件仍在可下载附件中，模型解读明确标为待复核草稿。动画和矢量图保留原件，但此命令不声称已覆盖运动信息或完成矢量渲染。

图片盘点只展开一层 ZIP；里面再次嵌套的 ZIP 会单列为未展开附件，不能把它们算作已完成图片覆盖。原始嵌套压缩包随容器保留。

嵌套压缩包中的文字课件可继续加入索引：

```powershell
.\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_knowledge --root output/course-knowledge nested-courseware --audit output/course-knowledge/teaching-image-audit.json
```

该命令核对保留容器的完整 SHA-256，只在内存中展开受深度和字节预算约束的 ZIP，不执行脚本。可处理常规课件、代码以及 JSONL、TSV 等文本样例；暂不解析的类型、超限项和加密成员逐项写入 `nested-courseware-report.json`，原始容器仍保留。相同来源版本重跑复用索引；不同来源位置保留独立引用。嵌套来源的下载会逐层校验成员与最终文件哈希，可同时下载所在原始外层 ZIP。空白 PDF 页和含视觉内容的 PPTX 页继续生成视觉处理清单；对应 PDF/PPTX 渲染命令可从保留附件读取嵌套来源，无需原课程目录。

表格附件支持只读索引：先用本机读取生成的 `spreadsheet-resource-audit.json` 确定原始文件哈希，再运行 `spreadsheet-courseware --audit output/course-knowledge/spreadsheet-resource-audit.json`。直接附件和嵌套 ZIP 来源均核对保留容器与 XLSX 哈希，并补上相同容器的来源别名。每条证据保留工作表、单元格坐标、值类型及原文件下载；零、布尔值和日期分别保留。公式只记录原表达式和已有缓存值，缺少缓存值时明确保留为空，不重算、不执行，也不改写或导出工作簿。超限或暂不支持的单元格类型会报错并保留原件，图表和图片只记录保留状态，不声称已渲染。


## 原声证据

当前课程清单中的有声视频均为单条 AAC 音轨。`audio-archive` 将其复制到每课的 `audio.m4a`，不重新编码，不再次降低音质。验证覆盖压缩包边界和完整哈希、编解码参数、全部解码 PCM 哈希、样本数及起止时间，并记录原视频完整哈希；新生成的第二版清单还验证每个解码帧的精确时间戳，不只比较首尾。已验证的音轨重跑时校验文件哈希后复用，不覆盖被改动的音轨。

早期清单若缺少 `decoded.timeline_digest`，还需进行完整时间轴补验。`course_audio_validation.validate_archived_audio` 会重新读取原视频和保留音轨，核对全部文件哈希、AAC 包、解码内容与每帧有理数时间戳，并检查过程中源文件、音轨和清单均未变化。它只返回与原清单哈希绑定的独立证明，不改写清单或修复音轨。批次证明保存在 `audio-timeline-validation-report.json`，仅当冻结清单中的每份音轨均通过时才标记该范围完成；这仍不等于整门课程已完成归档。备份包含此证明及其冻结计划。

损坏源若有另一录制版本，可使用明确标注的替代音轨；它不属于视频字节去重。准备恢复时要求两份源文件完整哈希、完整 AAC 包对比、所有未损坏源包一致、绝对时钟一致和候选音轨完整健康解码证明。程序重新核对源哈希，保留原损坏 AAC 包及其编辑时间轴，再复制并验证候选全部 AAC、PCM 和逐帧时间戳。每课的 `audio-recovery.json` 记录两份来源、诊断证明及保留文件哈希；`alternate-audio.m4a` 为替代音轨，`damaged-original-audio.m4a` 为保留的损坏原音轨。原音轨不会因此被标记为健康。

转写选择替代音轨时先写入 `asr-audio-source.json`，后续续跑须保持绑定一致；已有片段却没有此绑定时拒绝自动混用。画面仍采自原视频。课程页面和离线归档显示替代来源，提供替代播放、原损坏音轨下载及恢复证明；备份同时携带这些文件。四份恢复的批次证明见 `alternate-audio-recovery-report.json` 和 `alternate-audio-recovery-validation.json`。有恢复音轨不代表转写、视觉覆盖或整课验收已完成；无健康替代来源的损坏课程仍须分别恢复有效区间并保留时间缺口。

普通封装可能丢失 MP4 的音频前置空白和编码器预热裁剪。源音频完整解码成功、复制音轨却解码失败或不一致时，程序会尝试保留原音轨的编辑表、影片时间刻度和轨道时长，并修正媒体块偏移；随后重新验证全部 AAC 包、PCM 和逐帧时间戳。只支持正常速度的单段音频及其可选前置空白，复杂剪辑保留原件供复核。源音频本身解码失败不会被此修复掩盖，任何验证不一致均保留原视频并报错。失败清单同时记录发生阶段。

```powershell
.\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_knowledge --root output/course-knowledge audio-archive --inventory output/course-knowledge/inventory-unique.json
```

默认音轨预算 48 GiB，单独保留至少 32 GiB 的磁盘余量供后续归档使用。全量大小先根据原始码率估算，写入时还核对实际大小。`audio-archive-plan.json`、`audio-archive-progress.json` 和 `audio-archive-report.json` 分别记录预算、阶段和完成/失败清单。每课的 `audio.json` 是原声证据清单。支持的课程页面和新生成的离线复核页可播放原声，网页消息旁可跳到对应时间。

音轨归档不占模型 GPU，但完整解码校验会使用 CPU 和本地磁盘。该命令当前只接受单条 AAC 音轨，不会把其他格式或无声视频当作已完成；两个无声演示样例仍需要视觉归档。音轨保留也不能代替短暂代码画面、动画和操作演示的覆盖检查。

## 归档文件

| 文件 | 作用 |
| --- | --- |
| `inventory.json` | 原视频路径、大小、修改时间、媒体流和读取错误 |
| `inventory-unique.json` | 内容去重后的处理清单、重复路径、排除的异常文件 |
| `video-hashes.json` | 与大小和修改时间绑定的完整哈希缓存 |
| `knowledge.sqlite3` | 逐段转写、OCR、摘要、关系、检索索引和事件记录 |
| `job-progress.json` | 当前阶段、进程号、更新时间与视频进度 |
| `preparation-progress.json` | 去重进度 |
| `courseware-index-report.json` | 讲义处理结果、错误路径、PDF 提取警告、大小限制和需要 OCR 的页面 |
| `courseware-index-errors.json` | 讲义处理中已发生的错误 |
| `lessons/<编号>/evidence.json` | 逐段时间范围、原始识别、截图、模型信息与摘要 |
| `lessons/<编号>/transcript.md` | 可直接阅读、迁移的逐字稿和摘要 |
| `lessons/<编号>/graph.json` | 本课概念和关系，附来源证据编号 |
| `lessons/<编号>/review.html` | 无服务器也能打开的课程复核页，含搜索和图谱关系 |

数据库使用 WAL；运行中备份请使用 SQLite backup API，不能只复制主文件而忽略正在写入的 WAL。原始附件可另外生成可迁移副本，整个过程不执行课程代码：

```powershell
.\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_knowledge --root output/course-knowledge resources-archive --source 'D:\迁移\知乎 AI 大模型应用开发' --inventory output/course-knowledge/inventory-unique.json
```

该命令冻结附件清单，按完整 SHA-256 保存到 `resources/objects`，在 `resources-manifest.json` 记录原路径与对象位置；相同字节只保存一份，输出重新读回验证。包含课件、代码、数据集和清单中的无声演示原片，默认预算 16 GiB、保留 32 GiB 磁盘余量。原文件改变、对象损坏或空间不足会明确报错，原文件不会删除。`.downloading` 文件另列待恢复媒体，不能算成已完整保存的课程。附件副本尚不等于整个知识库已完成备份。

讲义证据页可下载哈希绑定的归档原文件；引用来自 ZIP 时，也可下载所在压缩包。下载只读取 `resources/objects`，不依赖原课程目录。直接文件先核对整文件哈希，ZIP 成员同时核对容器与成员哈希；当前原件不匹配历史引用时不会替换提供。缺少原文件哈希的旧文字引用仍可查看，但不冒称原件已校验。

从 ZIP 提取的下载文件按哈希缓存到 `resources/derived`，单文件上限 512 MiB，总缓存上限 2 GiB，并保留 32 GiB 磁盘余量；缓存写入串行执行。缓存满或损坏时会明确停止成员下载，可清理该衍生缓存后重试。下载整个原压缩包不占衍生缓存，但仍核对被引用成员。所有原文件以附件提供，不在知识库页面执行课程代码或 HTML。

Notebook 的运行输出可单独建立索引，不会运行单元代码。先准备经归档副本核对的 `notebook-source-audit.json`，其 `completed` 每项包含原路径 `path`、父文档键 `source_key` 和完整源文件哈希 `raw_digest`，再运行：

```powershell
.\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_knowledge --root output/course-knowledge notebook-outputs --audit output/course-knowledge/notebook-source-audit.json
```

命令只从已保留的原始 Notebook 读取打印结果、错误、文本 MIME 和 JSON 展示数据；现有父文档代码索引继续保留。尚未索引的 Notebook 会先补建源单元索引，包括历史 checkpoint；已存在的父文档哈希与审计不符时停止该项。每个输出按单元号、输出序号、表示类型和字符位置建立引用，重复运行复用当前版本。HTML 和 JavaScript 作为文本索引，页面不会执行；图片、嵌入附件、富文本和交互展示的视觉解释缺口会单独列出。已识别的凭据赋值和 JSON 敏感字段在索引里掩码，训练参数和普通 token/key 数据继续保留，原始文件仍作为哈希绑定附件保存。结果与错误见 `notebook-outputs-report.json`，该报告和审计、验证文件会进入备份。

Notebook 内嵌静态图片和已失败的教学图片可使用原分辨率切块流程处理：

```powershell
.\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_visual_sources --root output/course-knowledge --audit output/course-knowledge/notebook-image-audit.json
.\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_visual_sources --root output/course-knowledge --audit output/course-knowledge/teaching-image-audit.json --failures output/course-knowledge/image-repair-source-errors.json
```

切块保留原图像素，不缩放，最大 1200×1200 像素，相邻块重叠 64 像素。遇到模型截断或重复输出时沿较长方向继续细分；失败内容不当作完整证据。每个裁切图保留源图坐标、PNG 哈希、本地模型哈希和来源附件链接，Notebook 图像同时绑定 Notebook 文件及内嵌图像的哈希。相同裁切图共用识别缓存，各个来源保留独立引用。当前单图限制为 32 MiB、6400 万像素及 256 块，超出范围仍保留原件并明确报错；切块图像预算 4 GiB、另留 32 GiB 磁盘空间。模型描述仍需复核，静态识别不代表完成动画、HTML 或交互控件的视觉覆盖。

PDF 的单页识别失败会记录具体页号并继续后面的页；重复或截断的页面会转入分块识别。已有哈希绑定的页面证据继续复用。PDF 页面图像的最长边仍限制为 2000 像素，分块保持的是这些渲染像素；原 PDF 另行保留，不能据此声称极小字形或复杂图示已独立验收。

创建独立快照并验证恢复：

```powershell
.\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_knowledge --root output/course-knowledge backup --destination 'C:\Users\Administrator\Documents\CourseKnowledgeBackups'
.\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_knowledge --root output/course-knowledge backup-verify --snapshot '备份根目录\snapshots\快照编号'
.\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_knowledge --root output/course-knowledge backup-restore --snapshot '备份根目录\snapshots\快照编号' --destination '一个空的恢复目录'
```

快照通过 SQLite backup API 捕获一致的数据库，再保存其引用的截图、文档原图、附件对象和已验证音轨；每份文件重新核对 SHA-256。备份根目录下的 `objects` 在快照间复用相同字节，迁移时必须同时携带 `objects` 和 `snapshots`，不能只复制快照子文件夹。默认另留 64 GiB 目标盘空间。验证包括数据库完整性、外键、行数、所有证据对象哈希及图片引用完整性；已提交对象被改动时停止使用，恢复命令不覆盖非空目录。

运行期间的快照保留当前已完成数据，清单明确标记整个课程归档尚未验收；不能据此删除未完成课程。尚未提交的媒体不进入快照。离线复核页可在恢复后从数据库用 Review 重新生成，模型权重和 Python 运行环境需另行保留或安装，本命令不复制它们。

恢复前会按目标路径估算数据库及证据文件的写入量，包括相同备份对象恢复到多个路径的副本。默认保留 32 GiB 空间，可通过恢复命令的 `--reserve-gib` 调整；空间不足时不创建恢复目录。逐个复制文件前还会检查余量，防止其他进程占用空间后继续写满磁盘。中途停止时保留已恢复部分供检查，原备份不变。

## 视频删除前的验收

程序**没有删除原视频的命令**，`deletion_ready` 始终为 false。语音按 25 秒提交，时间是分段边界，并非逐字对齐；画面每 30 秒抽样并抑制近似静态画面，无法保证保留短暂出现的代码或演示动作。原声是否已保留须检查 `audio-archive-report.json` 和每课的验证清单，不能根据少量样本推断全量完成。仅凭当前抽样版本无法无损替代视频。

删除前需要：所有可读课程处理完成；逐项处置不可读视频和索引错误；复核专业名词、摘要和引用；对关键代码/操作密集课程补充视觉资料；确认有可恢复的知识库备份。需要保留再转写能力时，应另外保留压缩音频。PDF 提取超限或扫描页面不会被当成完整文本。

可用空间低于 8 GiB 时暂停。全量耗时应依据本机课程样本和去重后的时长估算，不能用短微信语音的测速推算长课。未通过验收前，保留原视频和原位置的所有非视频资料。
