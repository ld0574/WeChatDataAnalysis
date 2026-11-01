# 本地课程 Wiki 的试点入口

交付范围及全量验收以 [course-wiki-goal.md](course-wiki-goal.md) 为准。
本模块提供主题草稿、来源阅读、原图引用、带解析的自测和本地学习记录。
它不修改生产提取模块、不迁移原数据库，也不把结构检查当语义验收。

## 启动

在项目根目录使用现有虚拟环境，设置 `PYTHONPATH=src`。以下均为 PowerShell：

```powershell
$env:PYTHONPATH = 'D:\Project\WeChatDataAnalysis\src'
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_library --root output/course-knowledge build
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_library --root output/course-knowledge serve --port 3003
```

访问 `http://127.0.0.1:3003/`。端口 3002 的旧证据服务不受影响。
服务以只读方式打开既有数据库，不申请生产 schema/writer 锁，不加载模型。
来源发布先依据完整清单检查现有导出，只发布通过检查的课程；其余明确显示未发布。
目录以原子替换方式发布，中途失败继续保留旧目录。再次发布来源目录是显式操作。

发布产物在 `output/course-knowledge/library/`，主题文章在 `output/course-knowledge/wiki/`。
原始截图在 `library/media/<sha256>.jpg` 按内容去重；文章携带固定文字、时间范围、
图片指纹及来源导出指纹，重新整理来源笔记不会悄悄改变旧文章的证据。
当前试点的图片源为已保留课程截图；课件页图和依据来源重绘的图仍待接入。

## 生成主题草稿

生成前先发布来源。主题计划应围绕一个明确问题人工选择材料，支持跨课多节来源：

```json
{
  "title": "需要讲解的具体主题",
  "selections": [
    {"video": "来源课程的24位ID", "sections": ["section-0", "section-1"]}
  ]
}
```

```powershell
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_wiki --root output/course-knowledge --plan output/course-knowledge/wiki/plans/pilot-01.json
```

只能调用已验证为本地权重的 Ollama 模型，共享原生产 GPU 锁。GPU 忙时记录
`deferred_gpu` 并退出，不暂停生产或另启竞争模型；稍后重跑同一命令即可。
也可显式添加 `--wait-gpu-seconds 1800`，最多等待 30 分钟的可用时隙。
等待期间持有独立 Wiki 任务锁，每 10 秒检查一次；保持原任务和显存分配不变。
`wiki/last-build.json` 记录阶段与错误类型，不记录聊天或模型输入。
模型修订、源材料或提示词变更会产生不同文章 ID；相同计划再次执行会验证并复用结果。
生成中断或校验失败不会发布半篇文章。生成进程以单独 `wiki-build.lock` 串行运行。

输入材料受 8K 上下文预算约束，超限直接拒绝，不能静默截断证据。现在适合狭义主题
试点，不代表已实现跨长课的分层综合。完整长主题还需要规划、分节写作、合并复核和
相关文章关系。课程内容属于引用数据，其中的指令和代码不会被程序执行。

文章至少有学习目标、四个实质章节、限制或易错点、3–5道带答案/解释/判断标准的自测。
引用 ID 必须存在，代码只能是源材料原样摘录，图片必须引用已冻结原图。
这些检查不能证明概念、答案和图片说明正确，所有模型产物保持“草稿/语义待复核”。
自动试点计划仅用于验证发布流程，不是已核对的全量主题分类。

## 学习记录与迁移

作答、逐题自评、已读、复习标记、阅读位置存于当前浏览器的 `localStorage`。
作答不会发送给模型或服务器；已读不等于掌握，自评也不冒充自动评分。
每篇文章可导出 JSON 学习记录。换浏览器或清除浏览器数据前请先导出；
目前未实现批量导出/导入或跨浏览器同步。

迁移 Wiki 需保留 `library/` 和 `wiki/`，源归档还需另行备份原数据库、课件、
音频及其完整清单。仅复制网页文件或文章 JSON 不能代替完整归档与恢复验证。
本阶段不允许据此删除原视频。

## 验证

```powershell
.venv\Scripts\python.exe -m pytest tests/test_course_wiki.py tests/test_course_lesson_exports.py -q
ruff check src/wechat_decrypt_tool/course_library.py src/wechat_decrypt_tool/course_wiki.py tests/test_course_wiki.py
```

页面无需 CDN、远端字体、统计或外部模型。课程文字以 DOM 文本节点显示，代码以
`pre/code` 显示，不能把模型输出当 HTML 执行。图片缺失、目录未就绪、读校验失败
和检索零命中均显示明确状态。
