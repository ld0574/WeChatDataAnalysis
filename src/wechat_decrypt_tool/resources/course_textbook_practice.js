"use strict";
(() => {
  const form = document.getElementById("choice-practice");
  if (!form) return;
  const fields = [...form.querySelectorAll("fieldset[data-question]")];
  const status = document.getElementById("choice-status");
  const submit = form.querySelector('button[type="submit"]');
  const key = "course-wiki:" + form.dataset.pack;
  const api = typeof CourseWikiLearning === "undefined" ? null : CourseWikiLearning;
  const envelope = (learning) => ({format: api.FORMAT, records: [{article: form.dataset.pack, learning}]});
  let generation = 0;
  function answers() {
    return Object.fromEntries(fields.map(field => [field.dataset.question,
      [...field.querySelectorAll("input:checked")].map(input => input.value)]));
  }
  function hideFeedback() {
    for (const node of form.querySelectorAll(".choice-feedback")) {
      node.hidden = true;
      node.replaceChildren();
    }
  }
  function save(selected, results, reset = false) {
    try {
      const before = JSON.parse(localStorage.getItem(key) || "{}");
      const next = {...before, answers: reset ? {} : Object.fromEntries(fields.map((field, i) => [i, JSON.stringify(selected[field.dataset.question])]))};
      next.assessments = results ? Object.fromEntries(results.map((row, i) => [i, row.correct])) : {};
      if (api) api.apply(api.plan(envelope(next), localStorage, "replace"), localStorage);
      else localStorage.setItem(key, JSON.stringify(next));
    } catch { status.textContent += " 本次作答未能保存，可先保留页面。"; }
  }
  function hydrate() {
    generation++;
    try {
      const saved = JSON.parse(localStorage.getItem(key) || "{}");
      fields.forEach((field, i) => {
        const selected = JSON.parse(saved.answers?.[i] || "[]");
        if (!Array.isArray(selected)) return;
        field.querySelectorAll("input").forEach(input => { input.checked = selected.includes(input.value); });
      });
    } catch { /* A damaged saved attempt does not prevent a new attempt. */ }
    hideFeedback();
  }
  function offlineGrade(selected) {
    const questions = JSON.parse(document.getElementById("offline-practice-key").textContent).questions;
    const results = questions.map(row => ({id: row.id, correct_options: row.correct, explanation: row.explanation,
      correct: selected[row.id].length === row.correct.length && selected[row.id].every(letter => row.correct.includes(letter))}));
    return {score: results.filter(row => row.correct).length, total: results.length, results};
  }
  form.addEventListener("change", () => {
    generation++;
    hideFeedback();
    status.textContent = "选择已保存，完成后提交查看解析。";
    save(answers());
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    hideFeedback();
    const selected = answers();
    const missing = fields.filter(field => selected[field.dataset.question].length === 0);
    if (missing.length) {
      status.textContent = `还有 ${missing.length} 题未作答，请完成全部题目后提交。`;
      missing[0].querySelector("input").focus();
      return;
    }
    submit.disabled = true;
    const attempt = ++generation;
    status.textContent = "正在校验…";
    try {
      let result;
      if (form.dataset.offline === "true") result = offlineGrade(selected);
      else {
        const response = await fetch(`/api/practice/${form.dataset.course}/grade`, {method: "POST",
          headers: {"Content-Type": "application/json"}, body: JSON.stringify({pack_sha256: form.dataset.pack, answers: selected})});
        if (!response.ok) throw Error("grading failed");
        result = await response.json();
      }
      if (attempt !== generation) return;
      for (const row of result.results) {
        const target = document.getElementById("feedback-" + row.id);
        const answer = document.createElement("p");
        const explanation = document.createElement("p");
        answer.textContent = `${row.correct ? "回答正确" : "需要复习"}。参考答案：${row.correct_options.join("、")}`;
        explanation.textContent = row.explanation;
        target.replaceChildren(answer, explanation);
        target.dataset.correct = String(row.correct);
        target.hidden = false;
      }
      status.textContent = `本次答对 ${result.score} / ${result.total} 题。解析已显示在各题下方。`;
      save(selected, result.results);
    } catch { if (attempt === generation) status.textContent = "暂时无法校验，请保留选择后重试；若文章已更新，请先刷新页面。"; }
    finally { submit.disabled = false; }
  });
  document.getElementById("choice-reset").addEventListener("click", () => {
    generation++;
    form.reset();
    hideFeedback();
    status.textContent = "已清空本次选择，可以重新作答。";
    save({}, undefined, true);
  });
  window.addEventListener("course-wiki-learning-imported", hydrate);
  hydrate();
})();
