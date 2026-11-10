# Windows 启动器与实际 guard 的分别认证

原三课批次 `a4eb4f64f5606c90f80424ce29238406` 的[独立 guard 调度](course-ready-guard-exit.md)
保留了首份完整私有稿，并已实际启动第二门原账本。
真实 Windows venv 启动还包含一层 Python 包装进程：Popen 返回的 PID 并非 guard 自报 PID。
已观察并保存实际进程树，原控制器没有被重启或终止：

| 身份 | PID | 创建时间 |
| --- | --- | --- |
| 原调度 controller | 34556 | 1794276171.20596 |
| Popen 返回的 launcher | 16476 | 1794276175.1748905 |
| 实际 guard | 26548 | 1794276175.2320533 |
| 实际原账本 worker | 22352 | 1794276177.8860729 |

实际 guard 在 launcher 的存活子孙进程中，其创建时间和除可执行路径外的命令参数均一致。
原调度冻结后仍将 launcher 身份用于 guard 的退出校验；原账本正常完成后将被该检查拒绝。
这不是实际账本模型故障。不得把预期拒绝当成已发生终态，也不得重算正在进行的原账本。
证据保留在原执行目录的 `windows-wrapper-discovery.json`。

新 `course_private_guard_identity` 分别记录 launcher 和存活时观察到的实际 guard。
实际 guard 必须是该 launcher 或其真实后代，创建时间、命令和 PID 一致；join launcher 后
再核对同一实际 guard 已退出且其 worker 返回 0。身份替换、暂停或失败都不能通过。
本机真实 Python 子进程测试已走完观察、join、退出认证，没有模型调用。

新入口 `wechat_decrypt_tool.course_textbook_ready_wrapper_resume` 等待上述原 controller、
launcher、guard 和 worker 的准确终态。仅在原账本成功、原 controller 真实记录了预期身份错误，
原未用 writer 及最后一课角色仍未用时，才可继续原 writer 与最后一课。
没有增加原模型角色、尝试次数或语义修订额度，没有重新选择课程，没有重写首份完整稿。
等待不占全课程 worker 锁或 GPU。原 guard 和 worker 继续原样运行。

35 项相关测试通过，包括真实本机进程、错误命令、复用 PID、暂停、失败、实际退出、
准确旧终态、已用角色拒绝和已完成账本保留；Ruff 通过。
两个新模块在成功 prepare 后冻结。

## 实际独立输入恢复

私有目录：

`D:\Project\WeChatDataAnalysis\output\course-knowledge\wiki\ready-wrapper-continuations\a4eb4f64f5606c90f80424ce29238406`

副本：

`C:\Users\Administrator\Documents\CourseKnowledgeBackups\ready-wrapper-inputs-20261110-101141`

785 文件、42,841,375 字节，manifest SHA256：

`a48b308a2f8314abb7d45bc9d25b40a11331b502f2004afa6cfc58254f2c0e6f`

复制源码实际 load 同一执行、原运行中账本身份、原课程及未用角色；原根读取及模型调用 0。
副本包含第一稿完整结果原字节和此前真实独立重建回执，同一 proof
`316ce1b05070b42bfb397471b1ddcde2a432b1bedbfdb003cfb67b627536ce84`，保留 3 项未决意见。
没有在本副本再次全文重放第一稿，也没有包含当前运行中账本的未来结果。
恢复禁止网络、SQLite、推理子进程和原根目录 fallback；离线模型元数据取自已认证 manifest。
不声明媒体、模型、全量数据库或完整环境已恢复。

在副本的 `restored/workspace` 执行：

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/backup_ready_wrapper_inputs_20261110.py verify `
  output/course-knowledge D:\Project\WeChatDataAnalysis\output\course-knowledge `
  a4eb4f64f5606c90f80424ce29238406 ..\..\manifest.json
```

10:13 恢复通过后首次隐藏启动 launcher `16120`；实际新 controller 须核对，
正在运行的原账本和 guard 没有被替换。未来实际终态、完整稿及结果备份仍需逐项检查。
10:18 已实查实际新 controller `23836 / 1794276807.812756` 存活，
等待准确原账本与旧 controller 终态；原账本实际 worker `22352` 已完成 3/22 组。
180W 设置、200W/70°C 停止且不自动热恢复、两盘各 32 GiB 保留线和全部原暂停/失败记录保持。
