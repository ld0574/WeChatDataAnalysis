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

媒体标签中的旧编码使用替换字符读取，课程音视频仍按正常解码规则处理。标签编码异常不代表视频已损坏；不要仅凭 UTF-8 元数据错误排除课程。修复清单后需要重新运行 Prepare，以便补回之前排除的路径；已完成且大小、修改时间一致的完整哈希会复用。首尾解码只证明媒体可读，整段覆盖仍由后续转写和校验确认。

讲义按原始内容哈希、提取器版本和归档内路径保存不可变的提取版本。修正字体解码或升级提取器后，搜索切换到新版本，旧证据编号和文字仍可追溯；较短的新提取结果不会残留旧版本尾部。完成的同版本资源重跑时会跳过解析。库结构迁移使用独立文件锁串行执行，避免多个命令同时迁移。

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

安装了本机 PowerPoint 时，可只读、隐藏窗口打开课件，禁用宏，按课件原有比例将选中的视觉页导出 PNG。带有宏或外部数据链接的输入会单独报错；原课件不保存修改，导出后再次核对原始哈希。已有校验过的图片可续用，图片被改动时会停止复用并报错。

```powershell
.\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_knowledge --root output/course-knowledge slides-render --source 'D:\迁移\知乎 AI 大模型应用开发' --report output/course-knowledge/courseware-supplement-report.json
```

`slides-render-report.json` 保存来源、页码、原始哈希和图片哈希。渲染完成表示图示已保留，后续仍须进行本地视觉识别和复核，不能等同于图示已进入文字搜索。文档入库使用写事务并对 SQLite 忙状态重试；PDF 识别结果先缓存，入库失败时重跑可复用推理结果。

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

数据库使用 WAL；运行中备份请使用 SQLite backup API，不能只复制主文件而忽略正在写入的 WAL。PDF、代码和 ZIP 原文件保留在课程目录，不复制数 GB 的附件，也不会执行课程代码。

## 视频删除前的验收

程序**没有删除原视频的命令**，`deletion_ready` 始终为 false。语音按 25 秒提交，时间是分段边界，并非逐字对齐；画面每 30 秒抽样并抑制近似静态画面，无法保证保留短暂出现的代码或演示动作。原声不保存在知识库中。仅凭当前抽样版本无法无损替代视频。

删除前需要：所有可读课程处理完成；逐项处置不可读视频和索引错误；复核专业名词、摘要和引用；对关键代码/操作密集课程补充视觉资料；确认有可恢复的知识库备份。需要保留再转写能力时，应另外保留压缩音频。PDF 提取超限或扫描页面不会被当成完整文本。

可用空间低于 8 GiB 时暂停。全量耗时应依据本机课程样本和去重后的时长估算，不能用短微信语音的测速推算长课。未通过验收前，保留原视频和原位置的所有非视频资料。
