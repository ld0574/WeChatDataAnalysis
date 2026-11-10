# 原图修复耗尽后的真实终态

作者 `f8bc01ff9ffde2fd4e584e9f1ffe5912` 停在 12/16 单元，未形成完整稿。
`section-0012-write` 的两次写作和原一次修复的两次结构尝试均已耗尽。
原初审已经调用，原终审及后三单元尚未使用；不能以此增加第三次写作或第二次语义修复。

实际 guard `33244 / 1794270010.2194068` 和 worker `6044 / 1794270012.370835`
均已退出，guard 返回 1，原因是 `wiki_stage_attempt_budget_exhausted_section-0012-repair`。
两个修复返回均完整 stop，字节完全相同，SHA256：

`d6de0c9ae7b91250e63fa8a0f4cdb063fa06d6ffc793b68d064015ae4c3fa7b0`

每份包含 6 段、23 个覆盖映射，其中 17 个为空；图有 5 节点、6 边、2 个未知端点。
未知端点不能唯一匹配实际节点标签，不能靠标签别名做无损修正，更不能猜边后放行。
每次实际输出 3,635 tokens，生成约 67.846 / 67.141 秒。这些是失败候选的服务指标。

`course_textbook_graph_tail_terminal` 只读认证原 12 单元、2 项前缀未决意见、
原请求/schema/模型回执、初审及两次失败修复，不建立 accepted repair 缓存。
它不调用模型、原终审或后三单元，不允许发布或删除来源。
13 项终态测试和 14 项原有限尾部测试通过；这些不能代替课程验收。
本模块已由实际终态备份/重放使用，代码及绑定依赖保持冻结。

## 独立结果恢复

副本：

`C:\Users\Administrator\Documents\CourseKnowledgeBackups\graph-tail-terminal-20261110-084048`

1,150 文件、48,307,430 字节。manifest SHA256：

`bc24548c9413287b1727fecdf78e52d622ee0d42002cc0a92c31a94324f73d5a`

终态 proof SHA256：

`9b36ac7860617895b1eae4283629d4730b5be843508c65516f29bc18d586d290`

再次复制的源码实际重建同一 12/16 前缀、2 项未决意见、初审和两次失败修复。
44 份旧路径证明按原字节哈希一对一重定位，真实有限 registry 为 2 来源/659 段/368 帧，
quick_check 通过。原归档读取和模型调用均为 0。
`complete_manuscript`、语义验收、发布和来源删除均为 false。
这不是视频、模型、全量数据库或完整运行环境的恢复证明。

在该副本的 `restored/workspace` 内设置 PYTHONPATH 为其中的 src，使用项目 Python：

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/backup_graph_tail_terminal_20261110.py verify `
  output/course-knowledge D:\Project\WeChatDataAnalysis\output\course-knowledge ..\..\manifest.json
```

未完成工作继续留在全量 Goal；其他未使用课程通过[明确接续](course-tail-ready-continuation.md)推进，
不把这份终态重新解释成前驱完成，也不重启已停止的旧控制器。
