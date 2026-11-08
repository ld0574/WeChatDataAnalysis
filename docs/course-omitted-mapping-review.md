# 耗尽修订的遗漏标注提案与原最终审核

`course_textbook_omitted_mapping_review` 继续作业 `c2240cf9be918a77b74fb0a91f493568`。
原写作、修订、失败和预算均保留，没有第三次写作或第三次修订。

## 实际失败与边界

09:38 左右，段内 point_ids 修订两次均返回完整、相同的 5 段正文，实际解码 schema 均通过。
但有 3 个必需任务未出现在任何段落的 point_ids 中，转换回原覆盖结构时触发 minItems。
先前诊断只显示首个失败字段，并不代表只漏标一个任务。
原两次 raw response、真实请求、解码 schema、GPU 回执及原 ValidationError 均保留。
原私有 guard `25828 / 1794101310.197748` 已退出，returncode 1；不重跑它。

新入口逐份认证上述实际请求与全部原始材料，只对这 3 个**未标注任务**提出
“在所有实际已有段落中查找”的覆盖范围。程序不声称这些段落讲到了相应教学，
不新增、补写、删除或改述正文，不改变原 teaching 判断或有效的段落映射。
主要原文引用的补充仍是未验证元数据，不构成事实支持。

完整原文、未改变的候选、原缺失 ID、整个实际段落范围和未验证标识共同送入
原尚未调用的 `section-0007-repair-review`。复核仍由不同本机权重 `course-defiant:q4` 执行，
沿用原角色最多两次结构尝试；没有新增写作角色、语义修订或 recipe 刷新。
实际负面意见保留。即使此事实审核全部通过，完整组稿中仍强制保留
`omitted_point_mapping_proposal_requires_whole_original_coverage`，等待整条原文覆盖核对。
因此不能把本提案或审核运行结束当作文章验收通过。

原任务其余六个未调用单元继续使用真实段内 ID 格式。未标注有效教学时，校验明确列出
缺失 ID，避免原来的通用 `ValidationError` 让确定性模型原样再输出一次。
只改未使用角色的验证反馈，不修改或重试旧角色。

## 实际准备和运行

65 项相关测试及 Ruff 通过。冻结新适配器 SHA256：
`1742b2ceb7ee07e77850c93477e813a684bf34b43592869e0da574030b7231d6`。

```powershell
$env:PYTHONPATH='src'
$env:PYTHONUTF8='1'
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_omitted_mapping_review `
  --root output/course-knowledge --job c2240cf9be918a77b74fb0a91f493568 `
  --tokenizer output/course-knowledge/wiki/whole-lesson-narrative-20261104/local-model-tokenizer.json load
```

prepare 已执行一次，不重复运行。实际执行使用同参数的 guard，完整退出后使用 verify；
prefix 只重建实际完成的内部单元。缺失缓存的只读核对禁止调用模型或获取 GPU 锁。
新 guard 位于原作业 `point-mapped-resume/omitted-mapping-review/`，不覆盖旧 guard。
09:49:55 启动器为 `2532 / 1794102595.105253`，实际 guard/worker 身份另见进度文档。
随后实际核对 guard 为 `23008 / 1794102595.2668436`，Python worker 为
`22828 / 1794102628.8097687`；均存活，当前为原 `section-0007-repair-review` 等待 GPU。

## 有限输入副本与独立载入

副本：`C:/Users/Administrator/Documents/CourseKnowledgeBackups/omitted-mapping-inputs-20261108-094758`，
565 文件、34,828,008 字节，manifest SHA256
`eef8a98ac77f990f1f6901c5ce922a9a8fd0357f727334bab7c27ffeb8bdd5b7`。
实际再次复制并由独立源码 load，认证同样的前七单元、两次原 write、两次原 repair、
三项遗漏和提案；13 份旧账本 group proof 仅按原哈希一对一重定位路径。
有限 SQLite 为 videos/segments/frames 的 1/302/140 行，quick_check 通过。
不含全量数据库、原视频、模型或完整运行环境，依赖主 venv 与本机模型目录。

在副本 `restored/workspace` 中，PYTHONPATH 指向其 src，再用主 venv 执行：

```powershell
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/omitted-mapping-restore-20261108.py `
  output/course-knowledge 'D:\Project\WeChatDataAnalysis\output\course-knowledge' `
  c2240cf9be918a77b74fb0a91f493568 `
  output/course-knowledge/wiki/whole-lesson-narrative-20261104/local-model-tokenizer.json load
```

该输入副本只能证明继续处理所需输入可恢复，不能用它宣称最终审核、组稿或整课验收成功。
正文、原文、OCR、真实响应和审核理由始终只留本机。
