# 本地解码约束的合成数据校准

真实最终编辑候选存在空映射，被本地 `minProperties` 校验拒绝；单凭此现象还不能断言
供应端忽略了哪个关键词。`course_schema_capabilities` 只用三个固定合成请求调查实际本地供应端，
不读取任何课程原文，不消耗课程写作角色，不修改旧失败或重试额度。

三个测试均通过 `http://127.0.0.1:11434` 使用 `course-defiant:q4`，固定 4,096 上下文、
最多 256 输出 tokens、温度 0、关闭思考，每项最多一次请求，HTTP 失败立即保留并停止：

| 测试 | schema | 刻意冲突的合成提示 |
| --- | --- | --- |
| min_properties | 只能使用键 1/2/3，至少且最多一个属性 | 输出空对象 |
| required_primary_fields | 明确 required 的 paragraph 和 sources；sources 至少一项 | 输出空对象 |
| max_properties | 只能使用键 1/2/3，至少且最多一个属性 | 输出全部三个键 |

请求通过原共享 GPU 锁，单独的现有 private guard 实测温度、功耗和 C/D 空间。
任何新暂停/停止标记均保留，70°C/200W 和每盘至少 32 GiB 保护保持，不能自动热恢复。
没有重启或更改健康任务。请求之前先写入已发送记录，保留实际响应原字节、GPU owner、模型 digest、
计数和校验结果；违反 schema 是诊断结果，不能称为可接受的课程输出。

每个实际响应使用独立 JSON Schema 校验，readonly verify 重新读取真实请求和响应，重算同一结果；
verify 不访问模型。一次通过只能说明该响应符合约束，不能证明供应端在所有情况下都强制了某关键词。
一次实际违约也不能直接证明课程知识内容的对错，旧编辑失败依然保持失败。

本次已准备并实际派发的探针为
`3af622bae5e83f4b67c2bee57153584dbddae1723661f5882c9cecd2c1577aec`，
私有目录 `wiki/schema-capability-probes/<probe>/`。
已经调用 run，不能重跑，也不能通过修改代码或配方刷新这三个固定测试的额度。
原课程最终编辑 section-0010/0012/0013 均不因此获得新尝试。

```powershell
$env:PYTHONPATH='src'
$env:PYTHONUTF8='1'
.venv/Scripts/python.exe -m wechat_decrypt_tool.course_schema_capabilities `
  --root output/course-knowledge `
  --probe 3af622bae5e83f4b67c2bee57153584dbddae1723661f5882c9cecd2c1577aec verify
```

代码校验及相关守护合成测试 33 项通过。它们验证诊断器的行为、只读重建、
GPU owner/完整响应要求及停止保护，不能作为真实模型运行或文章质量通过的证据。

## 2026-11-10 实际结果与独立恢复

上述 `course-defiant:q4` 探针已真实完成 3/3；另以实际作者模型 `qwen3.5:9b`
执行了相同的三个固定合成请求，每模型每测试均只有一次原调用。作者模型探针为
`fae760b8d501ed398afae664890c91c23e9e9c449bc35fe8c15948f932addf45`，
私有目录 `wiki/schema-model-probes/<probe>/`。两者均完整 stop 返回，实际上下文 4,096。

| 固定测试 | course-defiant:q4 | qwen3.5:9b |
| --- | --- | --- |
| min_properties | 空对象，违反 minProperties | 空对象，违反 minProperties |
| required_primary_fields | 符合实际 schema | 符合实际 schema |
| max_properties | 三个键，违反 maxProperties | 三个键，违反 maxProperties |

两组实际输出 tokens 均依次为 2/32/20。真实响应表明这次运行不能依赖对象的
minProperties/maxProperties 约束；明确 required 主字段在这两个合成样本中有效，
仍不保证其他请求或课程语义通过。未来尚未使用的角色可据此选择表示；
不得改写冻结角色，或为旧失败 section-0010/0012/0013 刷新预算。

结果独立副本：
`C:\Users\Administrator\Documents\CourseKnowledgeBackups\schema-probe-results-20261110-065558`。
357 文件、7,295,497 字节，manifest SHA256：
`25fbbda4c3361bf5182b2a9e47c3049cdd6a90c44fb5d0cbd3a38b6e09f408ed`。
已从复制源码重新认证两个同一结果：原始调用合计 6 次，恢复新增调用 0、原归档读取 0。
恢复禁止网络、数据库与推理；这不是模型环境备份，也不是课程质量验收。

副本中的 `backup_schema_results_20261110.py verify` 接受恢复 root、原 root、manifest 路径；
运行位置为副本 `restored/workspace`，PYTHONPATH 指向其中的 `src`。
完整恢复回执保留在私有 `wiki/schema-probe-result-restores/`。
