"use strict";
var CourseWikiLearning = (() => {
  const FORMAT = "course-wiki-learning-v1";
  const PREFIX = "course-wiki:";
  const MAX_BYTES = 16 * 1024 * 1024;
  const fail = (message) => { throw new Error(message); };
  const object = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
  const articleId = (v) => typeof v === "string" && /^[a-f0-9]{32}$/.test(v);
  function learning(value) {
    if (!object(value)) fail("学习记录格式无效。");
    const result = {};
    for (const [key, entry] of Object.entries(value)) {
      if (key === "read" || key === "review") {
        if (typeof entry !== "boolean") fail("已读或复习标记必须为布尔值。");
      } else if (key === "scroll") {
        if (!Number.isFinite(entry) || entry < 0 || entry > 100000000)
          fail("阅读位置无效。");
      } else if (key === "answers" || key === "assessments") {
        if (!object(entry)) fail("作答或自评格式无效。");
        for (const [index, item] of Object.entries(entry)) {
          if (!/^(0|[1-9]\d{0,3})$/.test(index) ||
              typeof item !== (key === "answers" ? "string" : "boolean"))
            fail("题号、作答或自评格式无效。");
        }
      } else fail("记录包含不支持的字段，请保留原备份并核对版本。");
      result[key] = key === "answers" || key === "assessments" ? {...entry} : entry;
    }
    return result;
  }
  function normalize(value) {
    if (!object(value)) fail("请选择课程 Wiki 的学习记录 JSON 备份。");
    let rows;
    if (value.format === FORMAT && Array.isArray(value.records)) rows = value.records;
    else if (value.format === undefined && articleId(value.article) && object(value.learning))
      rows = [value]; // Existing per-article exports remain importable.
    else fail("备份版本或格式不受支持。");
    if (rows.length > 10000) fail("单份备份最多支持 10000 个文章版本。");
    const seen = new Set();
    const records = rows.map(row => {
      if (!object(row) || !articleId(row.article)) fail("文章版本编号无效。");
      if (seen.has(row.article)) fail("备份中有重复文章编号，未导入任何记录。");
      seen.add(row.article);
      return {article: row.article, learning: learning(row.learning)};
    }).sort((a, b) => a.article.localeCompare(b.article));
    return {format: FORMAT, records};
  }
  function parse(text) {
    if (typeof text !== "string" || new TextEncoder().encode(text).byteLength > MAX_BYTES)
      fail("学习记录备份超过 16 MiB 限制。");
    return normalize(JSON.parse(text));
  }
  function collect(storage) {
    const keys = () => Array.from({length: storage.length}, (_, i) => storage.key(i))
      .filter(key => key?.startsWith(PREFIX) && articleId(key.slice(PREFIX.length))).sort();
    const before = keys();
    const raw = before.map(key => storage.getItem(key));
    const result = normalize({format: FORMAT, records: before.map((key, i) => ({
      article: key.slice(PREFIX.length), learning: JSON.parse(raw[i]),
    }))});
    if (JSON.stringify(before) !== JSON.stringify(keys()) ||
        before.some((key, i) => storage.getItem(key) !== raw[i]))
      fail("学习记录正在其他页面更新，请稍后重新导出。");
    return result;
  }
  function serialize(value) {
    const text = JSON.stringify({...normalize(value), exported_at: new Date().toISOString()}, null, 2);
    if (new TextEncoder().encode(text).byteLength > MAX_BYTES) fail("学习记录备份超过 16 MiB 限制。");
    return text;
  }
  function plan(value, storage, mode = "keep") {
    if (!["keep", "replace"].includes(mode)) fail("导入冲突策略无效。");
    const records = normalize(value).records;
    let conflicts = 0, skipped = 0;
    const changes = [];
    for (const row of records) {
      const key = PREFIX + row.article;
      const previous = storage.getItem(key);
      const next = JSON.stringify(row.learning);
      if (previous !== null) conflicts++;
      if ((mode === "keep" && previous !== null) || previous === next) { skipped++; continue; }
      changes.push({key, previous, next});
    }
    return {total: records.length, conflicts, skipped, changes};
  }
  function apply(preview, storage) {
    for (const change of preview.changes) {
      if (storage.getItem(change.key) !== change.previous)
        fail("本机记录已变化，请重新预览后再导入。");
    }
    const written = [];
    try {
      for (const change of preview.changes) {
        storage.setItem(change.key, change.next);
        written.push(change);
      }
    } catch (cause) {
      let incomplete = false;
      for (const change of written.reverse()) {
        try {
          if (storage.getItem(change.key) !== change.next) { incomplete = true; continue; }
          if (change.previous === null) storage.removeItem(change.key);
          else storage.setItem(change.key, change.previous);
        } catch { incomplete = true; }
      }
      const error = new Error(incomplete
        ? "导入中断，部分记录未能恢复；请保留备份文件并核对已有记录。"
        : "导入失败，已恢复导入前记录。浏览器存储可能已满或不可写。");
      error.rollbackIncomplete = incomplete;
      throw error;
    }
    return {imported: written.length, skipped: preview.skipped};
  }
  return {FORMAT, MAX_BYTES, parse, collect, serialize, plan, apply};
})();
if (typeof module !== "undefined" && module.exports) module.exports = CourseWikiLearning;
