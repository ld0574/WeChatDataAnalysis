# 原请求实际上下文的恢复

`course_textbook_visible_context` 用于整课写作已停止、原两次写作尝试均保留，但完整候选因引用范围
与实际输入不一致而被拒绝的情形。它不修改已经绑定的原写作、引用去重或结构恢复模块。

准备阶段只读重建概念计划、原写作分配和之前的小节，精确认证两次原始请求、模型身份、真实响应
和 GPU 回执。原写作认证仍使用原证据包。允许范围只增加该请求 `context_sources` 中实际提供的
完整相邻原文；必须与清单中的文字逐字一致，摘要、缺失材料、其他来源与未知编号均不能加入。

候选正文、概述、结论、教学判断、覆盖映射和自测保持不变。主引用补充只是待审元数据提议；
无效生成图完整保留在私有隔离凭证。`kind=teaching` 只在不接受该字段的 composition 校验层暂时
移除，并在返回候选时原样还原。适配器不会删除范围外引用以使候选通过。

新恢复凭证保存到原作业的 `draft-resume/<原写作阶段>.json`，明确绑定实际上下文、原证据包、
扩展证据包、相邻原文、实际可见文字及适配器代码 SHA。不会创建假的 `<原写作阶段>.json`
模型成功缓存。已有结构恢复及引用去重凭证按原规则原样重建，不添加新字段使旧凭证失效。

`prepare` 新增模型调用为零。`guard` 在受支持的 `wiki-revision-thermal-guard` 下运行同一作业，
仅为已认证的写作单元将后续原 `review`、`repair` 和 `repair-review` 的证据范围换成实际可见原文。
原阶段名称、最多两次结构尝试和一次语义修订预算不变。其他单元、规划和整篇阅读角色不扩大范围。
后续仍必须完成独立事实、来源覆盖、课件关系、图文、自测和实际阅读检查，不能凭恢复发布文章。

所有临时 hooks 只存在于单独 Python 进程内，异常时恢复。历史请求认证方法可在原 Job 实例上
直接调用适配方法，因此适配入口显式转交冻结的原 Job 方法；不能依赖实例属于新 subclass。
真实零模型准备曾在这里因 `super()` 的实例类型限制停止，诊断和回归检查保留，不消耗模型预算。

可使用以下入口，参数必须来自原停止作业，不能以新作业分配预算：

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_visible_context `
  --root output\course-knowledge --inventory <原清单SHA> `
  --ledger-job <原账本作业ID> --tokenizer <原tokenizer路径> prepare
# prepare 成功并独立备份后，使用相同参数将最后的 prepare 改成 guard。
```

守护保留之前已退出 guard 的原状态。保护上限 200W，70°C 暂停且不自动恢复，C/D 各保留至少
32 GiB；不移除新 `pause.flag` 或 `supervisor-stop.flag`，也不重启其他健康进程。
恢复原件与代码增量备份到 `Documents/CourseKnowledgeBackups/whole-visible-context/<原文章作业ID>`。
这些增量备份不代表完整模型环境或全量知识库恢复。
