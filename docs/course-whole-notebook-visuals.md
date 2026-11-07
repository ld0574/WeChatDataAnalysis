# 完整原文课件的图片依赖恢复

第二课的完整原文清单包含 84 条 Notebook 文字记录，但原 Notebook 的五个单元还引用了七张图片。
图片本身和完整 OCR 已存在归档中；缺少的是同一个 Notebook 单元到这些图片及识别证据的明确映射。
不能把文件名相同、图片 SHA 相同或课件主题相似当作课堂关联和知识覆盖已经通过。

`course_textbook_whole_notebook_visuals` 只在 CPU 上读取既有材料，不调用模型，不重做 OCR，也不改变
已冻结的作者、覆盖计划或原文清单。它解析 Markdown 和 HTML 图片引用，按原 Notebook 在同一 ZIP
中的实际相对位置寻找唯一成员，校验原始字节、归档 SHA、Notebook SHA 和直接文字来源编号。
不通过文件名搜索代替原路径，不下载远程依赖，不运行 Notebook 代码；嵌入图、运行时视图、缺失或
有歧义的成员等未支持依赖明确保留为未决。

对已有识别结果，逐项核对当前及历史数据库 revision、所有文字块、原始图片与规范化图片的每个像素，
并由原验证器重建完整 OCR 区域树。数据库使用只读连接，查询限定实际 `path/source_key` 索引，不扫描
整个 documents 表。识别正文只进入私有 sidecar，CLI 仅返回数量、状态和哈希。

## 实际结果

2026-11-08 已实际 `prepare` 并在独立进程执行 `verify`：7/7 张图片完整映射，0 项未恢复依赖，
新增模型调用为 0。这里的“依赖恢复完整”只涉及这份已选 Notebook；图示含义、课堂关联、文章整合和
教学覆盖仍须独立审核，不能因此发布第二课或删除原视频。其他可恢复课件继续属于全量 Goal。

私有产物：

```text
output/course-knowledge/wiki/textbook-whole-notebook-visuals/
  68c6398d1b05c82fb32869397831b15cfff29ee2fa9ec1f2ff303ee4c51f6de7/manifest.json
```

manifest SHA 为 `09f24965642886598eae490f6aa8899137f7b75fe3978dd0e31fbcaf8f1fe6c6`，
proof SHA 为 `802ac7cb6cf229edb545f7c7b9a915cfe6801af88201f788adbeb67461d18bcb`。
模块现已绑定实际私有凭证，SHA 为
`0d4b7d146ec77e033f918f75d6b90f2aa53b081a1963fdc82ae4b4b4769cb0f5`，不得编辑或格式化。

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_whole_notebook_visuals `
  --root output/course-knowledge `
  --inventory 68c6398d1b05c82fb32869397831b15cfff29ee2fa9ec1f2ff303ee4c51f6de7 verify
```

本模块、Notebook 上下文和原图片验收器共 60 项测试及 Ruff 检查通过，包含真实文件/ZIP/SQLite 的
合成集成检查，以及缓存正文、图片字节、数据库文字块、revision 和历史元数据变更时的拒绝检查。
这些是软件契约测试，不能替代实际内容审核。

本轮增量副本为
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/whole-notebook-visual-links-20261108-044634`：
38 文件、36,871,693 字节，backup manifest SHA 为
`8cd98e3ca404eb9f66ac7973cc8aeea76693a4889287aec8438e1b735d6e254f`。
副本再复制后的 SHA、JSON 和 Python AST 检查通过。还在独立恢复目录中，仅用备份里的七份原始
OCR 数据库记录建立明确标记范围的 SQLite 子集，重新读取所备份的原 ZIP、Notebook、图片和区域缓存，
实际重建出完全相同的私有 sidecar，新增模型调用为 0。该子集不是完整知识库、模型环境或生产数据库；
恢复结果保存在 `independent-reconstruction.json`。
