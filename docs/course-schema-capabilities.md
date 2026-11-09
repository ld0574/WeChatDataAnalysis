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
