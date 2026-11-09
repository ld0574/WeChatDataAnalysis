# 原覆盖任务的未用审核与边界尾部

原第三课覆盖任务 `05bf20a9e03bff0d09c20bebe3fc463d` 在完成 5/56 批后，
第六批提案两次正常 stop 但分组映射无效，耗尽原提案结构额度并退出。
原预算、原请求、失败、完整材料和已完成前缀均保留，不能把所有索引改成 0 或重新生成提案。
前五批和两份真实失败已从完整有限副本独立恢复，见 [原有序覆盖](course-ordered-coverage.md)。

`course_textbook_ordered_boundary_tail` 只接续原任务剩余角色：

- 逐份认证两次真实失败的请求链、原 schema、完整响应、模型摘要和 GPU wire 收据。
- 固定选择第一份完整失败响应，保留完整有效来源映射的 2 个成员及原判断。
- 原映射无效的 16 个来源、254 个分句保留为显式未核义务；程序只保守提出 missing，
  不推断 covered 或 non_teaching，也不创建接受的失败提案缓存。
- 原第六批尚未调用的不同权重独立审核逐组核对完整原文、相邻语境和真实正文。
  程序补入的 missing 必须实际成立才可通过；它们无论审核通过与否都不会变成已覆盖。
- 原第 7–56 批尚未使用的提案改用连续分组结束位置和必填最后一组。
  最后一组固定覆盖到当前来源最后一个目标，避免成员索引与实际 groups 数组不一致。
  边界须严格递增且组非空，不能少来源、造文章依据、丢分句或跨不连续成员。

所有来源与文章仍完整进入实际请求；程序只无损转录成员，不做语义裁决。
候选仍用 `course-defiant:q4`，独立复核仍用不同权重的 `qwen3.5:9b`。
每个原角色最多两次结构尝试、0 次语义重试，不另建作业或重新分配预算。
原不同分句覆盖义务、稿件问题和负面审核保持；不允许发布或删除来源。

新输入恢复也显式适配原 CLI 的模块键 `__main__`，每个原冻结模块哈希保持严格相同。
原覆盖代码、运行中的来源抽取和其他作者均未编辑或重启。
180W 设置、200W 保护、70°C 停止且不自动恢复、C/D 各 32 GiB 保留线不变。

## 实际准备与验证

102 项新入口及相关回归测试、Ruff 已通过。覆盖了全部成员、单成员与 256 成员来源、
不同判断的边界、无效边界拒绝、实际双模型请求和 schema/GPU 回执、原失败链篡改拒绝、
只读重放禁止推理、负面审核保留未决、原两次额度和已使用角色拒绝。
测试只使用合成材料，不能代替真实批次或语义验收。

11 月 10 日 01:55 实际首次 prepare 成功，新增推理为 0：
前五批重建 1,280 分句、75 未决、11 个真实接受阶段；保留 2 个有效成员、254 个待核成员。
剩余 51 批包括第六批原独立审核和原第 7–56 批，没有运行任何新模型角色。
准备后该新模块已冻结，SHA256：
`6bf1c5bcac1f95920876492768e6ff175e440756afdc4e7c787890bd193f7f5b`。

状态和契约位于：
`output/course-knowledge/wiki/ordered-coverage-dispatch/05bf20a9e03bff0d09c20bebe3fc463d/unused-independent-boundary-tail/`。
原提案失败与接受缓存分开保存；运行结束的 `verify` 只读重建完整真实结果，缺缓存时禁止获取 GPU。
任意失败、暂停或热限制触发均停止，不自动重试。

独立完整有限输入副本已实际恢复通过：
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/ordered-boundary-inputs-20261110-015453`。
784 个不同文件、42,124,313 字节，manifest SHA256：
`7c861b9c46140bfe9fa41c6b7647ded63b4c67a073deefb046395b2a67cde53d`。
从再次复制的独立代码、完整父稿有限材料、原真实失败和新契约实际执行 load、
前五批只读重建和剩余角色未用检查；13 份旧 group proof 严格哈希重定位，新增推理 0。
副本不含未来结果、模型、视频、全量库或完整环境。

在副本 `restored/workspace` 内将 PYTHONPATH 指向该副本 src，可只读复现：

```powershell
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/ordered-boundary-input-restore-20261110.py `
  output/course-knowledge 'D:\Project\WeChatDataAnalysis\output\course-knowledge'
```

01:58 已首次派发私有 guard，venv launcher 为 `20724 / 1794247079.7911334`；
实际 guard 和实际 worker 身份须另以 guard-started / worker-started 核对。
派发成功不是第六批审核或新边界模型阶段已完成，完整计划仍为 56 批。

对原前五批真实接受候选做零推理格式对照，逐组全部成员及判断可原样往返还原：
1,280 分句的同 tokenizer 紧凑序列化从 6,859 降到 4,443 tokens。
它只验证表示无损及序列化减少，不是实际输出或端到端推理提速；未重跑这五批。
观察文件 `wiki/performance-observations-20261110/1794247011131998000-accepted-boundary-lossless-serialization.json`，
SHA256 `038a022b38df058fac996a6442059398a9a06cd6273ecce3fba3994da3afca30`，
精确副本位于原 performance-observations 备份目录。

02:11 实际边界提案已首次通过：原第 7 批 256 分句、13 组，使用原第一次尝试。
真实输入 37,828 tokens、输出 4,012 tokens、生成 86.919 秒、正常 stop；后续不同权重独立审核也已结束。
累计已完成 8/56 批、2,048 分句，并继续推进；不是同材料受控速度比较。
02:16 已到 9/56 批、2,304 分句，正在第 10 批独立审核。
原第六批的 254 个待核义务及原未决仍保留，不把结构成功当成语义通过或发布验收。

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
& .\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_ordered_boundary_tail `
  --root output/course-knowledge --parent c2240cf9be918a77b74fb0a91f493568 `
  --tokenizer output/course-knowledge/wiki/whole-lesson-narrative-20261104/local-model-tokenizer.json load
```

`guard` 只允许首次派发，`worker` 不能重复启动；`verify` 要求真实完整终态。
这些命令不能清除任何新暂停标记，也不能替代用户验收。
