# 原生 OCR 与原图纠错有限试点

目标是减少 GPU 逐字重写整屏文字的开销。先用已安装的 Windows 中英文离线 OCR
生成完整草稿，再让 Qwen 查看整张原图，只返回逐行纠错、遗漏、图示关系和不确定项。
第二次独立请求仍查看原图，检查全部文字和像素依据。两次使用相同模型权重，
不等于跨模型审核，也不是文章语义验收。

## 范围及预算

本次作业为 `72d2abc1d62c22befc894c212652d442`，来源 `84ef110ea186f81ea9041ee0`，
只处理三张原先 pending、model 为空且没有旧 OCR 缓存的帧：6925、6969、7013。
失败帧 6881 和原负面结果不在本次试点中。每帧纠错及复核各一次，无结构或语义自动重试。
所有全文、词框、原图、实际请求与模型响应都只保留本机，不进入 Git。

只有纠错完整标志和第二次像素复核全部通过的帧才写回 `frames`，同时登记来源事件。
来源错误不清除，不生成旧版成功缓存，不发布文章，不授权删除视频。
失败、原始注册、调用标记及一次性预算都保留。没有成对运行旧全量 OCR 基线，
所以只能报告实际耗时及通过率，不能据此声称同质量提速倍数。

## 实际准备及启动环境恢复

源码已与原始 recipe 绑定，不得编辑或格式化：

```
course_frame_assisted_pilot.py
da4721ca48a4e36ff38cdd4e2087ffdc4276ce10d2804cca70c3db5dcbbabc91
```

第一次 CPU 准备被 `Get-FileHash` 缺失阻断，尚未开始 GPU 调用。
最小合成图片复现表明，Windows PowerShell 5 子进程继承了 PowerShell 7 的模块路径。
只把子进程 `PSModulePath` 设置为 Windows PowerShell 自带模块目录后，同一脚本成功。
不修改全局环境，不变更执行策略，不改写已经绑定的原脚本。

`course_frame_assisted_native_repair` 在原作业下只恢复一次尚未产出任何原生收据的准备阶段；
保留 `prepare-failure.json`、recipe 和三份注册，不重建 GPU 预算，也不调用模型 API。
实际六次 CPU OCR 共约 6.06 秒，三帧中英文行数为 66/67、74/71、53/40；
纠错文字输入分别为 5,558、5,876、4,067 tokens，无裁剪。
原生 OCR 的这些数字不代表正确率或文章覆盖通过。

准备恢复源码 SHA：
`ebff8e4dc5bd246ed17469d6bdfa46b3065c694e8e742a9e3fd43144dca87b08`。

## 执行与只读核验

已准备的实际作业只能启动一次：

```powershell
$env:PYTHONPATH='src'
$env:PYTHONUTF8='1'
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_frame_assisted_pilot `
  --root output/course-knowledge guard --job 72d2abc1d62c22befc894c212652d442
```

守护使用该作业自己的锁、日志和状态，不占用四个已有标准守护状态文件；
模型请求仍通过共享 GPU 锁。180W 配置、200W 上限、70°C 暂停且不自动恢复，
C/D 盘各保留 32 GiB。预检失败同样保留终止标记，降温不等于重新授权。

```powershell
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_frame_assisted_pilot `
  --root output/course-knowledge verify --job 72d2abc1d62c22befc894c212652d442
```

verify 必须等真正 worker 退出；只读重建全文 OCR、原图 SHA、实际 wire 请求、
模型响应、GPU owner、上下文容量、正常结束及原始负面，不调用推理或刷新预算。
测试已覆盖正常与负面结果、输入篡改、上下文及 GPU 错误、准备失败、单次恢复、
温度/磁盘/暂停与原守护保护；28 项测试和 Ruff 通过。

## 精确输入副本及真实启动

输入与代码副本位于：
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/frame-assisted-pilot-inputs-20261108-065718`。
286 文件、29,042,446 字节，manifest SHA：
`b4ce92c382c28851d66aa7208020e7826116eadef9880c2c9682ba45670e6c57`。
已再次复制并核对 SHA、JSON 和 Python AST，然后用独立进程从恢复目录导入代码，
重建三张原图、全部原生收据及相同实际纠错 wire 请求，新增推理为零。
该副本不含活跃数据库快照、完整模型或全部视频环境。

私有 guard 于约 06:57 实际启动，真实身份为 `20260 / 1794092263.63916`，
实际 worker 为 `15948 / 1794092264.7279787`；外层 venv launcher 不算真正 worker。
输入目录中的 `activation.json`、`guard-started.json`、`started.json` 保留实际启动链。

## 07:09 实际终态：三张均未通过

worker 和 guard 已真正退出；零推理重建结果为 0 supported、3 unresolved，实际写回 0 张。
源记录、原错误及 3 ready / 177 pending 的帧状态保持原值。

- 6925：纠错返回两个完整标志为 false，还包含空的漏字条目，被结构校验拒绝。
- 6969：纠错的完整标志为 false，保留未决。
- 7013：纠错完整标志通过，但第二次原图复核否决，保留未决。

原始响应及审核理由只在本机私有作业目录。结构失败在 `6925-failure.json`，
另外两张的负面终态在 `6969-outcome.json`、`7013-outcome.json`；没有自动重试。
`applied.json` 中 applied_frames=0，不能把其有限程序执行完毕状态解释成质量通过。

实际四次模型调用合计服务时间 113.32 秒，包含结构失败调用；其中加载 26.29 秒、
输入计算 25.62 秒、生成 60.93 秒，共生成 3,436 tokens。
整个 worker 含共享 GPU 等待约 635 秒，即 10.6 分钟；CPU 六次 OCR 另约 6.06 秒。
只读 verify 的 model_calls=0 指核验没有新增调用，不是本次试点没有使用模型。

原生草稿加原图纠错在这三个样本上未达到质量要求，暂不推广为全量成功路径。
下一步需改进密集代码画面的识别来源或专用 OCR，不能给同三帧刷新原角色预算、
把原负面改成通过或以耗时下降声称同质量提速。全量 Goal 和其他健康队列继续。

完整有限终态及输入的私有副本为
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/frame-assisted-pilot-result-20261108-071001`，
312 文件、29,095,327 字节，manifest SHA：
`5cb6054af0e79ab296dcbbbd0b2afe1f5d2bdab32d14915552ef49ad6ceaf2cd`。
再次复制后，由恢复目录代码实际重建同一 0 supported / 3 unresolved 终态，新增推理为零。
它包含这次有限试点证据，不是完整数据库或模型环境备份。
