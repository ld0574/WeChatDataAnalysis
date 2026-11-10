# 剩余单元的原图修复角色

07:58 左右，原图接续作者 `f8bc01ff9ffde2fd4e584e9f1ffe5912` 在
`section-0012-write` 耗尽原两次结构尝试，完成 12/16 单元。
guard `23756 / 1794266308.476975` 实际返回 1，worker `16476` 已退出。
等待它的第三份三课执行也停止 0/3，原停止和失败保持，不能重新运行。

两次都是完整 stop 返回，图分别含 1/2 个未知端点；第二次还有 17 个空任务映射。
实际提示分别 21,599/21,601 tokens，输出 3,344/3,642 tokens，生成 61.555/66.609 秒。
这是两个被拒绝的候选，不是完整稿，也不能修改图端点后放行。
只读重建已认证全部 12 个前缀单元、上次原修复、两次失败及剩余角色未用，新增模型调用 0。

## 有限尾部执行

`course_textbook_graph_tail_roles` 只登记剩余的原四单元，不重做前 12 单元、账本或规划。
原第 9 单元的图修复由原专用 reader 重放，保留其旧失败、初审、修复和终审。
当前单元只使用原来尚未调用的初审、一次语义修复及终审；原失败候选仍是不可信审核输入。
即使初审未发现问题，修复仍必须执行。不得生成第三份写作请求或伪造 accepted write 缓存。

后三单元保留原成功写作路径。如果原写作的两次结构尝试因同类图问题耗尽，
必须先认证两个真实请求、完整返回、模型摘要、实际 schema、全部原文与原图错误，
再登记该单元的原未用审核/修复/终审。它不会重新调用已耗尽的写作角色。
非图错误、缺失证据、已用角色、修复额度耗尽或暂停均停止，不能改 recipe 刷新预算。
这些分派在本次显式有限合同中登记，不是在失败后自动重跑旧执行。

每个失败单元独立保留不可变执行、原角色结果和编排派生凭证。程序不增删节点、猜连线、
补任务归属或把模型判断当作事实。所有旧负面意见和新图问题继续进入完整稿未决项。
新编号图格式仍仅用于原三课的未用作者，不修改本作者的冻结格式。

私有目录：

`output/course-knowledge/wiki/graph-tail-original-roles/f8bc01ff9ffde2fd4e584e9f1ffe5912/`

新入口的 `prepare` 和独立输入恢复不会申请 GPU；实际 worker 要求先存在成功的独立恢复回执。
实际 run 使用共享全课程锁和独立 guard，不能重复启动；原健康覆盖和来源抽取不重启。
温控保持 180W 设置、200W/70°C 硬保护、不自动热恢复，C/D 各保留 32 GiB。

```powershell
$env:PYTHONPATH = 'src'
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_graph_tail_roles `
  --root output/course-knowledge --writer f8bc01ff9ffde2fd4e584e9f1ffe5912 prepare
```

此 prepare 只能登记一次；不能作为恢复或重试命令再次执行。
实际整个 worker 和 guard 成功退出后，须使用本入口的 `verify`，以认证新增单元派生凭证；
旧 graph-write 或 one-based verifier 不能替代该重建。完整结果还需另做独立备份和恢复。

14 项新测试及 46 项相邻测试通过，Ruff 通过。测试覆盖原成功路径、仅图故障分派、
原角色只用一次、只读禁止推理、修复失败终止、历史负面保留和恢复前禁止派发。
这些不代表真实修复、完整稿或课程语义验收通过。
当前公开文章仍为 1 篇；340 节有声、2 份无声及全部可恢复课件的全量 Goal 保持。

## 实际输入恢复与首次派发

成功副本：

`C:\Users\Administrator\Documents\CourseKnowledgeBackups\graph-tail-inputs-20261110-081633`

1,122 文件、48,082,945 字节，manifest SHA256：

`f0fff3389bbb6f6ef87588d728c4f730095080a786f10c94f852daa7fd7c1639`

再次复制的源码已实际重建相同的 12/16 前缀、上次图修复、两次失败、2 项前缀未决意见和剩余角色未用。
44 份旧绝对路径证明只按相同原字节一对一重定位；真实有限数据库为 2 来源/659 段/368 帧，
quick_check 通过。恢复禁止网络、推理和原归档 fallback；原模型身份只读取已认证的复制 manifest。
原归档读取和新增模型调用均为 0。副本不含未来完整结果、媒体、模型、全库或运行环境。

在副本 `restored/workspace` 将 PYTHONPATH 指向其中 `src`：

```powershell
python output/course-knowledge/wiki/backup_graph_tail_inputs_20261110.py verify output/course-knowledge D:\Project\WeChatDataAnalysis\output\course-knowledge f8bc01ff9ffde2fd4e584e9f1ffe5912 ..\..\manifest.json
```

08:20 已首次受控启动，launcher `16836`，guard `33244 / 1794270010.2194068`，
包装 worker `7848`，实际 Python worker `6044 / 1794270012.370835`，均已观察存活。
worker 先在 CPU 重建原证据；只有重建及独立恢复回执认证通过后才消耗剩余原角色。
此时原作者进度仍保存旧停止状态，不能据此误报新 worker 已失败或已完成新单元。
guard 约 60.2°C、177.4W、设置 180W；C 49.09 GiB、D 41.40 GiB。
旧三课 controller 仍停止；本派发不自动解除它们，也不代表完整稿或新公开文章完成。

## 原修复已耗尽，尾部保持未完成

上述原尾部执行后来在 `section-0012-repair` 耗尽两次结构尝试，guard 返回 1。
完成仍为 12/16，不再使用本入口重启或给原修复增加尝试。
原初审、两份失败返回、前缀与未用后续角色均已[认证并独立恢复](course-graph-tail-terminal.md)。
另三门原未用课程通过[显式接续](course-tail-ready-continuation.md)推进，不以该稿完成为前提。
