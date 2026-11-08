# 当前可用完整来源的有限整课接续

`course_textbook_corpus_batch` 将未尝试课程的完整原文输入接到已有知识账本与整课作者。
一次最多三课，本次选择两课，遵循完整 340 节有声、2 份无声及全部可恢复课件的 Goal 范围。
批次只是执行断点，不能替代全量完成标准。

## 内容和预算

按完整 340 课目录顺序选择已有完整来源的课程，跳过已有账本 manifest、已保留的批次预约
和已交付文章。不选择或重跑旧失败课程，也不把尚未发布的来源记为输入完成。
原整条来源、相邻原文、知识任务和教学条件不截断；来源版本及完整原文逐项认证。

每课依次执行原完整知识账本、原独立复核、实际账本重建，然后首次启动概念组织与整课写作。
首次作者使用段内 point_ids 及明确的缺失任务反馈。请求、schema、原始输出、派生元数据和
独立负面全部保存；原角色结构尝试最多两次，每单元语义修订最多一次。
不改冻结模块、不更换 recipe 刷新旧额度、不自动重试失败。

产出为完整私有稿，仍待完整原文覆盖、课件关联、图文、最多五道选择题与最终编辑验收。
不会发布、不删除视频，不借用第一课的 editorial proof。

## 真实批次

批次 `60ee5378015a2d2f2a221548ba19df87` 已在 CPU 完成首次 preflight：
两课，1,027 条完整原记录，44 个调度组；新增模型调用为 0。
本次等待健康的完整覆盖作业 `4c7009067249cc972b363551ea0244c8`，
精确 guard `15712 / 1794082894.9652352`、worker `7628 / 1794082896.3737292`。
它们真正成功退出后，使用原 assignments 入口完整重建第二课全部覆盖结果，再开始两课。
不能仅凭完成状态文件、观察超时、PID 重用或未核对的 snapshot 启动。

最初拟等待第三课作者，但它在修订处实际失败，健康前驱检查拒绝了准备，未生成批次或消费额度。
本批改为等待另一项仍健康、已有准确身份的任务；第三课旧失败继续保留并单独有限恢复。
两课素材互相独立，没有将第三课失败视作成功或清除该状态。

执行中每个 ledger/writer 使用私有 guard、共享 GPU 锁和独立一次性标记。
70°C 暂停且不自动热恢复，200W 保护上限、C/D 各 32 GiB 保留线及所有已有控制标记不变。
65 项相关测试及 Ruff 通过，冻结新模块 SHA256：
`95b5b291c712e61b22b8acf4e42879f5516f39d6822328b9096e2064d9258b14`。

09:49:55 控制器启动器为 `31500 / 1794102595.853068`，实际控制器身份见进度文档。
实际 Python 控制器为 `6336 / 1794102596.002112`，已核对存活。
内部状态名 `waiting_for_exact_whole_writer` 沿用早期命名，实际前驱以 plan 的
`predecessor_kind=coverage`、覆盖作业 ID 和准确 guard/worker 身份为准。
prepare 已实际运行，不重复准备此批次；run 也只能启动一次。

```powershell
$env:PYTHONPATH='src'
$env:PYTHONUTF8='1'
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_corpus_batch `
  --root output/course-knowledge --batch 60ee5378015a2d2f2a221548ba19df87 load
```

完整作者结束后的零推理重建使用 first-verify，须明确传入实际 inventory、ledger 和 tokenizer。
运行日志、前驱证明、每课结果和失败均位于 `wiki/full-course-batches/<batch>/`。

## 输入恢复的实际边界

副本：`C:/Users/Administrator/Documents/CourseKnowledgeBackups/full-course-batch-inputs-20261108-094903`，
307 文件、37,504,003 字节，manifest SHA256
`8a71ac2ff5ff7dbdf2ef2c211bf529c0fcf574fe31ce2a76cfbbc399588fd452`。
已再次复制并由独立源码实际执行上述 load，认证相同两课全部原文、来源版本、preflight、预算和代码。
有限 SQLite 的 videos/segments/frames 为 2/659/368 行，quick_check 通过。
不包含前驱的完整覆盖证明、全量数据库、模型、视频或完整运行环境。
副本只用于上述只读 load；进程身份和完整前驱收据必须在实际运行目录重新核对。

恢复时在 `restored/workspace` 中设置 PYTHONPATH 为其 src，使用主 venv 和上述 load 命令。
输入载入通过并不代表模型作业已执行、文章完成或整课质量通过。

## 10:05 首次作者的 guard 布置

原两课控制器 `6336 / 1794102596.002112` 保持存活，仍等待原覆盖作业通过。
原覆盖已到 55/61 批；没有重启控制器或提前执行两课。

为避免后两课首次作者也将变化的 guard 状态纳入不可变材料备份，已启动独立 CPU watcher：
`31884 / 1794103302.1521664`，私有驱动为
`output/course-knowledge/wiki/full-course-guard-layout-20261108.py`。
它只处理本批两个尚未开始的作者，在首次 manifest 出现后认证原库存、实际账本、模型、
tokenizer 和代码哈希，取得原作者锁，确认尚无作者角色后才创建相对目录符号链接。
实际 guard 目标为本批 `item-XX-authoring-guard`，位于作者不可变材料目录之外。
原 guard 仍 resolve 到真实私有目录运行，温控与锁规则不变，不移动已运行的 guard。

已在本机 Python 3.11 实际验证 `Path.rglob` 不遍历该目录符号链接。
当前 watcher 真实状态为等待新作者，0/2、模型调用 0；尚未宣称两课布置完成。
状态及独立失败记录位于批次内 `guard-layout-progress.json`、
`guard-layout-failure-*.json`；每课布置成功另存 `item-XX-guard-layout.json`。

四份布置输入已独立精确复制到
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/guard-layout-inputs-20261108-100256`，
12,540 字节，manifest SHA256：
`31716a1ffc7c25440b8353a53963813e1fbf25b478a98a732f16357b5a94df09`。
这只是布置输入复制，不是运行进程恢复验证。
