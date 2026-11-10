# 完整内部稿的首次原文覆盖

`course_textbook_first_boundary_coverage` 为已完整重建、从未分配覆盖预算的内部稿，
首次检查全部原始材料到真实读者正文的覆盖。可读取原恢复作者 `index-reviewed`，
或原两课批次的第二份作者 `original-second-course`；后者必须绑定原批次。
不将内部稿当作验收或发布结果，不重新分配已有父稿的覆盖预算。

所有原记录、逐句文本、相邻原文及全部正文块均保留。
候选使用已测试的连续分组结束边界和必填最后一组，程序只无损转换成员，
不能把无效映射改成已覆盖，也不使用候选理由作为事实依据。
`course-defiant:q4` 提议，`qwen3.5:9b` 用不同权重独立审核每组。
各原角色最多两次结构尝试，零语义重试；负面审核保留未决。

## 已准备的真实任务

2026-11-10 02:36 首次准备完成，未调用生成模型：

- 父作者：`1dde57c07edb707b95bc3b9ad30e5939`，完整 14/14 单元、4 项原问题保持。
- 首次覆盖作业：`6d9d3224c4b15133be3edfc45bcb6294`。
- 完整范围：55 批、13,853 分句、535 原记录、222,135 原文字符。
- 对全部 55 份真实候选上下文做 tokenizer 预检：最大输入 38,950 tokens，
  输出预留 12,288，context 65,536；独立审核输入在实际执行时另行计量。
- 前驱为现有覆盖 `05bf20a9e03bff0d09c20bebe3fc463d` 的准确 worker 与 guard，
  绑定原 PID、创建时间和不可变执行契约哈希。

前驱仍运行时只等待并检查控制标记；两者实际成功退出后，必须完整执行原前驱的
只读结果重建，才能启动新私有 guard 和 worker。前驱失败、暂停或结果不完整均停止。
派发和 worker 都只允许首次运行，不自动重试，不从降温中自动恢复。

新父覆盖契约仍使用原 `whole-manuscript-coverage-execution.json` 位置，
防止其他入口替该父稿重新分配预算。执行契约名为 `first-boundary-coverage-execution.json`；
派发状态在 `output/course-knowledge/wiki/first-boundary-coverage-dispatch/<覆盖作业>/`，
逐批状态在 `output/course-knowledge/wiki/whole-manuscript-coverage-jobs/<覆盖作业>/`。
模块身份用规范文件名绑定，避免 CLI 的 `__main__` 与导入名称不一致。
真实 prepare 后代码冻结，不格式化或修改绑定模块。
本次模块 SHA256 为 `d9b9ad6b6658ef74d07e4796ad46b4235a6c551709d3a4b78a6fe424f3407c8b`。

81 项相关测试及 Ruff 通过。合成测试检查全部原文与正文进入实际双模型请求、
原额度不刷新、负面审核保留、schema/原响应/缓存/转换凭证篡改拒绝、
只读重放不获取 GPU 或推理、准确前驱等待及成功结果重建。
测试不能替代真实覆盖或课程语义验收。

## 独立完整有限输入恢复

首次派发前已实际验证独立副本：
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/first-boundary-inputs-20261110-023638`。
784 个不同文件、48,332,840 字节，manifest SHA256：
`84468ab3f7054d6e9c0b6d522ca30d3c9a17273cc24f648d72b784cb03a8156c`。
从再次复制的独立源码、完整父稿有限原材料和真实回执实际执行新入口 `load` 与未用角色检查，
认证同一 14/14 父稿、4 项问题、55 批和 13,853 分句，模型调用 0。
20 份旧 group proof 只按原字节哈希一对一重定位，禁止原根目录 JSON 回退。

副本包含前驱的不可变执行契约以认证等待绑定，不包含前驱的未来或完整结果。
不含模型、视频、全量数据库或完整环境，不能当作这些内容的恢复证明。
在副本 `restored/workspace` 内将 PYTHONPATH 指向其 src，可只读复现输入认证：

```powershell
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/first-boundary-input-restore-20261110.py `
  output/course-knowledge 'D:\Project\WeChatDataAnalysis\output\course-knowledge'
```

独立恢复通过后首次隐藏派发，venv launcher PID 为 `8416`；
这只表示启动派发，不表示前驱已完成、worker 已启动或新覆盖批次完成。
02:42 已核对实际派发进程 `7136 / 1794249549.7674613` 健康，
真实状态为 `waiting_for_exact_predecessor_coverage`，0 新模型调用，尚未启动新 worker。

## 运行与只读检查

设置 `PYTHONPATH=src`、`PYTHONUTF8=1`、`PYTHONUNBUFFERED=1`，使用项目 venv。
对已准备任务，`load` 认证完整父稿、同一计划和原契约；`guard` 首次等待并执行；
`verify` 只在真实完整终态重建全部原响应，缺缓存时禁止推理。
不得对已准备父稿再次执行 `prepare`，不得直接重复调用 worker。

```powershell
& .\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_first_boundary_coverage `
  --root output/course-knowledge --parent 1dde57c07edb707b95bc3b9ad30e5939 `
  --tokenizer output/course-knowledge/wiki/whole-lesson-narrative-20261104/local-model-tokenizer.json load
```

180W 设置、200W 保护上限、70°C 即暂停且不自动热恢复、C/D 各 32 GiB 保留线保持。
不重启健康来源抽取、当前覆盖或下一课作者，不清除旧暂停、失败或已消费预算。
全量范围继续为 340 节音频、2 份无声来源、全部可恢复课件；读者成品仍为 1 篇。

## 08:55 已完成的真实结果与独立恢复

原 worker `12596 / 1794258530.1522605` 和 guard `7136 / 1794249549.7674613`
均已退出，guard 返回 0。55/55 批、13,853/13,853 子句已完成，但覆盖验收没有通过：

- 已覆盖 7,867，复核通过的非教学 4,943。
- 未决 1,043，分属 43 份来源；其中原提议为 covered 的 650、non_teaching 的 393，
  独立审核均为 false。不能把它们写成已确认遗漏或直接排除。
- 当前计数中的 confirmed missing 为 0；93 份来源存在原教学账本与覆盖分类冲突。
- 原 4 项父稿问题保留，语义准确性、发布和来源删除均未通过。

所有 55 批实际都含完整正文的单一块，不是因为正文分块尚未搜索到尾部。
不重复原已接受批次，也不把这次统计完成解释成新覆盖预算。
后续必须在原负面意见保留的前提下，处理未决分类及尚未使用的实际编辑任务。

完整有限结果副本已从再次复制的源码实际只读重建成功：

`C:\Users\Administrator\Documents\CourseKnowledgeBackups\first-boundary-results-offline-20261110-085120`

1,281 文件、67,706,939 字节，manifest SHA256：

`182b753265ca68b2f0bf6078b38d0be716af5d3767affbb8e7eab68128d3735d`

实际认证 119 个原模型阶段、全部 55 批和同一 1,043 未决项，20 份旧 group proof
只按精确原字节重定位。原归档读取 0、新模型调用 0，不允许网络、推理或原根回退。
副本中的模型身份从已认证 manifest 读取，不声称本次验证了运行时模型环境。
它不是媒体、模型、全量数据库或完整环境恢复，也不是文章语义验收。

首次结果副本 `first-boundary-results-20261110-084618` 因 CoverageJob 构造时另行导入的
模型元数据函数仍尝试查询服务而被离线审计拦下。该副本、原脚本和失败日志保留。
新独立恢复驱动仅在复制 runtime 中将该元数据函数绑定到原记录身份，
重新验证同一结果，没有重跑覆盖或更改冻结代码。

在成功副本的 `restored/workspace` 设置 PYTHONPATH 为 src，并执行：

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/resume_first_boundary_result_restore_20261110.py verify `
  output/course-knowledge D:\Project\WeChatDataAnalysis\output\course-knowledge ..\..\manifest.json
```

只读编辑路由另确认 128 份不同的受影响来源：43 份含未决子句、93 份有分类冲突，重叠 8 份。
599 条未决子句的 reference 不在现有单元的主要 point citations 并集中，
其中 444 条 teaching_detected=true 仅表示原提议曾判断教学，不能代表复核通过或已确认遗漏。
这不证明原材料未被读取；它说明后续编辑不能只凭旧章节主来源映射遗漏全课待判证据。
路由不调用模型、不登记新角色、不重新运行原接受批次，也不更改任何覆盖状态。
