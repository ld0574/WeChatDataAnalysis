# 已审核讲解的新主题编排

`wechat_decrypt_tool.course_textbook_gap_placement` 接在真实私有整合稿和独立结果恢复之后。
它处理尚无主题位置的已审核原样讲解，不重写段落、不重新运行原补充编辑或原失败审核。
全量目标仍为 340 节有声课程、2 份无声来源及全部可恢复课件，公开读者文章仍只有 1 篇。

本次真实位置诊断中，7 个原覆盖组的 85 个目标依赖尚未进入正文的段落：
22 个目标涉及与现有正文逐字相同的两份候选，另外 63 个目标依赖五份尚无位置的讲解。
这不意味着 85 个确认遗漏；精确文字相同也不证明引用或语义覆盖正确。
其余 545 条原未决、53 份来源矛盾、父稿问题和全部未采用段落继续保留。

## 有限的新角色

首次 prepare 只读重放旧补充的 47 个真实角色，并核对整合稿、实际独立恢复回执和位置依赖。
快照包含完整旧稿、全部旧段落/元数据、相关完整原记录与上下文、确切的原分组成员，
以及其余未决项的完整编号集合。没有裁剪原文或按时间窗口安排文章。

新阶段只有一次布局提案和一次不同权重的上下文审核；两者均只有一次原调用额度。
布局只能为五段原样讲解选择现有小节之后的位置、知识主题标题及段落顺序，或保留待处理。
原来审核失败、未映射或缺少已解决教学组的段落不能进入本次编排。
不同权重审核逐段检查事实、引用、标题、前置知识与衔接，并逐组检查原文中的每个目标成员。
两份逐字相同的候选还须通过此次实际覆盖审核；不因程序找到相同文字而自动清除义务。

若布局中的任何已安排段落未通过，整份新布局保留，不私自删除负面部分后发布另一份未审稿。
覆盖审核为正、且其所需全部正文确实存在时，才记录该组的有限依赖复核。
这只改变本次 85 条依赖的状态，不继承候选的全课覆盖结论，不处理其余 545 条原未决，
也不清除 53 份来源矛盾、父稿意见或原失败。没有通过图文、课件、自测和实际阅读验收前仍为私有稿。

新代码与输入从首次 prepare 起冻结。原剩余补充、整合模块、旧响应与预算保持不变。
GPU 沿用共享锁、180W 设置、200W/70°C 保护与不自动热恢复；C/D 各保留 32 GiB。
不会中断健康的来源抽取或作者。模型、完整正文、OCR 与审核理由只保存在本机 output。

## 命令与恢复边界

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
& .\.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_gap_placement `
  --root output/course-knowledge --parent 87e51e9142e9ab14315873679be8dd58 prepare
```

同一整合稿只允许登记一次。后续使用 prepare 返回的 job，运行 `--job <job> load`、
`guard` 或真实完整退出后的 `verify`。`verify` 严格重建两个真实请求、响应、解码 schema、
模型摘要及最终私有稿，缺少缓存时禁止新增推理；不能用未来结果或合成测试冒充真实完成。

首次运行前需从复制的代码和完整有限快照实际独立 load，并核对全部文件哈希、原样主题段落和
原生小节位置。私有驱动为：

`D:\Project\WeChatDataAnalysis\output\course-knowledge\wiki\backup_gap_theme_placement_20261110.py`

它的 `inputs` 模式恢复输入快照，不重复执行旧补充角色的全文重放；`results` 模式在新 worker
和真实 guard 成功退出后，才从新复制代码重建两个新角色及同一私有整合结果。
恢复禁止原归档回退、网络、SQLite、推理子进程及 GPU 获取，不包含媒体、模型、全量库或完整环境。
旧结果的原字节和此前重放范围仍由[前驱恢复文档](course-gap-assembly.md)说明。

本入口与原整合入口的 28 项相关检查和 Ruff 已通过，包括完整保留、位置顺序、负面布局隔离、
完整依赖、精确重复不自动放行、缺失/重复/虚构成员拒绝和只读恢复禁止推理。
这些检查不代表真实课程布局、语义质量或全文阅读验收已经通过。

首次真实准备在零模型调用、尚未登记任何新角色时因重复传输完整正文而停止，原失败保留。
新请求只传一次完整拟整合正文，全部原文、图示、段落和完整证据仍在。
新布局角色按实际 65,536 context 核验，预留 8,192 输出和额外开销，输入上限 55,296；
每次真实调用前再次核验完整输入，超出即停止，禁止裁剪或放宽当前旧角色的额度。

## 实际准备、独立输入恢复和首次派发

12:16 首次成功 prepare 的 job 为 `0004742fe367e2a1228454ed270b2361`。
实际完整审核探针为 54,279 tokens，所有原正文、图示、相关完整原记录和原目标均保留；
新小节的成员用实际段落编号指向同一份完整正文，避免再次传输相同文字。
第一次容量预检失败没有注册新角色或调用模型，失败文件仍在原准备记录目录。

新输入快照 SHA256：
`3b7b78f54461e52a244e3a1144b4b38f17feb8a81e90a95d097d600e3f6829a7`。
实际输入副本：

`C:\Users\Administrator\Documents\CourseKnowledgeBackups\gap-theme-inputs-20261110-121603`

330 文件、28,554,210 字节，manifest SHA256：
`aca67c7342777d8f6c65c127be95e110129fd8d164cc8ef25901ad4cf5ee853b`。
从复制代码实际 load 同一完整快照、原样主题段落和原生小节位置通过；
原归档回读 0、新模型调用 0，没有再次重放旧补充角色或恢复媒体、模型、全量数据库、完整环境。

在该副本 `restored/workspace` 内执行：

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/backup_gap_theme_placement_20261110.py verify `
  output/course-knowledge D:\Project\WeChatDataAnalysis\output\course-knowledge `
  0004742fe367e2a1228454ed270b2361 ..\..\manifest.json inputs
```

实际 guard `35040 / 1794284183.945457`、实际 worker `10844 / 1794284185.0870616`
均已实查存活；guard 的 venv launcher 与实际 worker 身份分开记录，最近为 theme-layout 等待共享 GPU。
12:17 已准备一次 CPU 收尾并隐藏派发：

`D:\Project\WeChatDataAnalysis\output\course-knowledge\wiki\finish_gap_theme_placement_20261110.py`

它绑定上述两个准确身份、代码及输入，等待成功退出后才重放新两角色并独立恢复实际结果；
任何暂停、失败或输入变化都保留并停止，不启动模型或自动重试，不把派发计为结果通过。
此刻尚未产生新布局结果，公开文章仍为 1 篇。
