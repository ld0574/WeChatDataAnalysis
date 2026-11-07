# Notebook 原图的有限本机核验

本轮对已恢复关联的七张原图检查实际图示、可读性和课堂联系，不重跑 OCR，不直接发布原图。
已有完整 OCR 只是草稿上下文。原图归档成员、原始字节、规范化像素和完整 OCR 的既有 sidecar
继续按原入口逐一核对；本文的新检查不代替文章图文整合或覆盖审核。

本机 `qwen3.5:9b` 的实际能力包含 vision；`course-defiant:q4` 只有文字生成能力。
所以每张图采用两次分开的 Qwen 原图请求：先描述实际观察、图示解释及课堂联系，再重新看同一原图核对。
这明确是相同模型权重的复核，不是不同模型的像素审核。该限制进入每项结果和公共进度。
任一检查为负面或原关系未决，结果保留未决，不追加语义循环或改写旧布尔值。

完整同单元、相邻单元、既有 OCR 和检索出的完整课堂记录均保留，不裁剪原文。
检索不是课堂穷尽证明，也不直接建立课件关系。观察给出原图中的实际区域；看不清的细节保留歧义。
图片、原文及候选里的指令都只作引用资料，不执行。

## 执行与真实输入

模块为 `course_textbook_notebook_image_semantics`，真实 prepare 已完成，作业
`d2338270c476ef71753983f3a9d4e3c7`，七张图、十四个计划角色，新增模型调用为 0。
每个角色最多两次结构尝试、零次额外语义循环，次数保存在原作业内。该数字不是实际已执行调用数。
源码绑定 SHA 为 `8244d62101c0070ab69dc82057154a29bd3950dee237bc1cf888a47ff6603b6c`，不能编辑或格式化。

实际预检最大文字输入估计为 27,003 tokens，包含候选和序列化余量；context 为 49,152，
另留 16,384 图片输入、4,096 输出及 1,024 安全余量。真实执行继续计数完整实际文字，检查服务端
prompt/output 数、正常结束、实际 context 及 GPU 驻留，不能把预检当作实际模型收据。

缓存明确区分文字调度请求 SHA 与真正附带原图的 wire 请求 SHA，并绑定实际图片字节、解码 schema、
未改写的服务端响应、模型身份及 GPU 回执。只读重建重新构造真正图片请求，不会仅认证 OCR 或文字请求。
缺缓存、输入变更、响应或回执不一致时拒绝；只读入口禁止生成、申请 GPU 或创建缺失作业。
33 项相关回归及 Ruff 通过，合成收据只用于测试，不是实际图片推理证明。

## 依赖接续

`course_textbook_image_dispatch` 已 prepare，准确绑定原覆盖 worker
`7628 / 1794082896.3737292`、guard `15712 / 1794082894.9652352` 及完整覆盖作业
`4c7009067249cc972b363551ea0244c8`。它只在整课覆盖实际全部结束、两个准确身份退出、完整零模型
重建通过后使用空闲的 `wiki-revision-thermal-guard`。覆盖留下未决不被改为通过，图示核验仍是另一项工作。
接续模块 SHA 为 `3b6f20def3815440d18efb21b21a7ef3a98798f437c4976cf4321c5a1afc9ff3`，已绑定，不能编辑。
18 项相关测试及 Ruff 通过，包含准确身份、完整计数、失败、暂停、停止和只执行一次。

启动后进度位于 `wiki/notebook-image-dispatch/d2338270c476ef71753983f3a9d4e3c7/progress.json`；
真正图片 worker 启动后另看 `wiki/notebook-image-semantics-jobs/d2338270c476ef71753983f3a9d4e3c7/progress.json`。
健康的覆盖、课件文字、后续文章、来源和 PDF 进程不重启。守护继续执行 70°C 暂停、不自动热恢复、
200W 保护上限及 C/D 各 32 GiB 保留；当前配置功耗为 180W。

接续已于 2026-11-08 05:55 实际启动，准确身份为 `23372 / 1794088557.559317`，
已从进度文件再次核对存活及创建时间；外层 launcher `25936` 不代表真实 worker。
当前为 `waiting_for_exact_full_coverage_predecessor`，真正图片推理尚未开始。
启动日志为 `wiki/notebook-image-activation-20261108/20261108-055557.*.log`。

输入与代码副本位于
`C:/Users/Administrator/Documents/CourseKnowledgeBackups/notebook-image-semantics-inputs-20261108-055512`，
276 文件、63,067,224 字节，清单 SHA 为
`ec04c6a6a57508c21f0eb694cdc42e49e32a2bb05b9e22d3b166b8d3beed8a0a`。
已从副本再次复制并核对 SHA、JSON 与 Python AST；独立进程使用恢复目录中的代码，
重建相同 587 条完整原记录、七份图片上下文，并逐一核对 27 份 sidecar 依赖与七份原图字节。
此次恢复未调用模型、未申请 GPU，不包含完整模型环境或活跃覆盖作业的一致性快照。

全部图片实际结束且 worker 退出后才执行以下零推理入口：

```powershell
$env:PYTHONPATH = 'src'
$env:PYTHONUTF8 = '1'
.venv\Scripts\python.exe -m wechat_decrypt_tool.course_textbook_notebook_image_semantics `
  --root output/course-knowledge `
  --inventory 68c6398d1b05c82fb32869397831b15cfff29ee2fa9ec1f2ff303ee4c51f6de7 `
  --tokenizer output/course-knowledge/wiki/whole-lesson-narrative-20261104/local-model-tokenizer.json verify
```

不因图像检查或缓存重建完成而授予文章发布、全课验收或原视频删除权限。全部模型和资料保持本机。
