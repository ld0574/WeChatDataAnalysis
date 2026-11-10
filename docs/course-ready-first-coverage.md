# 原三课批次首份完整稿的首次全文覆盖

`wechat_decrypt_tool.course_textbook_ready_first_coverage` 为原批次
`a4eb4f64f5606c90f80424ce29238406` 的已完成第 0 项接入首次全文覆盖。
父作者为 `42b874e8debbb2216fc0964740bbdb5e`，真实完整稿为 4/4 单元，仍有 3 项意见。
这不是此前失败的新主题布局，也不重新分配其已消费额度；原两个完整稿的质量义务各自保持。
全量范围仍为 340 节有声、2 份无声及全部可恢复课件，公开读者文章仍为 1 篇。

## 父稿认证和执行边界

首次 capture 实际零推理重建完整原作者，与已经独立恢复的原 proof 完全一致：
`316ce1b05070b42bfb397471b1ddcde2a432b1bedbfdb003cfb67b627536ce84`。
原实际 writer guard 成功退出、原完整稿、3 项意见和先前恢复回执均核对。
新不可变快照保留完整 inventory、working、私有稿、阅读投影、全部意见及原证据文件哈希。
后续 load 核对快照、完整正文/目录哈希及每份原证据字节，不在每次覆盖 load 时重复执行原作者重放。
副本中的原账本/作者字节校验不等于再次全文重放这些旧角色；其此前完整恢复范围见
[原守护退出修正](course-ready-guard-exit.md)。

新入口通过作用域内的 provider 接入已冻结的首次覆盖模块，退出时恢复原函数，
没有修改原覆盖模块、作者、健康抽取或正在运行的下一份作者。
同一父稿必须尚无任何覆盖契约；首次登记后，后续只使用同一首次计划和原角色额度。
输入和代码从实际 prepare 起冻结，失败、暂停及温控不会触发自动重试或热恢复。

原全文覆盖规则保持：全部完整原记录、无损分句、所有正文块和原待核教学义务均在范围内。
候选和不同权重审核逐项检查覆盖、未讲清和非教学分类，负面不被计为通过；
既不以同术语替代充分讲解，也不把账本、旧候选或程序分界当作事实依据。
关联课件、解释图、选择自测、跨课联系和实际阅读验收仍独立待完成。

34 项新入口/原首次覆盖相关检查及 Ruff 通过，覆盖完整父稿、负面意见保持、
原实际恢复、活进程拒绝、重复预算拒绝、作用域恢复、完整原字节变动和输入恢复门槛。
这些检查不是全课语义验收，也不允许发布或删除视频。

## 实际首次准备和独立输入恢复

新 job：`179fca82ac083cda4d6e257bece3f140`。
真实计划为 14 批、228 份完整来源、3,473 分句、55,702 原文字符；父稿 3 项意见保持。
14 个候选请求完整预检通过，最大输入 22,297 tokens，输出预留 12,288，最大 context 49,152。
独立审核仍在真实执行前按其实际完整输入测量，不提前宣称所有语义审核成功。

实际输入副本：

`C:\Users\Administrator\Documents\CourseKnowledgeBackups\ready-first-coverage-inputs-20261110-123745`

420 文件、30,659,843 字节，manifest SHA256：
`9dfef438ccc216ec468012007e7ae0a061e35b0b39becbe5ba71e004b1fac432`。
复制源码实际重建同一完整快照、14 批及全部 3,473 分句计划，原归档读取和新增模型调用均为 0。
没有在此副本重新执行旧作者/账本的角色重放，也没有恢复媒体、模型、全量库或完整环境。
离线模型元数据只取自已认证执行契约，离线 archive facade 不查询 SQLite 或模拟在线模型环境。
网络、SQLite、推理子进程、GPU 获取及原归档回退均被禁止。

私有恢复驱动：

`D:\Project\WeChatDataAnalysis\output\course-knowledge\wiki\backup_ready_first_coverage_20261110.py`

在上述副本 `restored/workspace` 内执行：

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/backup_ready_first_coverage_20261110.py verify `
  output/course-knowledge D:\Project\WeChatDataAnalysis\output\course-knowledge `
  42b874e8debbb2216fc0964740bbdb5e `
  wiki/whole-lesson-narrative-20261104/local-model-tokenizer.json ..\..\manifest.json inputs
```

## 首次派发及后续核对

12:39:26 首次隐藏派发 launcher `29164`；实际 guard 和 worker 身份必须另核对，
不能把 venv launcher、旧的同号 PID 或启动成功计为覆盖完成。
任务使用共享 GPU 锁、180W 设置、200W/70°C 保护、不自动热恢复和 C/D 各 32 GiB 保留线。
未重启健康来源或当前原作者；本次准备和输入恢复没有新增模型调用。

实际完成退出后，用同一入口的 `verify` 只读重建全部新覆盖角色，再单独恢复真实结果。
输入副本不能代替未来结果；覆盖结束仍须保留所有未决和原 3 项意见，不能直接发布。

12:40 实际 guard `4972 / 1794285567.1191356`、实际 worker
`27524 / 1794285571.0999236` 均已核对存活，worker 最近为第 0 批等待共享 GPU，完成 0/14。
guard 的 `worker_pid=17236` 是 venv launcher；实际 worker 由自己的 started/progress
及真实父进程关系确认，没有把 launcher 当作 worker。

12:41:22 首次隐藏派发有限 CPU 收尾，绑定上述两个精确身份、输入与恢复驱动：

`D:\Project\WeChatDataAnalysis\output\course-knowledge\wiki\finish_ready_first_coverage_20261110.py`

它等待原 worker/guard 实际成功退出，才完整重建新覆盖结果并独立恢复实际新角色。
任何原失败、暂停、温控、身份替换或输入变化均保留并停止，不获取 GPU、不新增模型调用，
不会自行重跑本覆盖或原作者。12:41 派发时仍没有完成的覆盖批次或独立结果恢复。

12:44 对真实首批进行了额外的零推理只读重建：256 个分句全部已解决、0 未决，
认证 3 个实际角色，结果 SHA256：
`d5900cb709a17f6b070a63cb22a29182876859763c50c05110b9311301b69b6c`。
私有回执为 `wiki/first-boundary-coverage-dispatch/179fca82ac083cda4d6e257bece3f140/first-batch-prefix-proof.json`。
这个有限前缀不是整课结论，不清除父稿 3 项意见，也不能替代未来完整结果恢复。

12:47 实查同一真实 guard、worker 和 CPU 收尾均存活且创建时间一致。
实时覆盖已完成 2/14 批、512/3,473 分句，当前为第 2 批候选验证；
这与另行只读重建的首批 256 分句是不同范围，不混作两批均已额外重建。
原第二门课作者完成 3/14 单元，来源保持 458 段转写并继续 OCR；健康进程均未重启。
GPU 最近 60.1°C、176.6W、180W 设置，两盘 C 47.20 GiB、D 41.27 GiB，保护条件不变。
