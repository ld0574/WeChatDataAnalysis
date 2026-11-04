"use strict";
(() => {
  const host = document.getElementById("learning-records");
  if (!host || typeof CourseWikiLearning === "undefined") return;
  const api = CourseWikiLearning;
  const revision = document.body.dataset.articleRevision;
  const current = typeof revision === "string" && /^[a-f0-9]{64}$/.test(revision);
  const envelope = (records) => ({format: api.FORMAT, records});
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  const content = element("div", undefined, "learning-content");
  const status = element("p", "", "learning-status");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const report = (text) => { status.textContent = text; };
  const fields = [];
  let timer;
  function getCurrent() {
    if (!current) return {};
    const raw = localStorage.getItem("course-wiki:" + revision);
    return api.parse(JSON.stringify(envelope([
      {article: revision, learning: raw === null ? {} : JSON.parse(raw)},
    ]))).records[0].learning;
  }
  function save(changes, quiet = false) {
    try {
      const before = getCurrent();
      const next = {...before, ...changes};
      for (const name of ["answers", "assessments"]) {
        if (changes[name]) next[name] = {...before[name], ...changes[name]};
      }
      api.apply(api.plan(envelope([{article: revision, learning: next}]), localStorage, "replace"), localStorage);
      if (!quiet) report("已保存到当前浏览器。可导出学习记录备份。");
      return true;
    } catch {
      report("本次更改未保存。浏览器存储不可用、空间不足或原记录异常；请先复制当前答案并保留已有备份。");
      return false;
    }
  }
  function hydrate() {
    try {
      const value = getCurrent();
      for (const field of fields) {
        const entry = field.group ? value[field.group]?.[field.index] : value[field.name];
        if (field.node.type === "checkbox") field.node.checked = entry === true;
        else field.node.value = typeof entry === "string" ? entry : "";
      }
      resume.disabled = !current || !(value.scroll > 0);
      report(`当前浏览器已保存 ${api.collect(localStorage).records.length} 个文章版本的学习记录。`);
    } catch {
      report("无法读取部分学习记录。请保留已有备份；当前输入仍可复制保存。");
    }
  }
  function checkbox(label, name) {
    const row = element("label");
    const node = element("input");
    node.type = "checkbox";
    node.dataset.learningField = name;
    node.addEventListener("change", () => save({[name]: node.checked}));
    row.append(node, document.createTextNode(label));
    fields.push({node, name});
    return row;
  }
  content.append(element("p", "阅读位置、答案和自评保存在当前浏览器。不同文章版本分别保留；换浏览器或清理浏览器前，请先导出备份。"));
  const flags = element("div", undefined, "learning-actions");
  const resume = element("button", "回到上次阅读位置");
  resume.type = "button";
  resume.disabled = true;
  resume.addEventListener("click", () => {
    try { window.scrollTo({top: getCurrent().scroll || 0, behavior: "auto"}); }
    catch { report("读取位置失败，请先保留已有备份。"); }
  });
  if (current) {
    flags.append(checkbox("已读完", "read"), checkbox("需要复习", "review"), resume);
    content.append(flags);
    document.querySelectorAll("#reading-self-test > section").forEach((section, index) => {
      const practice = element("div", undefined, "practice");
      const label = element("label", "先写下你的答案");
      const answer = element("textarea");
      answer.id = "practice-answer-" + index;
      answer.rows = 4;
      answer.maxLength = 20000;
      label.htmlFor = answer.id;
      answer.addEventListener("input", () => save({answers: {[index]: answer.value}}));
      const self = element("label", undefined, "assessment");
      const assessment = element("input");
      assessment.type = "checkbox";
      assessment.addEventListener("change", () => save({assessments: {[index]: assessment.checked}}));
      self.append(assessment, document.createTextNode("我能独立解释，并达到掌握标准"));
      fields.push({node: answer, group: "answers", index}, {node: assessment, group: "assessments", index});
      practice.append(label, answer, self);
      section.insertBefore(practice, section.querySelector("details"));
    });
    const savePosition = () => {
      if (window.scrollY > 0 && save({scroll: Math.min(100000000, window.scrollY)}, true)) resume.disabled = false;
    };
    window.addEventListener("scroll", () => {
      clearTimeout(timer);
      timer = setTimeout(savePosition, 500);
    }, {passive: true});
    window.addEventListener("pagehide", () => { clearTimeout(timer); savePosition(); });
  }
  const backup = element("div", undefined, "learning-actions");
  const exportButton = element("button", "导出学习记录");
  exportButton.type = "button";
  exportButton.id = "learning-export";
  exportButton.addEventListener("click", () => {
    try {
      const data = api.serialize(api.collect(localStorage));
      const url = URL.createObjectURL(new Blob([data], {type: "application/json"}));
      const link = element("a");
      link.href = url;
      link.download = "course-wiki-learning.json";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
      report("学习记录已导出。备份包含本浏览器保存的旧版和新版记录。");
    } catch {
      report("学习记录导出失败。请保留原有备份并复制当前答案，检查浏览器存储是否可读。");
    }
  });
  backup.append(exportButton);
  content.append(backup, element("p", "也可导入旧阅读页导出的学习记录。导入只在本机浏览器中处理。"));
  const fileLabel = element("label", "选择学习记录 JSON 备份");
  const file = element("input");
  file.type = "file";
  file.id = "learning-import-file";
  file.accept = ".json,application/json";
  fileLabel.htmlFor = file.id;
  const modeLabel = element("label", "记录冲突时");
  const mode = element("select");
  mode.id = "learning-import-mode";
  modeLabel.htmlFor = mode.id;
  for (const [value, label] of [["keep", "保留本机已有记录"], ["replace", "用备份替换同版本记录"]]) {
    const option = element("option", label);
    option.value = value;
    mode.append(option);
  }
  const preview = element("p", "选择文件后会先显示导入预览。", "learning-preview");
  preview.setAttribute("role", "status");
  const apply = element("button", "导入预览中的记录");
  apply.type = "button";
  apply.id = "learning-import-apply";
  apply.disabled = true;
  let imported, plan, generation = 0;
  function prepare() {
    plan = undefined;
    apply.disabled = true;
    if (!imported) return;
    try {
      plan = api.plan(imported, localStorage, mode.value);
      preview.textContent = `预览：${plan.total} 个版本，${plan.conflicts} 个已有记录，将导入 ${plan.changes.length} 个，保留 ${plan.skipped} 个。`;
      apply.disabled = plan.changes.length === 0;
    } catch {
      preview.textContent = "无法建立导入预览，尚未写入任何记录。请核对备份和浏览器存储。";
    }
  }
  file.addEventListener("change", async () => {
    const version = ++generation;
    imported = plan = undefined;
    apply.disabled = true;
    const selected = file.files?.[0];
    if (!selected) { preview.textContent = "选择文件后会先显示导入预览。"; return; }
    try {
      if (selected.size > api.MAX_BYTES) throw Error("size");
      const value = api.parse(await selected.text());
      if (version !== generation) return;
      imported = value;
      prepare();
    } catch {
      if (version === generation) preview.textContent = "备份格式无效或超过 16 MiB，尚未写入任何记录。";
    }
  });
  mode.addEventListener("change", prepare);
  apply.addEventListener("click", () => {
    if (!plan) return;
    try {
      const result = api.apply(plan, localStorage);
      plan = undefined;
      apply.disabled = true;
      hydrate();
      preview.textContent = `导入完成：${result.imported} 个版本，保留 ${result.skipped} 个已有记录。`;
    } catch (error) {
      plan = undefined;
      apply.disabled = true;
      preview.textContent = error.rollbackIncomplete
        ? "导入中断，部分记录未能恢复。请保留备份并核对本机记录。"
        : "导入未完成。本机记录可能已变化或存储不可写；请重新选择文件并核对预览。";
    }
  });
  content.append(fileLabel, file, modeLabel, mode, preview, apply, status);
  host.append(content);
  window.addEventListener("storage", (event) => {
    if (event.key === null || event.key?.startsWith("course-wiki:")) {
      plan = undefined;
      apply.disabled = true;
      report("其他页面更新了学习记录。当前输入保留；刷新页面可读取最新记录。");
      if (imported) preview.textContent = "其他页面更新了本机记录，请重新核对冲突策略以建立预览。";
    }
  });
  hydrate();
})();
