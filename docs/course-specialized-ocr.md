# 专用 OCR 离线草稿

原生 Windows 草稿加 Qwen 原图纠错的首轮三张试点全部未通过。因此不继续给同三张图刷新 GPU
角色，而是在独立环境补充专用文字识别证据。`tools/course_specialized_ocr.py` 仅生成私有 OCR
草稿，不能改归档数据库、清除失败、发布文章或授权删除视频。

## 隔离与固定输入

独立 Python 环境：`output/course-knowledge/tools/rapidocr-3.10-env`。
固定 RapidOCR 3.10.0、ONNX Runtime 1.28.0 及其 21 项实际依赖，版本见
`tools/course_specialized_ocr_requirements.txt`。没有修改健康 worker 的 Python 环境。

文字检测与识别分别使用 PP-OCRv5 server 的本地 ONNX 权重，模型共 172,695,790 字节。
两份文件均按发布者配置的 SHA256 核对；下载和识别为不同命令。
识别只读显式给定的本地模型，禁止 socket 连接，关闭 ORT 遥测；实际检测及识别 provider
均验证为 `CPUExecutionProvider`，每个 session 最多两条计算线程。
这只是专用光学识别草稿，现有语音、写作、事实及图文审核仍沿用 GPU 模型。

所有返回文字、原图 SHA、原图坐标、多边形和置信度完整保留，包括低置信度结果。
没有用置信度过滤隐藏低分文字。库自身不返回的区域不能因此宣称已经识别；完整原图仍保留。
一次调用最多六张不同图，输出目录只能新建一次；结构或运行失败保留原件，不自动重试。
C/D 各保留 32 GiB。

本次已实际执行的工具源码绑定 SHA：
`51b66e4b635e783890ec6d58b6b220cbedd5cd289a6434af911ef053496324fc`。
该文件不能在既有执行凭证下编辑或格式化。

## 已知文本的实际校准

三个已知正确的合成代码及中文画面，字号分别为 14、18、22 像素；每张 13 行，
忽略空白后 449 个字符。统一计算编辑距离，大小写、标点和代码符号均参与比较。

| 字号 | 专用 OCR 字符错误 | Windows 中文 OCR 字符错误 |
| --- | ---: | ---: |
| 14 px | 0/449 | 125/449 |
| 18 px | 0/449 | 100/449 |
| 22 px | 0/449 | 133/449 |

专用 OCR 三张共约 31.56 秒。这个校准不验证空白、缩进、复杂布局或真实课程完整性，
不能将 0 字符错误扩大为真实课程准确率 100%。合成原图、预期文本、两种实际识别及比较方法
均只保存在本机校准目录 `wiki/specialized-ocr-20261108`，没有挑选最好的一份响应。

## 三张真实画面的补充草稿

沿用上一轮三张完全相同的原图，只运行新 OCR 的 CPU 草稿，不申请 GPU、不写回原帧。
实际结果为：

| 帧 ID | 返回行数 | 低于 0.85 的行数 | 耗时 |
| --- | ---: | ---: | ---: |
| 6925 | 88 | 12 | 17.75 秒 |
| 6969 | 90 | 8 | 15.89 秒 |
| 7013 | 55 | 12 | 12.77 秒 |

三张共约 46.41 秒。低分文字及位置均保留，不是自动排除项，也不等于全部错误都能由低分发现。
原 GPU 试点仍为 0 supported / 3 unresolved；源错误、原帧、角色预算及原响应完全不变。
实际核对了新草稿的原图 SHA、工具 SHA、实际 CPU provider、原数据库行和原负面凭证。
`private-evidence-binding.json` 保存这份对应关系，不在工具输出中展示真实文字。

下一步需要在真正未尝试的画面上，用固定有限预算验证专用草稿与原像素、代码布局及图示的对应关系。
原三张 GPU 预算不能因 OCR 方法变化而重置。本次 CPU 证据本身不构成发布或全量质量通过。

## 本地运行

```powershell
uv venv --python .venv\Scripts\python.exe --no-python-downloads `
  output/course-knowledge/tools/rapidocr-3.10-env
uv pip install --python output/course-knowledge/tools/rapidocr-3.10-env/Scripts/python.exe `
  --no-cache -r tools/course_specialized_ocr_requirements.txt

output/course-knowledge/tools/rapidocr-3.10-env/Scripts/python.exe `
  tools/course_specialized_ocr.py assets --folder <本机模型目录>
output/course-knowledge/tools/rapidocr-3.10-env/Scripts/python.exe `
  tools/course_specialized_ocr.py recognize --folder <已校验本机模型目录> `
  --output <全新私有输出目录> --input <原图文件>
```

九项测试和 Ruff 通过，涵盖网络拒绝、实际 CPU provider、低分保留、数据和模型变更、
失败原件、同目录拒绝重跑及异常隐私。

## 已实际完成的独立离线恢复

副本：`C:/Users/Administrator/Documents/CourseKnowledgeBackups/specialized-ocr-offline-20261108-072825`。
59 文件、283,829,153 字节，包含两份模型、21 份匹配 Windows x64 / Python 3.11 的 wheel、
工具与固定版本、校准输入、原图和本次私有草稿。manifest SHA：
`0e76fbddc575323f8b3dedc4bacfa0378e7fb84c34ee8e249c14753399935371`。

已核对全部文件，复制到独立恢复目录，以 `--offline --no-index --require-hashes` 从副本安装到
全新环境，不使用运行中 worker 的 site-packages。实际验证 21 项包版本一致，RapidOCR、ORT、
OpenCV、NumPy 和 Pillow 均从恢复环境导入。
从恢复的两个模型实际运行一张已知合成画面，返回相同 13 行文字、多边形、置信度和 CPU provider。
三张真实原图仅核对 SHA，没有重新识别真实图或申请 GPU。

离线安装需已有 Windows x64 Python 3.11 与 uv；运行方式如下，环境目录必须选全新位置：

```powershell
$ocrBackup = 'C:\Users\Administrator\Documents\CourseKnowledgeBackups\specialized-ocr-offline-20261108-072825'
uv venv --python .venv\Scripts\python.exe --no-python-downloads <全新环境目录>
uv pip install --python <全新环境目录>/Scripts/python.exe `
  --offline --no-index --no-cache --require-hashes --link-mode copy `
  --find-links "$ocrBackup/wheelhouse" -r "$ocrBackup/requirements-offline.txt"
```

这证明该独立 OCR 配置的有限恢复，不代表完整课程数据库、全部模型、全部素材或其他机器上的环境
已经验收。原 GPU 失败的既有独立副本继续保留。
