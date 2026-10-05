"use strict";
const $ = (id) => document.getElementById(id);
const h = (tag, cls, text, kids) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; (kids || []).forEach((k) => e.appendChild(k)); return e; };
const clear = (e) => { while (e.firstChild) e.removeChild(e.firstChild); return e; };
let KEY = sessionStorage.getItem("aegis_key") || "";
async function api(path, opt) {
  const r = await fetch(path, { ...opt, headers: { "Content-Type": "application/json", ...(KEY ? { "X-API-Key": KEY } : {}) } });
  if (r.status === 401) { $("key").hidden = false; throw new Error("API key required: enter it and retry."); }
  if (!r.ok) { let m = r.statusText; try { const j = await r.json(); m = j.detail || j.error || m; if (Array.isArray(m)) m = m.map((x) => x.msg).join("; "); } catch (_) {} throw new Error(r.status + ": " + m); }
  return r.json();
}
function chip(t) { return h("span", null, t); }
async function init() {
  try {
    const m = await api("/api/meta"); const c = clear($("chips"));
    c.append(chip("store " + m.store_hash.slice(0, 8)), chip(m.documents + " docs"), chip(m.claims.ADMITTED + " claims"), chip(m.llm.enabled ? "LLM: " + m.llm.analyzer_mode : "LLM: off (deterministic)"));
    const ex = await api("/api/examples"); ex.questions.forEach((q) => { const o = h("option", null, q.id + " · " + q.text); o.value = q.text; $("ex").appendChild(o); });
  } catch (e) { showErr(e.message); }
}
function showErr(m) { const e = $("err"); e.textContent = m; e.hidden = !m; }
function render(d) {
  $("out").hidden = false;
  const st = clear($("status")); st.append(h("span", "badge " + d.status, d.status), h("span", "badge q-" + d.quality, "evidence: " + d.quality),
    h("span", "badge " + (d.contract_verified ? "ANSWERED" : "UNANSWERABLE"), d.contract_verified ? "contract-verified" : "verifier failed"));
  const a = clear($("answer"));
  d.sentences.forEach((s) => {
    const row = h("div", "s " + s.kind, null, [h("span", "tag", s.kind === "SOURCE" ? "source" : "system note"), document.createTextNode(s.text + " ")]);
    s.claim_ids.forEach((id) => { const b = h("button", "cite", "[" + id + "]"); b.type = "button"; b.addEventListener("click", () => hl(id)); row.appendChild(b); });
    a.appendChild(row);
  });
  const cl = clear($("claims")); d.claims.forEach((c) => cl.appendChild(h("div", "card", null, [h("div", null, c.entity + " · " + c.predicate + " = " + c.value),
    h("div", "muted", c.id + " · scope: " + c.scope + " (" + c.scope_basis + ") · " + c.assertion_kind + " · tier " + c.tier + (c.needs_review ? " · NEEDS REVIEW" : "") + (c.historical_reference ? " · historical" : ""))])));
  const ev = clear($("evidence")); d.evidence.forEach((e) => { const card = h("div", "card", null, [h("div", null, e.role + " · " + e.document + (e.page ? " p" + e.page : "") + " · " + e.method + (e.raster_agreement !== "n/a" ? " · OCR " + e.raster_agreement : "")), h("blockquote", null, "“" + e.quote + "”")]); card.dataset.claim = e.claim_id; ev.appendChild(card); });
  const n = clear($("notes")); const sec = (t, items) => { if (!items.length) return; n.appendChild(h("div", "muted", t)); items.forEach((x) => n.appendChild(h("div", "card", x))); };
  sec("Assumptions", d.assumptions.map((x) => x.type === "current" ? "“Current” = revision " + x.resolved + (x.planned_excluded.length ? " (planned, excluded: " + x.planned_excluded.join(", ") + ")" : "") : x.because + " → " + x.restricted_to));
  sec("Conflicts (none selected)", d.conflicts.map((x) => x.slot + ": " + x.sides.map((s) => JSON.stringify(s.value) + " [" + s.docs.join(", ") + "]").join(" vs ")));
  sec("Gaps", d.gaps.map((x) => x.phrase ? "'" + x.phrase + "' matches nothing in the corpus" : "no " + x.predicate + " for " + x.entity));
  sec("Unverified advisories (not used)", d.advisories.map((x) => x.doc + " reports " + JSON.stringify(x.value) + " — " + x.why));
  sec("Searched", d.searched.map((x) => x.slot + ": " + x.documents_searched + " docs, " + x.n_candidates + " candidates, " + x.n_applicable + " applicable"));
  const w = clear($("why")); w.appendChild(h("p", null, "Analyzer: " + d.why.analyzer + " · intent " + d.why.intent + " · policy " + d.why.policy + " · resolved scope: " + d.why.scope));
  d.why.slots.forEach((s) => {
    w.appendChild(h("div", "muted", s.slot + " — " + s.candidates + " candidates, " + s.selected.length + " selected, " + s.excluded.length + " excluded"));
    if (s.excluded.length) { const t = h("table"); s.excluded.forEach((x) => t.appendChild(h("tr", null, null, [h("td", null, x.id), h("td", null, x.predicate + " = " + x.value), h("td", null, x.scope), h("td", null, x.why)]))); w.appendChild(t); }
  });
  $("meta").textContent = "trace " + d.why.trace_id + " · " + d.why.ms + " ms · store " + d.why.store_hash.slice(0, 8) + " · answer " + d.why.answer_sha;
}
function hl(id) { document.querySelectorAll("#evidence .card").forEach((c) => c.classList.toggle("hl", c.dataset.claim === id)); const t = document.querySelector('#evidence .card[data-claim="' + id + '"]'); if (t) t.scrollIntoView({ block: "nearest" }); }
$("ex").addEventListener("change", (e) => { if (e.target.value) { $("q").value = e.target.value; $("f").requestSubmit(); } });
$("key").addEventListener("change", (e) => { KEY = e.target.value; sessionStorage.setItem("aegis_key", KEY); });
$("f").addEventListener("submit", async (e) => {
  e.preventDefault(); showErr(""); $("go").disabled = true;
  try { render(await api("/api/ask", { method: "POST", body: JSON.stringify({ question: $("q").value }) })); } catch (x) { showErr(x.message); } finally { $("go").disabled = false; }
});
init();
