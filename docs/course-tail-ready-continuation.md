# 保留未完成前驱，接续同一原三课

原三课批次 `a4eb4f64f5606c90f80424ce29238406` 的三个旧控制器均真实停止于 0/3。
原三课仍是 1,372 份完整记录、560,214 字符，账本分组为 5/22/24；
未重新预留课程、重跑已接受账本或刷新作者角色。

`course_textbook_tail_ready_resume` 为同一原三课建立显式新执行。
默认模式仍要求准确的原尾部 guard/worker 完整成功；
`prepare --retain-incomplete-predecessor` 则要求前驱已退出，
其[真实失败终态](course-graph-tail-terminal.md)已被完整认证并实际独立恢复。
新执行保存 `original_predecessor_complete:false`，不重试失败前驱，不取消其剩余工作。

这次登记使用后一模式。它绑定原失败控制器、原课程、未用角色、准确前驱进程身份、
终态 proof 和两次已完成的编号图校准。启动前重新认证终态，未用作者采用既定编号图格式；
原成功/失败记录和每单元一次语义修复限制保持。新模块 prepare 后冻结。
24 项接续测试、13 项终态测试及 14 项原有限尾部测试通过，Ruff 通过。

私有执行目录：

`D:\Project\WeChatDataAnalysis\output\course-knowledge\wiki\tail-ready-continuations\a4eb4f64f5606c90f80424ce29238406`

此前默认 live 模式 prepare 因前驱已经退出而拒绝，未注册或调用角色。
其源码和失败记录保存在 `wiki/tail-ready-prepare-history/<批次>/`，不清除或改写为成功。

## 输入副本与实际独立恢复

成功副本：

`C:\Users\Administrator\Documents\CourseKnowledgeBackups\tail-ready-inputs-20261110-085323`

615 文件、39,989,586 字节，manifest SHA256：

`e4f1bd7dc401543507a9d4a208ee44975122f1fe34c19ef9dfd7008198de1fd2`

再次复制的源码实际 load 同一三课、两次原校准及全部旧控制器失败；
原角色仍未用、模型调用和原根读取均为 0。包含由单独成功终态副本复制的真实有限 registry。
本副本认证已单独完整恢复的终态证明，没有在本副本再次重放整个前驱；
`terminal_separately_independently_restored:true`、`terminal_full_replay_in_this_copy:false`。
实际控制器启动仍要对原终态做完整只读认证。
不宣称未来写作结果、媒体、模型、全量数据库或完整环境已经恢复。

首次输入副本 `tail-ready-inputs-20261110-085141` 在复制源码检查时，
因未包含第二个旧控制器的 progress.json 而失败。该副本、manifest、失败与原 helper 保留。
v2 helper 明确纳入该旧控制器的全部有限 JSON，再建立上述新副本，未改变冻结运行模块。

在成功副本的 `restored/workspace` 内设置 PYTHONPATH 为 src，执行：

```powershell
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/backup_tail_ready_inputs_v2_20261110.py verify `
  output/course-knowledge D:\Project\WeChatDataAnalysis\output\course-knowledge `
  a4eb4f64f5606c90f80424ce29238406 ..\..\manifest.json
```

## 首次运行的入口约束

控制器通过导入后的 `main()` 启动，保持模块规范名称。
冻结控制器用自己的模块名称派发后续 writer；直接以 `-m` 将它作为 `__main__` 运行
会使该子进程名称错误。这里只选择正确入口，不修改已绑定代码或课程角色。

```powershell
$env:PYTHONPATH = 'D:\Project\WeChatDataAnalysis\src'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  -c 'from wechat_decrypt_tool.course_textbook_tail_ready_resume import main; main()' `
  --root D:\Project\WeChatDataAnalysis\output\course-knowledge `
  --batch a4eb4f64f5606c90f80424ce29238406 run
```

这只是已登记、已恢复输入的首次执行方式，存在 started.json 时不得再次执行。
08:54 已隐藏启动 launcher 22600；实际 controller/guard/worker 必须另核对 PID 和创建时间。
08:56 已观察到实际 controller `14272 / 1794272083.6037016` 存活，
正在 CPU 完整认证原终态；账本和作者尚未计为完成，不能只依据旧前驱的 stopped 状态误报新故障。
完整稿还需全文覆盖、编辑、课件图文、自测及实际阅读验收，派发不计为公开成品。
180W 设置、200W/70°C 硬保护、不自动热恢复、双盘各留 32 GiB 保持。

## 第一稿完成后的实际控制器停止

第一门原作者已实际完成 4/4，guard 返回 0，退出后独立原 verify-writer 重建通过，
仍保留 3 项未决意见。原 controller 因自身同时是 guard，在内部校验时触发
`requires_tail_ready_resume_actual_original_course_writer_guard_success`，真实停止并保持原 0/3。
不能重启本控制器，也不能重新执行第一稿。
[独立 guard 退出接续](course-ready-guard-exit.md)仅处理后两课原未用角色，并保留此处全部原记录。
