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
      `[${time(s.start)}${s.kind === "frame" ? " 图" : ""}]`,
      "#source-" + ref,
    );
    a.setAttribute("aria-label", `查看出处 ${s.title} ${time(s.start)}`);
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
function claim(value, packet) {
  const p = el("p", value.text);
  refs(p, value.citations, packet);
  return p;
}
function image(ref, caption) {
  const f = el("figure");
  const img = el("img");
  img.src = `/library/frame/${ref.image_sha256}`;
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
      `${caption} · 原始课程画面 ${time(ref.start)}（图文对应待复核）`,
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
      `${row.questions} 道自测 · ${row.images} 张原图 · ${row.gaps} 项资料缺口`,
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
    counts.textContent = `${wiki.articles.length} 篇主题草稿 · ${catalog.published} / ${catalog.requested} 节课程已发布来源笔记`;
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
    };
    const step = wiki.build.stage;
    const labels = {
      outline: "提纲",
      exercises: "自测与易错点",
      complete: "合并校验",
      interrupted: "当前步骤",
    };
    const stageName =
      labels[step] ||
      (step?.startsWith("section-")
        ? `正文第 ${Number(step.slice(8)) + 1} 节`
        : "");
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
    let rows = wiki.articles;
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
  const value = await api("/wiki/api/article/" + encodeURIComponent(id));
  if (version !== routeVersion) return;
  const a = value.article,
    p = value.packet,
    state = stored(id);
  activeReading = id;
  main.append(link("← 返回主题文章", "#home"));
  heading(a.title, "主题文章 / 本地草稿");
  main.append(
    notice(
      "模型草稿 · 来源结构与引用编号已检查 · 语义、图片相关性和主题完整性待复核。自测为自评，不自动认定掌握。",
    ),
  );
  main.append(claim(a.overview, p));
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
      const pre = el("pre");
      pre.append(el("code", s.code));
      section.append(pre);
      refs(section, s.code_citations, p);
    }
    if (s.image) section.append(image(p.sources[s.image], s.image_caption));
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
        `${s.title} · ${time(s.start)}–${time(s.end)} · ${s.kind === "frame" ? "原始画面" : "语音转写"}`,
      ),
      el("p", s.text, "source-text"),
    );
    if (s.kind === "frame") d.append(image(s, s.text));
    d.append(link("查看所属课程笔记", "#lesson=" + s.video));
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
  main.append(
    searchForm("搜索原文笔记", (q) => {
      location.hash = "search=" + encodeURIComponent(q);
    }),
    el("p", `${d.items.length} 条结果；检索结果不是模型回答。`, "meta"),
  );
  for (const r of d.items) {
    const row = el("div", undefined, "row");
    row.append(
      link(
        `${r.title} · ${time(r.start)}`,
        "#lesson=" + r.video + "/" + r.section,
      ),
      el("h3", r.heading),
      el("p", r.summary),
    );
    main.append(row);
  }
  if (!d.items.length)
    main.append(el("p", "未找到匹配，请尝试更短的主题或术语。", "empty"));
  toc([["检索结果", "content"]]);
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
