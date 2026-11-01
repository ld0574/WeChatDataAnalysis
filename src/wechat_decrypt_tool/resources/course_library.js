"use strict";
const main = document.querySelector("main");
const outline = document.querySelector("#outline");
let routeVersion = 0,
  activeReading = null,
  saveTimer;
const el = (tag, text, cls) => {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
};
const link = (text, href) => {
  const n = el("a", text);
  n.href = href;
  return n;
};
const time = (seconds) => {
  const n = Math.floor(seconds);
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`;
};
async function api(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) {
    let message = `读取失败 (${r.status})`;
    try {
      message = (await r.json()).detail || message;
    } catch {}
    throw new Error(message);
  }
  return r.json();
}
function stored(key) {
  try {
    return JSON.parse(localStorage.getItem(`course-wiki:${key}`) || "{}");
  } catch {
    return {};
  }
}
function save(key, value) {
  try {
    localStorage.setItem(`course-wiki:${key}`, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}
function notice(text) {
  return el("div", text, "notice");
}
function snapshotNotice(catalog) {
  if (catalog.published_snapshot) main.append(notice("正在阅读已发布 Wiki 的独立快照。包含创建时的文章与引用图片，不含全部课程素材或实时整理进度。阅读和自测记录保存在当前浏览器，更换浏览器或端口需另行迁移。"));
}
function heading(title, eyebrow) {
  if (eyebrow) main.append(el("div", eyebrow, "eyebrow"));
  main.append(el("h1", title));
  document.title = `${title} · 课程 Wiki`;
}
function toc(items) {
  outline.replaceChildren(el("p", "阅读目录", "eyebrow"));
  for (const [title, id] of items) {
    const a = link(title, `#${id}`);
    a.addEventListener("click", (event) => {
      event.preventDefault();
      document.getElementById(id)?.scrollIntoView({ behavior: "auto" });
    });
    outline.append(a);
  }
}
function list(values) {
  const ul = el("ul");
  for (const v of values) ul.append(el("li", v));
  return ul;
}
function searchForm(label, submit) {
  const form = el("form", undefined, "search");
  const input = el("input");
  input.type = "search";
  input.placeholder = label;
  input.setAttribute("aria-label", label);
  input.required = true;
  const b = el("button", "搜索");
  b.type = "submit";
  form.append(input, b);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submit(input.value);
  });
  return form;
}
function refs(node, ids, packet) {
  const span = el("span", undefined, "citations");
  for (const ref of [...new Set(ids)]) {
    const s = packet.sources[ref];
    if (!s) continue;
    const a = link(
      `[${s.kind === "document" ? sourceLocation(s) : time(s.start)}${s.image_sha256 ? " 图" : ""}]`,
      "#source-" + ref,
    );
    a.setAttribute("aria-label", `查看出处 ${s.title} ${sourceLocation(s)}`);
    a.addEventListener("click", (event) => {
      event.preventDefault();
      const target = document.getElementById("source-" + ref);
      if (target) {
        target.open = true;
        target.scrollIntoView({ behavior: "auto" });
      }
    });
    span.append(a);
  }
  node.append(span);
}
function sourceLocation(source) {
  if (source.kind !== "document") return `${time(source.start)}–${time(source.end)}`;
  if (source.page) return `课件第 ${source.page} 页`;
  if (source.slide) return `课件第 ${source.slide} 张`;
  return "课件片段 " + source.id;
}
function claim(value, packet) {
  const p = el("p", value.text);
  refs(p, value.citations, packet);
  return p;
}
function image(ref, caption) {
  const f = el("figure");
  const img = el("img");
  img.src = ref.kind === "document"
    ? `/wiki/image/${ref.image_sha256}.${ref.image_extension}`
    : `/library/frame/${ref.image_sha256}`;
  img.alt = caption;
  img.loading = "lazy";
  img.addEventListener(
    "error",
    () => img.replaceWith(el("p", "原始画面暂不可读取，请核对来源。", "error")),
    { once: true },
  );
  f.append(
    img,
    el(
      "figcaption",
      `${caption} · ${ref.kind === "document" ? sourceLocation(ref) + (ref.region ? " · 原图裁切区域 " + ref.region.join(", ") : "") : "原始课程画面 " + time(ref.start)}（图文对应待复核）`,
    ),
  );
  return f;
}
function articleRow(row) {
  const r = el("div", undefined, "row");
  const h = el("h3");
  h.append(
    link(
      row.title,
      "#article=" + row.id + (row.section ? "/" + row.section : ""),
    ),
  );
  const state = stored(row.id);
  r.append(
    h,
    el("span", "模型草稿 · 语义待复核", "status"),
    el(
      "span",
      `${row.questions} 道自测 · ${row.images} 处正文配图${row.source_images === undefined ? "" : ` · ${row.source_images} 张出处原图`} · ${row.gaps} 项资料缺口`,
      "meta",
    ),
  );
  if (state.read || state.review)
    r.append(
      el(
        "p",
        `${state.read ? "已读" : "未标记已读"}${state.review ? " · 待复习" : ""}`,
        "meta",
      ),
    );
  if (row.excerpt)
    r.append(
      el("p", row.excerpt),
      el("p", `命中：${row.heading} · 本地关键词匹配`, "meta"),
    );
  return r;
}
async function home(version) {
  const [wiki, catalog] = await Promise.all([
    api("/wiki/api/catalog"),
    api("/library/api/catalog"),
  ]);
  if (version !== routeVersion) return;
  heading("把知识读成文章", "本地课程 Wiki");
  snapshotNotice(wiki);
  main.append(
    el(
      "p",
      "按主题理解原理、做法和限制，再用自测检验自己能否解释与应用。原始课程和图文出处随时可查。",
      "lead",
    ),
  );
  const counts = el("p", "", "meta");
  const status = el("p", "", "meta");
  status.setAttribute("role", "status");
  main.append(counts);
  main.append(
    notice(
      "当前文章为本地模型草稿。引用编号与结构已检查，概念、图文对应和答案仍待语义复核；当前资料不足以替代或删除原视频。",
    ),
  );
  function updateStatus() {
    counts.textContent = `${wiki.articles.filter(r => !r.superseded_by).length} 篇当前主题草稿 · ${wiki.articles.length} 个保留版本 · ${catalog.published} / ${catalog.requested} 节课程已发布来源笔记`;
    if (!wiki.build) {
      status.textContent = "";
      return;
    }
    const names = {
      waiting_gpu: "主题试点正在等待现有 GPU 任务让出时隙",
      generating: "正在本机生成主题试点",
      assembling: "正在合并与检查章节",
      paused: "文章整理已暂停，断点已保留",
      deferred_gpu: "GPU 等待已结束，试点尚未生成",
      interrupted: "主题试点进程已退出，任务需要续跑",
      failed: "试点生成失败，未发布半成品",
      draft_published: "主题试点草稿已发布，等待内容复核",
      needs_more_sources: "本次修订未能从现有来源完成文章，未发布不完整草稿",
    };
    const step = wiki.build.stage;
    const labels = {
      outline: "提纲",
      introduction: "概述与前置知识",
      exercises: "自测与易错点",
      complete: "合并校验",
      interrupted: "当前步骤",
    };
    const stageName =
      labels[step] ||
      (step?.startsWith("section-")
        ? `正文第 ${Number(step.slice(8)) + 1} 节`
        : step?.startsWith("chapter-") ? `正文第 ${Number(step.slice(8)) + 1} 章`
        : step?.startsWith("self-check-") ? `第 ${Number(step.slice(11)) + 1} 道自测`
        : step?.startsWith("repair-") ? "修订已标记的内容" : "");
    const completed = wiki.build.completed_stages;
    status.textContent =
      (names[wiki.build.state] || "主题试点状态待核对") +
      (stageName ? ` · ${stageName}` : "") +
      (Number.isInteger(completed)
        ? ` · ${completed}/${wiki.build.total_stages} 步已保存`
        : "");
  }
  main.append(status);
  updateStatus();
  const results = el("div");
  let query = "";
  const render = async (q) => {
    query = q;
    results.replaceChildren();
    let rows = wiki.articles.filter(r => !r.superseded_by);
    if (q.trim()) {
      results.append(el("p", "正在本地检索主题正文…", "meta"));
      try {
        rows = (await api("/wiki/api/search?q=" + encodeURIComponent(q))).items;
      } catch (error) {
        if (version === routeVersion && query === q)
          results.replaceChildren(el("p", error.message, "error"));
        return;
      }
      if (version !== routeVersion || query !== q) return;
      results.replaceChildren();
    }
    for (const row of rows) results.append(articleRow(row));
    if (!rows.length)
      results.append(
        el(
          "p",
          q
            ? "没有匹配的主题正文或自测。可以到来源课程中检索原始笔记。"
            : "主题文章尚未发布。来源笔记正在整理，不能将片段摘要当作完整 Wiki。",
          "empty",
        ),
      );
  };
  main.append(searchForm("搜索主题文章", render), results);
  render("");
  toc([["主题文章", "content"]]);
  async function poll() {
    if (version !== routeVersion) return;
    try {
      const next = await api("/wiki/api/catalog");
      if (version !== routeVersion) return;
      const changed =
        JSON.stringify(next.articles) !== JSON.stringify(wiki.articles);
      wiki.articles = next.articles;
      wiki.build = next.build;
      updateStatus();
      if (changed) render(query);
    } catch {
      if (version === routeVersion)
        status.textContent =
          "进度暂时无法更新，已显示的内容仍可阅读；稍后自动重试。";
    }
    if (version === routeVersion) setTimeout(poll, 10000);
  }
  setTimeout(poll, 10000);
}
async function article(id, version, targetSection) {
  const [value, versions] = await Promise.all([
    api("/wiki/api/article/" + encodeURIComponent(id)), api("/wiki/api/catalog"),
  ]);
  if (version !== routeVersion) return;
  const a = value.article,
    p = value.packet,
    state = stored(id);
  activeReading = id;
  main.append(link("← 返回主题文章", "#home"));
  heading(a.title, "主题文章 / 本地草稿");
  snapshotNotice(versions);
  const metadata = versions.articles.find(r => r.id === id);
  if (metadata?.superseded_by) main.append(link("已有更新修订草稿 →", "#article=" + metadata.superseded_by));
  if (p.revision) main.append(link("查看修订前版本与其学习记录", "#article=" + p.revision.parent_article));
  main.append(
    notice(
      "模型草稿 · 来源结构与引用编号已检查 · 语义、图片相关性和主题完整性待复核。自测为自评，不自动认定掌握。",
    ),
  );
  if (p.scope_note) main.append(notice(p.scope_note));
  main.append(claim(a.overview, p));
  const reviewBox = el("details", undefined, "source-crosscheck");
  reviewBox.append(el("summary", "来源对照 · 本机模型辅助检查"));
  const reviewStatus = el("p", "正在读取对照记录…", "muted");
  reviewBox.append(reviewStatus);
  main.append(reviewBox);
  async function readCrosscheck() {
    try {
      const checked = await api("/wiki/api/review/" + encodeURIComponent(id));
      if (version !== routeVersion) return;
      if (checked.state === "model_crosscheck_complete") {
        const counts = checked.summary;
        if (counts.unsupported || counts.partial) {
          const warning = notice(`来源对照提示：${counts.unsupported} 项依据不足或矛盾、${counts.partial} 项只有部分支持。这篇草稿需要修订，请展开下方对照并核对原文。`);
          reviewBox.before(warning);
        }
        reviewStatus.textContent = `模型判断：有原文支持 ${counts.supported} 项、部分支持 ${counts.partial} 项、依据不足或矛盾 ${counts.unsupported} 项、无法判断 ${counts.unclear} 项。逐字引句已核对，但判断仍可能错误；这不是独立语义验收，也未核对原图。`;
        const labels = { supported: "有原文支持", partial: "部分支持", unsupported: "依据不足或矛盾", unclear: "无法判断" };
        for (const r of checked.reviews) {
          const row = el("div", undefined, "result");
          const sectionMatch = /^section-(\d+)/.exec(r.target);
          const target = sectionMatch ? "section-" + sectionMatch[1] : r.target.startsWith("quiz-") ? "quiz" : r.target.startsWith("pitfalls-") ? "pitfalls" : "objectives";
          const title = sectionMatch ? a.sections[Number(sectionMatch[1])].heading : r.target.startsWith("quiz-") ? "自测题 " + (Number(r.target.slice(5)) + 1) : r.target.startsWith("pitfalls-") ? "易错点" : "概述与前置知识";
          const jump = link(`${title} · ${labels[r.verdict]}`, "#article=" + id + "/" + target);
          row.append(jump, el("p", r.reason));
          for (const witness of r.evidence) {
            row.append(el("blockquote", witness.quote));
            refs(row, [witness.source], p);
          }
          reviewBox.append(row);
        }
      } else if (["waiting_gpu", "crosschecking", "batch_complete", "preparing"].includes(checked.state)) {
        reviewStatus.textContent = `来源对照正在${checked.state === "waiting_gpu" ? "等待 GPU" : "进行"}，已完成 ${checked.completed_batches}/${checked.total_batches} 批。文章仍待复核。`;
        if (version === routeVersion) setTimeout(readCrosscheck, 10000);
      } else {
        reviewStatus.textContent = checked.state === "not_started" ? "尚未生成来源对照记录，文章准确性仍待复核。" : "来源对照已中断或失败，未完成的检查不能当作通过。";
      }
    } catch {
      if (version === routeVersion) reviewStatus.textContent = "对照记录暂不可读，正文与自测仍可使用；文章准确性待复核。";
    }
  }
  readCrosscheck();
  const omissionBox = el("details", undefined, "source-omissions");
  omissionBox.append(el("summary", "遗漏检查 · 原文是否讲进文章"));
  const omissionStatus = el("p", "正在读取遗漏检查…", "meta");
  omissionBox.append(omissionStatus);
  main.append(omissionBox);
  async function readOmissions() {
    try {
      const report = await api("/wiki/api/completeness/" + encodeURIComponent(id));
      if (version !== routeVersion) return;
      if (report.state === "model_omissions_complete") {
        const c = report.summary;
        omissionStatus.textContent = `本机 ${report.model} 判断：已讲到 ${c.covered} 段、部分讲到 ${c.partial} 段、遗漏 ${c.missing} 段、模型认为无需展开 ${c.not_essential} 段、无法判断 ${c.unclear} 段。只检查本篇选定原文；模型可能漏判或误判，不代表整门课程或主题完整。`;
        if (report.unassigned_source_ids.length) omissionBox.append(notice(`另有 ${report.unassigned_source_ids.length} 份固定来源未进入本次章节检查，仍待补查。`));
        if (c.missing || c.partial || c.unclear) omissionBox.before(notice("遗漏检查发现待补内容，请展开检查原文与章节；这篇文章仍是草稿。"));
        const labels = { covered: "已讲到", missing: "遗漏", partial: "部分讲到", not_essential: "模型认为无需展开", unclear: "无法判断" };
        for (const chapter of report.chapters) {
          const chapterBox = el("details", undefined, "omission-chapter");
          const pending = chapter.reviews.filter(r => ["missing", "partial", "unclear"].includes(r.coverage)).length;
          chapterBox.append(el("summary", `${chapter.heading} · ${pending} 个范围待核查`));
          for (const row of chapter.reviews) {
            const result = el("div", undefined, "result");
            result.append(link(`${chapter.heading} · ${labels[row.coverage]}`, "#article=" + id + "/section-" + chapter.stage.slice(8)), el("p", row.reason), el("blockquote", row.original.quote));
            refs(result, [row.original.source], p);
            for (const target of row.article_evidence) {
              const section = /^section-\d+/.exec(target)?.[0] || (target.startsWith("quiz-") ? "quiz" : target.startsWith("pitfalls-") ? "pitfalls" : "objectives");
              result.append(link("对照文章位置", "#article=" + id + "/" + section));
            }
            chapterBox.append(result);
          }
          omissionBox.append(chapterBox);
        }
      } else if (["waiting_gpu", "generating", "assembling"].includes(report.state)) {
        omissionStatus.textContent = `遗漏检查${report.state === "waiting_gpu" ? "正在等待 GPU" : "正在进行"} · ${report.completed_stages}/${report.total_stages} 章已保存。`;
        setTimeout(readOmissions, 10000);
      } else {
        omissionStatus.textContent = report.state === "not_started" ? "尚未检查原文中的知识是否被文章遗漏。已有句子有出处，不等于内容完整。" : "遗漏检查未完成，断点保留；不能当作完整性检查通过。";
      }
    } catch {
      if (version === routeVersion) omissionStatus.textContent = "遗漏检查记录暂不可读取；正文仍可阅读，完整性待复核。";
    }
  }
  readOmissions();
  const body = el("article", undefined, "article-body");
  const sections = [
    ["学习目标", "objectives"],
    ...a.sections.map((s, i) => [s.heading, "section-" + i]),
    ["易错点", "pitfalls"],
    ["自测与解析", "quiz"],
    ["资料缺口", "gaps"],
    ["原始出处", "sources"],
  ];
  toc(sections);
  const objectives = el("section");
  objectives.id = "objectives";
  objectives.append(el("h2", "学习目标"), list(a.objectives));
  if (a.prerequisites.length) {
    objectives.append(el("h3", "前置知识"));
    for (const c of a.prerequisites) objectives.append(claim(c, p));
  }
  body.append(objectives);
  a.sections.forEach((s, i) => {
    const section = el("section");
    section.id = "section-" + i;
    section.append(el("h2", s.heading));
    for (const c of s.paragraphs) section.append(claim(c, p));
    if (s.code) {
      section.append(el("p", "课程原样代码摘录 · 未执行验证；片段依赖周围代码，请结合完整出处和版本条件阅读。", "meta"));
      const pre = el("pre");
      const selected = p.chapter_plan?.[i]?.code_excerpt;
      const original = selected && p.sources[selected.source]?.text;
      let displayedCode = s.code;
      if (original && original.slice(selected.start, selected.end) === s.code) {
        const lineStart = original.lastIndexOf("\n", selected.start - 1) + 1;
        const indentation = original.slice(lineStart, selected.start);
        // Retain the source line's leading whitespace for nested statements.
        if (/^[\t ]*$/.test(indentation)) displayedCode = indentation + s.code;
      }
      pre.append(el("code", displayedCode));
      section.append(pre);
      refs(section, s.code_citations, p);
    }
    if (s.image) section.append(image(p.sources[s.image], s.image_caption));
    const pages = [...new Set([...s.paragraphs.flatMap(c => c.citations), ...s.code_citations])]
      .filter(ref => ref !== s.image && p.sources[ref]?.kind === "document" && p.sources[ref].image_sha256);
    if (pages.length) {
      const originals = el("details", undefined, "source-pages");
      originals.append(el("summary", `查看本章引用的课件原页（${pages.length} 张）`));
      originals.append(el("p", "按本章实际引用展示原页，便于核对文字和代码；原页不代表模型解释已正确。", "meta"));
      for (const ref of pages) originals.append(image(p.sources[ref], "本章引用的课件原页"));
      section.append(originals);
    }
    body.append(section);
  });
  const pitfalls = el("section");
  pitfalls.id = "pitfalls";
  pitfalls.append(el("h2", "易错点与限制"));
  for (const c of a.pitfalls) pitfalls.append(claim(c, p));
  body.append(pitfalls);
  const quiz = el("section");
  quiz.id = "quiz";
  quiz.append(
    el("h2", "检验自己是否理解"),
    el(
      "p",
      "先用自己的话作答，再查看答案和判断标准。你的作答只保存在此浏览器，不发送给模型。",
      "meta",
    ),
  );
  const saved = el("p", "", "meta");
  function persist() {
    saved.textContent = save(id, {
      ...state,
      scroll: stored(id).scroll || state.scroll || 0,
    })
      ? "学习记录已保存在本机"
      : "浏览器无法保存，请备份自己的作答";
  }
  state.answers ||= {};
  state.assessments ||= {};
  a.quiz.forEach((q, i) => {
    const box = el("section", undefined, "quiz");
    box.append(el("h3", `${i + 1}. ${q.question}`));
    const label = el("label", "我的作答");
    const input = el("textarea");
    input.id = "answer-" + i;
    label.htmlFor = input.id;
    input.value = state.answers[i] || "";
    input.addEventListener("input", () => {
      state.answers[i] = input.value;
      persist();
    });
    const details = el("details");
    details.append(
      el("summary", "查看答案、解析与判断标准"),
      el("p", q.answer),
      el("p", q.explanation),
      list(q.criteria),
    );
    refs(details, q.citations, p);
    const assessment = el("label");
    const check = el("input");
    check.type = "checkbox";
    check.checked = !!state.assessments[i];
    check.addEventListener("change", () => {
      state.assessments[i] = check.checked;
      persist();
    });
    assessment.append(
      check,
      document.createTextNode(" 对照标准后，我能独立解释这道题（自评）"),
    );
    box.append(label, input, details, assessment);
    quiz.append(box);
  });
  quiz.append(saved);
  body.append(quiz);
  const gaps = el("section");
  gaps.id = "gaps";
  gaps.append(
    el("h2", "资料缺口与待核对项"),
    list(
      a.gaps.length
        ? a.gaps
        : ["模型未报告额外缺口；这不代表主题覆盖已通过独立复核。"],
    ),
  );
  body.append(gaps);
  const sources = el("section");
  sources.id = "sources";
  sources.append(
    el("h2", "原始出处"),
    el(
      "p",
      "以下是本篇固定引用的原文与原图。语音时间为分块范围，不能当作逐字对齐时间。",
      "meta",
    ),
  );
  for (const [ref, s] of Object.entries(p.sources)) {
    const d = el("details", undefined, "source-ref");
    d.id = "source-" + ref;
    d.append(
      el(
        "summary",
        `${s.title} · ${sourceLocation(s)} · ${s.kind === "document" ? "课件摘录" : s.kind === "frame" ? "原始画面" : "语音转写"}`,
      ),
      el("p", s.text, "source-text"),
    );
    if (s.image_sha256) d.append(image(s, s.kind === "document" ? "保留的课件图像" : s.text));
    if (s.kind === "document") {
      d.append(el("p", `${s.relative_path} · ${s.locator}`, "meta"));
      d.append(el("p", `摘录范围仅为本片段；${s.review_required ? "机器提取文字与图示解读待复核" : "提取文字，排版与语义待复核"}。固定版本 ${s.revision.slice(0, 12)}。`, "meta"));
    } else d.append(link("查看所属课程笔记", "#lesson=" + s.video));
    sources.append(d);
  }
  body.append(sources);
  main.append(body);
  const learning = el("div", undefined, "learning");
  for (const [key, label] of [
    ["read", "标记已读"],
    ["review", "加入复习"],
  ]) {
    const l = el("label");
    const c = el("input");
    c.type = "checkbox";
    c.checked = !!state[key];
    c.addEventListener("change", () => {
      state[key] = c.checked;
      persist();
    });
    l.append(c, document.createTextNode(" " + label));
    learning.append(l);
  }
  const exportButton = el("button", "导出本文学习记录");
  exportButton.addEventListener("click", () => {
    const blob = new Blob(
      [
        JSON.stringify(
          { article: id, title: a.title, learning: stored(id) },
          null,
          2,
        ),
      ],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob);
    const l = link("", url);
    l.download = `wiki-learning-${id}.json`;
    l.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  learning.append(exportButton);
  main.append(learning);
  if (state.scroll) {
    const b = el("button", "继续上次阅读位置");
    b.addEventListener("click", () =>
      window.scrollTo({ top: state.scroll, behavior: "auto" }),
    );
    main.insertBefore(b, body);
  }
  if (targetSection)
    document
      .getElementById(targetSection)
      ?.scrollIntoView({ behavior: "auto" });
}
async function sources(version) {
  const c = await api("/library/api/catalog");
  if (version !== routeVersion) return;
  heading("来源课程", "来源导航");
  main.append(
    el(
      "p",
      `${c.published} / ${c.requested} 节可阅读。未发布课程继续显示，避免把少量完成内容当作全量。`,
      "lead",
    ),
  );
  const results = el("div");
  main.append(
    searchForm("搜索课程原文笔记", (q) => {
      location.hash = "search=" + encodeURIComponent(q);
    }),
    results,
  );
  let group = "";
  for (const l of c.lessons) {
    if (l.collection !== group) {
      group = l.collection;
      results.append(el("h2", group));
    }
    const r = el("div", undefined, "row");
    r.append(
      l.state === "draft_available"
        ? link(l.title, "#lesson=" + l.id)
        : el("span", l.title),
    );
    r.append(
      el(
        "p",
        l.state === "draft_available"
          ? "来源笔记可读 · " + l.sections + " 个片段"
          : "尚未发布 · 整理中",
        "meta",
      ),
    );
    results.append(r);
  }
  toc([["课程清单", "content"]]);
}
async function lesson(id, version, targetSection) {
  const l = await api("/library/api/lesson/" + encodeURIComponent(id));
  if (version !== routeVersion) return;
  main.append(link("← 来源课程", "#sources"));
  heading(l.title, "来源笔记 / 按授课时间排列");
  main.append(
    notice(
      "此页保留片段摘要与转写，供查证使用。片段问题没有完整答案，不能当作 Wiki 自测。",
    ),
  );
  toc(l.sections.map((s) => [time(s.start) + " " + s.title, s.id]));
  for (const s of l.sections) {
    const section = el("section");
    section.id = s.id;
    section.append(
      el("h2", `${time(s.start)} · ${s.title}`),
      el("p", s.summary),
    );
    for (const c of s.concepts)
      section.append(el("h3", c.name), el("p", c.explanation));
    const d = el("details");
    d.append(el("summary", "展开原始语音转写"));
    for (const ref of s.context) {
      const e = l.evidence[String(ref)];
      d.append(
        el("p", `${time(e.start)}–${time(e.end)}  ${e.text}`, "source-text"),
      );
    }
    section.append(d);
    const frames = Object.values(l.evidence).filter(
      (e) => e.kind === "frame" && e.start >= s.start && e.start <= s.end,
    );
    if (frames.length) {
      const pictures = el("details");
      pictures.append(el("summary", `查看原始画面（${frames.length} 张）`));
      for (const f of frames) pictures.append(image(f, f.text));
      section.append(pictures);
    }
    main.append(section);
  }
  if (targetSection)
    document
      .getElementById(targetSection)
      ?.scrollIntoView({ behavior: "auto" });
}
function downloadLearning(text, name) {
  const blob = new Blob([text], {type: "application/json"});
  const url = URL.createObjectURL(blob);
  const anchor = link("", url);
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function learningRecords(version) {
  const catalog = await api("/wiki/api/catalog");
  if (version !== routeVersion) return;
  heading("学习记录", "本机备份与恢复");
  main.append(el("p", "导出全部文章版本的作答、自评、已读、复习标记和阅读位置，换浏览器后可从 JSON 文件恢复。文件在本机读取，作答不上传服务器。", "lead"));
  const summary = el("p", "", "meta");
  const status = el("p", "", "meta");
  status.setAttribute("role", "status");
  const exportAll = el("button", "导出全部学习记录");
  function updateSummary() {
    try {
      summary.textContent = `当前浏览器保存 ${CourseWikiLearning.collect(localStorage).records.length} 个文章版本的记录。旧版本与新版本分开保留。`;
    } catch (error) { summary.textContent = error.message; }
  }
  exportAll.addEventListener("click", () => {
    try {
      const data = CourseWikiLearning.collect(localStorage);
      downloadLearning(CourseWikiLearning.serialize(data), "course-wiki-learning.json");
      status.textContent = `已导出 ${data.records.length} 个文章版本的记录。请保存下载的 JSON 文件；它不包含课程正文与图片。`;
    } catch (error) { status.textContent = `导出失败：${error.message}`; }
  });
  main.append(summary, exportAll);
  const box = el("section");
  box.append(el("h2", "从备份恢复"));
  const fileLabel = el("label", "选择学习记录备份");
  const file = el("input");
  file.type = "file";
  file.accept = ".json,application/json";
  file.id = "learning-backup";
  fileLabel.htmlFor = file.id;
  const modeLabel = el("label", "相同文章版本已有记录时");
  const mode = el("select");
  mode.id = "learning-conflict-mode";
  modeLabel.htmlFor = mode.id;
  for (const [value, text] of [["keep", "保留本机已有记录"], ["replace", "使用备份中的记录"]]) {
    const option = el("option", text);
    option.value = value;
    mode.append(option);
  }
  const preview = el("p", "支持批量备份及原有的单篇学习记录 JSON；最大 16 MiB。选择文件后先预览，再确认导入。", "meta");
  preview.setAttribute("role", "status");
  const apply = el("button", "导入记录");
  apply.disabled = true;
  let data = null, plan = null, selection = 0;
  function prepare() {
    apply.disabled = true;
    plan = null;
    if (!data) return;
    try {
      plan = CourseWikiLearning.plan(data, localStorage, mode.value);
      const known = new Set(catalog.articles.map(row => row.id));
      const unknown = data.records.filter(row => !known.has(row.article)).length;
      preview.textContent = `备份含 ${plan.total} 个文章版本；本机已有 ${plan.conflicts} 个，将${mode.value === "keep" ? "保留" : "覆盖"}这些版本的本机记录；本次导入 ${plan.changes.length} 个。` +
        (unknown ? ` 其中 ${unknown} 个版本当前资料中未找到，记录仍会保留，之后可再次导出。` : "") +
        " 记录只对应原文章编号，不会把旧版作答或自评迁移到新稿。";
      apply.disabled = plan.changes.length === 0;
    } catch (error) { preview.textContent = `无法预览：${error.message}`; }
  }
  file.addEventListener("change", async () => {
    const request = ++selection;
    data = null; plan = null; apply.disabled = true;
    const chosen = file.files?.[0];
    if (!chosen) { preview.textContent = "未选择备份文件。已有记录未改变。"; return; }
    preview.textContent = "正在本机读取备份…";
    try {
      if (chosen.size > CourseWikiLearning.MAX_BYTES) throw new Error("文件超过 16 MiB 限制。");
      const parsed = CourseWikiLearning.parse(await chosen.text());
      if (version !== routeVersion || request !== selection) return;
      data = parsed;
      prepare();
    } catch (error) {
      if (version === routeVersion && request === selection) preview.textContent = `备份未导入：${error.message}`;
    }
  });
  mode.addEventListener("change", prepare);
  apply.addEventListener("click", () => {
    if (version !== routeVersion || !plan || apply.disabled) return;
    apply.disabled = true;
    try {
      const result = CourseWikiLearning.apply(plan, localStorage);
      status.textContent = `已导入 ${result.imported} 个文章版本，保留本机已有记录 ${result.skipped} 个。已读与自评不代表自动认定掌握。`;
      updateSummary();
      prepare();
    } catch (error) {
      status.textContent = error.message;
      // Require a fresh preview after any failure, including concurrent edits.
      plan = null;
      preview.textContent = "请重新选择备份文件或调整冲突选项，再预览导入。";
    }
  });
  const fields = el("div", undefined, "learning");
  fields.append(fileLabel, file, modeLabel, mode);
  box.append(fields, preview, apply, status);
  main.append(box);
  updateSummary();
  toc([["学习记录", "content"]]);
}
async function terms(query, version) {
  const data = await api("/library/api/terms?q=" + encodeURIComponent(query));
  if (version !== routeVersion) return;
  heading("术语索引", "来源词条");
  main.append(
    notice("同名词条按原文汇集；尚未认定不同课程中的术语完全等价。"),
    searchForm("查找术语", (q) => {
      location.hash = "terms=" + encodeURIComponent(q);
    }),
    el("p", `${data.total} 个匹配，显示前 60 个`, "meta"),
  );
  for (const t of data.items) {
    const r = el("div", undefined, "row");
    r.append(
      link(t.name, "#term=" + t.id),
      el("p", `出现于 ${t.lesson_count} 节课`, "meta"),
    );
    main.append(r);
  }
  toc([["术语", "content"]]);
}
async function term(id, version) {
  const t = await api("/library/api/term/" + encodeURIComponent(id));
  if (version !== routeVersion) return;
  heading(t.name, "术语 / 保留各课原始解释");
  for (const o of t.occurrences) {
    const r = el("div", undefined, "row");
    r.append(
      el("p", o.explanation),
      link(
        `${o.title} · ${time(o.start)}`,
        "#lesson=" + o.video + "/" + o.section,
      ),
    );
    main.append(r);
  }
  toc([["原始解释", "content"]]);
}
async function search(query, version) {
  const d = await api("/library/api/search?q=" + encodeURIComponent(query));
  if (version !== routeVersion) return;
  heading("来源笔记检索", "本地关键词匹配");
  const count = el("p", undefined, "meta");
  const results = el("div");
  const more = el("button", "继续加载结果");
  const failure = el("p", undefined, "error");
  failure.setAttribute("role", "status");
  let shown = 0;
  let next = null;
  main.append(
    searchForm("搜索原文笔记", (q) => {
      location.hash = "search=" + encodeURIComponent(q);
    }),
    count, results, more, failure,
  );
  function appendPage(page) {
    for (const r of page.items) {
      const row = el("div", undefined, "row");
      row.append(
        link(
          `${r.title} · ${time(r.start)}`,
          "#lesson=" + r.video + "/" + r.section,
        ),
        el("h3", r.heading),
        el("p", r.summary),
      );
      results.append(row);
    }
    shown += page.items.length;
    next = page.next_offset;
    count.textContent = `已发布来源笔记中匹配 ${page.total} 条，已显示 ${shown} 条；检索结果不是模型回答。`;
    more.hidden = next === null;
  }
  appendPage(d);
  more.addEventListener("click", async () => {
    if (more.disabled || next === null) return;
    more.disabled = true;
    more.textContent = "正在加载…";
    results.setAttribute("aria-busy", "true");
    failure.textContent = "";
    try {
      const page = await api("/library/api/search?q=" + encodeURIComponent(query) + "&offset=" + next);
      if (version !== routeVersion) return;
      appendPage(page);
    } catch (error) {
      if (version === routeVersion)
        failure.textContent = `后续结果加载失败，已显示的结果仍可阅读。${error.message}；请重试。`;
    } finally {
      more.disabled = false;
      more.textContent = "继续加载结果";
      results.setAttribute("aria-busy", "false");
    }
  });
  if (!d.items.length)
    main.append(el("p", "未找到匹配，请尝试更短的主题或术语。", "empty"));
  toc([["检索结果", "content"]]);
}
async function coverage(version) {
  const d = await api("/wiki/api/coverage");
  if (version !== routeVersion) return;
  heading("整理进度", "全部来源 · 覆盖快照");
  const date = new Date(d.database_snapshot_observed * 1000).toLocaleString(
    "zh-CN", { timeZone: "Asia/Shanghai", hour12: false },
  );
  main.append(
    el("p", `数据库快照：${date}（北京时间）。后台仍可能继续处理，本页数字不是实时进度。`, "muted"),
  );
  const refresh = el("button", "重新读取快照");
  refresh.addEventListener("click", route);
  main.append(refresh);
  if (d.stale_inputs.length)
    main.append(notice("来源或文章目录已更新，这份快照已过期；需后台重新生成覆盖清单。重新读取按钮不会触发统计任务。"));
  const summary = el("section");
  summary.id = "coverage-summary";
  summary.append(el("h2", "分别看提取、发布与引用"));
  summary.append(list([
    `课程范围：${d.scope.canonical_audio_courses} 节有声课；另有 ${d.scope.excluded_no_audio_sources} 节无声来源。`,
    `转写：${d.summary.transcribed_courses} 节；已发布来源笔记：${d.summary.source_notes_published} 节。`,
    `主题草稿：${d.summary.draft_articles} 篇（保留 ${d.summary.draft_article_versions ?? d.summary.draft_articles} 个版本）；引用涉及 ${d.summary.courses_with_draft_citations} 节课，还有 ${d.summary.courses_without_draft_citations} 节未被文章引用。`,
    `课件与附件：清单保留 ${d.scope.retained_resource_paths} 个路径，待处理 ${d.scope.pending_resource_paths ?? "未知"} 个；其中 ${d.summary.retained_paths_with_some_indexed_chunks} 个有部分检索片段。`,
    `主题文章已引用 ${d.summary.draft_cited_document_chunks ?? 0} 个课件片段，涉及 ${d.summary.resource_paths_with_draft_citations ?? 0} 个附件路径；这不代表全文已整理。`,
  ]));
  summary.append(notice("提取成功、已被文章引用都不能证明知识完整。主题文章仍待语义复核，课件证据尚未接入文章；原视频继续保留。"));
  if (d.summary.source_identity_mismatches || d.issues.length)
    summary.append(notice(`需核对：${d.summary.source_identity_mismatches} 节来源身份不一致，${d.issues.length} 项引用问题。`));
  main.append(summary);
  const courses = el("section");
  courses.id = "coverage-courses";
  courses.append(el("h2", "逐课查看缺口"));
  const filters = el("form", undefined, "coverage-filters");
  const query = el("input");
  query.type = "search";
  query.placeholder = "筛选课程名称或原目录";
  query.setAttribute("aria-label", "筛选课程名称或原目录");
  const state = el("select");
  state.setAttribute("aria-label", "课程整理状态");
  for (const [value, label] of [
    ["all", "全部课程"], ["unpublished", "还没有来源笔记"],
    ["uncited", "尚未被文章引用"], ["cited", "已有草稿引用"],
  ]) {
    const o = el("option", label);
    o.value = value;
    state.append(o);
  }
  filters.append(query, state);
  filters.addEventListener("submit", (event) => event.preventDefault());
  const results = el("div");
  const count = el("p", "", "muted");
  count.setAttribute("role", "status");
  const more = el("button", "再显示 40 节");
  let limit = 40;
  const states = {
    not_registered: "尚未入库", pending: "待转写", transcribed: "已转写",
    transcribed_with_gaps: "转写有缺口", failed: "处理失败", visual_only: "仅画面",
  };
  function render() {
    const q = query.value.trim().toLocaleLowerCase();
    const rows = d.courses.filter((r) =>
      `${r.relative_path} ${r.title}`.toLocaleLowerCase().includes(q) &&
      (state.value === "all" ||
        (state.value === "unpublished" && !r.source_notes_published) ||
        (state.value === "uncited" && !r.draft_articles.length) ||
        (state.value === "cited" && r.draft_articles.length)),
    );
    results.replaceChildren();
    count.textContent = `匹配 ${rows.length} 节，显示 ${Math.min(limit, rows.length)} 节`;
    for (const r of rows.slice(0, limit)) {
      const row = el("article", undefined, "result");
      row.append(el("h3", r.title), el("p", r.relative_path, "muted"));
      row.append(el("p", `${states[r.database_state] || r.database_state} · ${r.source_notes_published ? "来源笔记可读" : "来源笔记待发布"}`));
      row.append(el("p", `已提取 ${r.extracted_speech_segments} 个语音片段；文章实际引用 ${r.draft_cited_speech_segments} 个片段、${r.draft_cited_frames} 张图。另有 ${r.unreferenced_extracted_speech_segments} 个已提取片段未被引用。`));
      if (r.source_notes_published)
        row.append(link("阅读来源笔记", "#lesson=" + r.id));
      r.draft_articles.forEach((id, i) => {
        row.append(el("span", " · "), link(`主题草稿 ${i + 1}`, "#article=" + id));
      });
      results.append(row);
    }
    if (!rows.length) results.append(el("p", "没有匹配的课程。", "empty"));
    more.hidden = rows.length <= limit;
  }
  [query, state].forEach((n) => n.addEventListener("input", () => { limit = 40; render(); }));
  more.addEventListener("click", () => { limit += 40; render(); });
  courses.append(filters, count, results, more);
  main.append(courses);
  render();
  const gaps = el("section");
  gaps.id = "coverage-gaps";
  gaps.append(el("h2", "尚未完成的验收"), list([
    "完整主题规划、长材料综合和课件图文引用仍需补齐。",
    "引用数量不代表原文支持结论；文章准确性、条件遗漏与图文对应需要独立复核。",
    "独立备份与恢复验收尚未由本清单证明，不能据此删除原视频。",
  ]));
  function detail(title, values) {
    const box = el("details");
    box.append(el("summary", `${title}（${values.length}）`));
    box.append(list(values.map((r) => typeof r === "string" ? r :
      [r.relative_path || r.path || "未命名来源", r.reason || r.error || r.code].filter(Boolean).join(" · "),
    )));
    gaps.append(box);
  }
  detail("待处理课件与附件", d.pending_resources);
  detail("无声来源（仍保留在范围中）", d.no_audio_sources);
  detail("来源读取排除项", d.excluded_source_errors);
  const findings = d.recorded_index_findings;
  gaps.append(el("p", "以下为早期课件提取记录；后续 OCR 可能已补齐，仍需核对当前结果，不能直接当作当前失败。", "muted"));
  detail("历史提取错误", findings.errors);
  detail("历史跳过项", findings.skipped);
  detail("历史 PDF 警告", findings.pdf_warnings);
  main.append(gaps);
  toc([["范围与状态", summary.id], ["逐课缺口", courses.id], ["待验收项", gaps.id]]);
}
async function route() {
  const version = ++routeVersion;
  activeReading = null;
  clearTimeout(saveTimer);
  main.replaceChildren(el("p", "正在读取本地资料…"));
  main.setAttribute("aria-busy", "true");
  outline.replaceChildren();
  window.scrollTo(0, 0);
  try {
    const hash = location.hash.slice(1);
    const pos = hash.indexOf("=");
    const mode = pos < 0 ? hash : hash.slice(0, pos);
    const id = pos < 0 ? "" : decodeURIComponent(hash.slice(pos + 1));
    main.replaceChildren();
    if (mode === "article")
      await article(id.split("/")[0], version, id.split("/")[1]);
    else if (mode === "sources") await sources(version);
    else if (mode === "coverage") await coverage(version);
    else if (mode === "learning") await learningRecords(version);
    else if (mode === "lesson")
      await lesson(id.split("/")[0], version, id.split("/")[1]);
    else if (mode === "terms") await terms(id, version);
    else if (mode === "term") await term(id, version);
    else if (mode === "search") await search(id, version);
    else await home(version);
  } catch (error) {
    if (version === routeVersion) {
      heading("暂时无法读取");
      main.append(
        el("p", error.message, "error"),
        link("返回主题文章", "#home"),
      );
    }
  } finally {
    if (version === routeVersion) main.setAttribute("aria-busy", "false");
  }
}
window.addEventListener(
  "scroll",
  () => {
    if (!activeReading) return;
    const id = activeReading;
    const y = window.scrollY;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      const s = stored(id);
      s.scroll = y;
      save(id, s);
    }, 400);
  },
  { passive: true },
);
window.addEventListener("hashchange", route);
route();

document.querySelector(".skip").addEventListener("click", (event) => {
  event.preventDefault();
  main.focus();
  main.scrollIntoView();
});
