# 漏标写作的原修订额度接续

`course_textbook_bounded_paragraph_resume` 继续原作者
`c2240cf9be918a77b74fb0a91f493568`，不增加写作角色、不刷新两次结构尝试和一次语义修订额度。
已完成的八个内部单元、原最终审核、旧失败及所有备份保留。

## 实际故障

10:10 左右，只读重建已确认 8/14 单元。随后第九单元 `section-0008-write`
实际两次生成均正常 stop，输出 4,905、6,220 tokens，未触及 12,288 的输出额度。
第一次 42 项任务中标注 14 项，第二次标注 25 项，分别缺少 28、17 项段落对应。
后者仍是结构失败；这只能证明对应关系缺失，不能直接断言正文覆盖或遗漏了哪些教学。
原 guard `24480`、实际作者 `23128` 已退出，returncode 1，原停止原因和两次响应保留。

先前缺失 ID 反馈确实送入第二次请求并使标注数量增加，仍未完整；不再换措辞重跑同一 write。
本接续只使用尚未调用的原 review、repair、repair-review，以及原后五单元未使用角色。

## 新约束及审核边界

选择最后一份实际完整响应，不混合两次正文。缺少标注的 17 项仅提出在全部已有正文内查找，
明确记为未验证范围；正文不改写，引用补充也不是事实支持。
先用原独立模型审核，随后无论初审是否通过都必须使用原一次语义修订，最后再独立复核。
负面审核保留，不能因修订运行结束就视作验收通过。

修订及后续尚未调用的写作角色恢复使用 coverage.paragraphs 从零索引。
解码 schema 同时要求固定数量的真实正文段落，并将所有索引上限绑定到该数量。
每个有效教学任务仍须至少一个目标段落，全部任务键仍为必填。
内部段落数量为 `max(2, min(12, ceil(任务数 / 4)))`，本单元为 11 段、索引 0–10；
这只约束内部格式，不改变概念章节、教学范围或完整原文，不按老师的时间分段。
非教学分支及逐项排除审核保留，不要求为非教学材料编造正文。

程序约束只能防止空映射和越界，不能保证解释充分；仍须按完整原文做独立事实审核、
整条原文覆盖、课件关联、整篇编辑、最多五道选择题及真实阅读验收。
先前第八单元和本次失败写作的覆盖义务均进入最终未决项，不发布私有稿或删除视频。

## 验证及首次准备

48 项相关回归与 Ruff 通过，覆盖真实作者入口、必填映射、段落数量、索引上下界、
引用与排除规则、强制原修订、负面保留、已用角色拒绝、未修改原响应及双重覆盖义务。
冻结模块 SHA256：
`1d652e3b69820c9410cf13827b34911f6fdfcf76ddb1a8121c982fc6e3e9cdd8`。

10:20 实际 prepare 已完成：原八单元认证、17 项漏标、11 个修订段落，新增模型调用 0。
prepare 不再次执行；运行、输入恢复与实际作者身份随后以真实收据记录。
私有目录为原作者
`point-mapped-resume/omitted-mapping-review/bounded-paragraph-continuation/`。
沿用版本化运行状态备份，静态证据仍不可变。

```powershell
$env:PYTHONPATH='src'
$env:PYTHONUTF8='1'
$env:PYTHONUNBUFFERED='1'
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_bounded_paragraph_resume `
  --root output/course-knowledge --job c2240cf9be918a77b74fb0a91f493568 `
  --tokenizer output/course-knowledge/wiki/whole-lesson-narrative-20261104/local-model-tokenizer.json load
```

完整退出后用 verify 只读重建实际结果；prefix 只核对实际已完成单元。
读者正文、原文、OCR、模型响应和审核理由只留本机，不进入 Git 或工具输出。
180W 当前设置、200W 保护上限、70°C 暂停且不自动热恢复、两盘各 32 GiB 保留线保持不变。

## 有限输入副本的实际恢复

副本：`C:/Users/Administrator/Documents/CourseKnowledgeBackups/bounded-paragraph-inputs-20261108-102115`。
604 文件、35,104,683 字节，manifest SHA256：
`653f2854e43957adc3588c9dd0dddd5e8cea9de277df66df674c9089a4e5623d`。
已再次复制并从独立源码实际 load，认证相同八单元、17 项遗漏和原未用修订计划，新增模型调用 0。
13 份 legacy group proof 仅按原字节哈希一对一重定位路径。
有限 SQLite 的 videos/segments/frames 为 1/302/140 行，quick_check 通过。
不含视频、音频、全部原画面、全量数据库、模型或完整运行环境；依赖主 venv。

在副本 `restored/workspace` 中将 PYTHONPATH 指向其 `src`，执行：

```powershell
& 'D:\Project\WeChatDataAnalysis\.venv\Scripts\python.exe' `
  output/course-knowledge/wiki/bounded-paragraph-restore-20261108.py `
  output/course-knowledge 'D:\Project\WeChatDataAnalysis\output\course-knowledge' `
  c2240cf9be918a77b74fb0a91f493568 `
  output/course-knowledge/wiki/whole-lesson-narrative-20261104/local-model-tokenizer.json load
```

输入恢复不能证明后续模型角色执行或整课验收通过。作业实际结束后另做结果副本及独立 verify。

10:23:03 已启动一次 guard，启动器为 `30228 / 1794104583.3567283`，
实际 Python 进程 `4860 / 1794104583.50857` 正在 CPU 认证输入；
此时尚未生成 guard-started 或新作者 started，不能把它计为新增模型角色已执行。
