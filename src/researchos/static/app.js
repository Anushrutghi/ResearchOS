const el = (selector) => document.querySelector(selector);
const messagesEl = el("#messages");
const inputEl = el("#messageInput");
let activeChatId = null;
let activeChat = null;
let editingMessageId = null;
let queuedFiles = [];
let versions = [];
let buildProgress = null;
let aiProviderConfigured = false;

async function api(url, options = {}) {
  const token = sessionStorage.getItem("researchos_api_token");
  const headers = {"Content-Type": "application/json", ...(options.headers || {})};
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(url, {...options, headers});
  const data = response.headers.get("content-type")?.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}

async function loadState() {
  const data = await api("/api/state");
  versions = data.versions;
  buildProgress = data.build_progress;
  aiProviderConfigured = data.ai_provider_configured === true;
}

async function loadChats() {
  const data = await api("/api/chats");
  const list = el("#chatList");
  list.replaceChildren();
  for (const chat of data.chats) {
    const button = node("button", `chat-row${chat.id === activeChatId ? " active" : ""}`, chat.title);
    button.title = chat.title;
    button.addEventListener("click", () => openChat(chat.id));
    list.append(button);
  }
}

async function newChat() {
  activeChat = await api("/api/chats", {method: "POST", body: "{}"});
  activeChatId = activeChat.id;
  el("#inputStyle").value = "chat"; el("#outputStyle").value = "balanced";
  editingMessageId = null;
  setView("chat");
  renderMessages();
  await loadChats();
  inputEl.focus();
}

async function openChat(id) {
  activeChat = await api(`/api/chats/${id}`);
  activeChatId = id;
  el("#inputStyle").value = activeChat.input_style || "chat";
  el("#outputStyle").value = activeChat.output_style || "balanced";
  editingMessageId = null;
  setView("chat");
  renderMessages();
  await loadChats();
}

function renderMessages() {
  messagesEl.replaceChildren();
  const list = activeChat?.messages || [];
  el("#welcome").hidden = list.length > 0;
  el("#viewTitle").textContent = activeChat?.title || "New chat";
  for (const message of list) {
    const row = node("article", `message ${message.role}`);
    const avatar = node("div", "message-avatar", message.role === "assistant" ? "R" : "Y");
    const inner = node("div", "message-inner");
    const content = node("div", "message-content", message.content);
    inner.append(content);
    if (message.role === "assistant") {
      const meta = node("div", "message-meta", message.source === "ai"
        ? "AI-generated response · Verify research claims against sources"
        : "Local workspace guidance · Not AI-generated research");
      inner.append(meta);
    } else {
      const actions = node("div", "message-actions");
      const edit = node("button", "edit-message", "Edit message");
      edit.addEventListener("click", () => {
        editingMessageId = message.id;
        inputEl.value = message.content;
        resizeInput();
        inputEl.focus();
        el("#sendButton").title = "Save edit";
      });
      actions.append(edit);
      inner.append(actions);
    }
    if (message.role === "user") {
      row.append(inner, avatar);
    } else {
      row.append(avatar, inner);
    }
    messagesEl.append(row);
  }
  messagesEl.scrollTop = messagesEl.scrollHeight;
  el("#sendButton").title = editingMessageId ? "Save edit" : "Send";
}

async function sendMessage(text = inputEl.value.trim()) {
  if (!text || !activeChatId) return;
  const send = el("#sendButton");
  send.disabled = true;
  try {
    if (editingMessageId) {
      const data = await api(`/api/chats/${activeChatId}/messages/${editingMessageId}`, {
        method: "PUT", body: JSON.stringify({message: text, input_style: el("#inputStyle").value, output_style: el("#outputStyle").value})
      });
      activeChat = data.chat;
      editingMessageId = null;
    } else {
      const data = await api("/api/chat", {
        method: "POST", body: JSON.stringify({chat_id: activeChatId, message: text,
          input_style: el("#inputStyle").value, output_style: el("#outputStyle").value})
      });
      activeChat = data.chat;
    }
    inputEl.value = "";
    resizeInput();
    renderMessages();
    await loadChats();
  } catch (error) {
    showError(error.message);
  } finally {
    send.disabled = false;
  }
}

function showError(message) {
  const row = node("div", "error-banner", message);
  (el("#panelView").hidden ? messagesEl : el("#panelView")).prepend(row);
}

function resizeInput() {
  inputEl.style.height = "auto";
  inputEl.style.height = `${Math.min(inputEl.scrollHeight, 180)}px`;
}

function setView(view) {
  document.querySelectorAll(".nav-item").forEach((button) => button.classList.toggle("selected", button.dataset.view === view));
  el("#chatView").hidden = view !== "chat";
  el("#panelView").hidden = view === "chat";
  if (view !== "chat") renderPanel(view);
  else el("#viewTitle").textContent = activeChat?.title || "New chat";
}

function panelHeading(title, subtitle) {
  const box = node("div", "panel-heading");
  box.append(node("p", "eyebrow", "RESEARCH WORKSPACE"), node("h1", "", title), node("p", "", subtitle));
  return box;
}

async function renderPanel(view) {
  const panel = el("#panelView");
  panel.replaceChildren(node("div", "loading", "Loading workspace…"));
  el("#viewTitle").textContent = view === "versions" ? "Versions & progress" : view[0].toUpperCase() + view.slice(1);
  try {
    panel.replaceChildren();
    if (view === "versions") {
      panel.append(panelHeading("Build progress", "Nine tracked releases from the working v0.2 foundation through the v1.0 integrated release. Statuses are updated only when implementation is verified."));
      if (buildProgress) {
        const activity = node("article", "activity-card");
        activity.append(node("div", "eyebrow", `${buildProgress.release} BUILD ACTIVITY · ${buildProgress.status.replaceAll("_", " ")}`),
                        node("h2", "activity-title", buildProgress.current_task),
                        node("p", "activity-updated", `Updated ${new Date(buildProgress.updated_at).toLocaleString()}`));
        const columns = node("div", "activity-columns");
        const completed = node("div", "activity-column"); completed.append(node("strong", "", "Completed"));
        (buildProgress.completed || []).forEach((item) => completed.append(node("p", "", `✓ ${item}`)));
        const next = node("div", "activity-column"); next.append(node("strong", "", "Next"));
        (buildProgress.next || []).forEach((item) => next.append(node("p", "", `○ ${item}`)));
        columns.append(completed, next); activity.append(columns); panel.append(activity);
      }
      const list = node("div", "version-list");
      versions.forEach((version) => {
        const card = node("article", "version-card");
        card.append(node("div", "version-number", version.tracking.toUpperCase()));
        const copy = node("div");
        copy.append(node("strong", "", `${version.release} · ${version.title}`), node("small", "", version.status === "complete" ? "Delivered and verified; see the matching root version note." : version.status === "in_progress" ? "Current active milestone. Remaining acceptance criteria are listed in the version note." : "Planned; not implemented yet."));
        const status = node("span", `status-pill ${version.status}`, version.status.replace("_", " "));
        card.append(copy, status);
        card.tabIndex = 0; card.setAttribute("role", "button"); card.title = `Open ${version.tracking}.md`;
        card.addEventListener("click", async () => {
          const existing = card.nextElementSibling;
          if (existing?.classList.contains("version-report")) { existing.remove(); return; }
          try { const note = await api(`/api/versions/${version.tracking}`); const report = node("pre", "version-report", note.content); card.after(report); }
          catch (error) { showError(error.message); }
        });
        card.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); card.click(); } });
        list.append(card);
      });
      panel.append(list);
    } else if (view === "library") {
      await renderLibrary(panel);
    } else if (view === "evidence") {
      await renderEvidence(panel);
    } else if (view === "methodology") {
      await renderMethodology(panel);
    } else if (view === "experiments") {
      await renderExperiments(panel);
    } else if (view === "agents") {
      await renderAgentWorkflows(panel);
    } else if (view === "writing") {
      await renderWriting(panel);
    } else if (view === "verification") {
      await renderVerification(panel);
    } else if (view === "projects") {
      await renderProjects(panel);
    } else {
      const names = {evidence: "Evidence map", methodology: "Methodology", experiments: "Experiments", verification: "Verification", writing: "Writing"};
      const descriptions = {
        evidence: "The v0.3 evidence foundation is available. Imported passages retain page provenance; claims and relationship suggestions remain reviewable, provisional records.",
        methodology: "",
        experiments: "",
        verification: "Independent evidence, citation, and statistical verification will be developed across v0.3–v0.9. Search results remain discovery leads.",
        writing: "The writing engine builds editable templates from approved records and exports approved drafts to Markdown, LaTeX, DOCX, and PDF."
      };
      panel.append(panelHeading(names[view], descriptions[view]), node("div", "empty-card", "No completed records in this workspace section yet. The roadmap panel shows each engine's current release status."));
    }
  } catch (error) {
    panel.replaceChildren(node("div", "error-banner", error.message));
  }
}

async function renderVerification(panel) {
  panel.append(panelHeading("Verification and quality", "Check local file integrity and review state. These checks do not decide whether scientific claims are true."));
  const [health, report] = await Promise.all([api("/api/health"), api("/api/quality")]);
  const healthCard = node("article", "activity-card"); healthCard.append(node("h2", "activity-title", `Workspace health · ${health.status}`));
  for (const [name, ok] of Object.entries(health.checks)) healthCard.append(node("p", "subtle", `${ok ? "✓" : "!"} ${name.replaceAll("_", " ")}`));
  panel.append(healthCard);
  const quality = node("article", "activity-card"); quality.append(node("h2", "activity-title", `Scientific quality review · ${report.status}`));
  quality.append(node("p", "subtle", "A report of explicit integrity and researcher-review checks, with no combined quality score."));
  for (const check of report.checks) {
    const row = node("section", "source"); row.append(node("strong", "", `${check.status.toUpperCase()} · ${check.check.replaceAll("_", " ")}`), node("p", "subtle", check.detail));
    if (check.records_checked !== undefined) row.append(node("small", "subtle", `${check.records_checked} records checked`));
    quality.append(row);
  }
  panel.append(quality);
  panel.append(node("div", "empty-card", "For a recoverable copy, run `researchos backup --output-dir research_runs --destination ../researchos-backup.tar.gz`. Verify with `researchos verify-backup ../researchos-backup.tar.gz`; restore only into a new directory."));
}

async function renderProjects(panel) {
  panel.append(panelHeading("Research projects", "Organize traceable records into collections and collaborate with named local users."));
  const auth = node("section", "activity-card");
  auth.append(node("h2", "activity-title", "Connect to this workspace"), node("p", "subtle", "Create a token with `researchos auth create-user`. The token is kept in this browser tab only."));
  const token = node("input"); token.type = "password"; token.autocomplete = "off"; token.placeholder = "Bearer token";
  token.value = sessionStorage.getItem("researchos_api_token") || "";
  const saveToken = node("button", "top-button", "Connect");
  auth.append(labeledControl("API token", token), saveToken); panel.append(auth);
  saveToken.addEventListener("click", async () => {
    if (token.value) sessionStorage.setItem("researchos_api_token", token.value);
    else sessionStorage.removeItem("researchos_api_token");
    await renderPanel("projects");
  });
  if (!sessionStorage.getItem("researchos_api_token")) {
    panel.append(node("div", "empty-card", "Connect with a local API token to view or create projects.")); return;
  }
  const form = node("form", "activity-card");
  form.append(node("h2", "activity-title", "Create a project"));
  const title = node("input"); title.maxLength = 200; title.required = true; title.placeholder = "Project title";
  const description = node("textarea"); description.maxLength = 4000; description.placeholder = "Research question or project notes";
  form.append(labeledControl("Title", title), labeledControl("Description", description));
  const create = node("button", "top-button", "Create project"); create.type = "submit"; form.append(create);
  form.addEventListener("submit", async (event) => { event.preventDefault(); try { await api("/api/projects", {method:"POST", body:JSON.stringify({title:title.value,description:description.value})}); await renderPanel("projects"); } catch (error) { showError(error.message); } });
  panel.append(form);
  const page = await api("/api/projects?limit=100");
  if (!page.items.length) panel.append(node("div", "empty-card", "No projects are available to this account yet."));
  for (const project of page.items) {
    const card = node("article", "activity-card"); card.append(node("h2", "activity-title", project.title), node("p", "subtle", project.description || "No description"), node("p", "activity-updated", `${project.members[project.owner] || "owner"} · ${project.revision} revisions · updated ${new Date(project.updated_at).toLocaleString()}`));
    const collectionForm = node("form"); const collectionTitle = node("input"); collectionTitle.placeholder = "New collection title"; collectionTitle.required = true; collectionTitle.maxLength=200;
    const collectionRefs = node("input"); collectionRefs.placeholder = 'Optional JSON records, e.g. [{"record_type":"paper","record_id":"…"}]'; collectionRefs.setAttribute("aria-label", "Collection record references as JSON");
    const addCollection = node("button", "top-button", "Add collection"); addCollection.type="submit"; collectionForm.append(collectionTitle, addCollection);
    collectionForm.insertBefore(collectionRefs, addCollection);
    collectionForm.addEventListener("submit", async (event) => { event.preventDefault(); try { const records=collectionRefs.value.trim()?JSON.parse(collectionRefs.value):[]; await api(`/api/projects/${project.project_id}/collections`, {method:"POST",body:JSON.stringify({title:collectionTitle.value,records})}); await renderPanel("projects"); } catch (error) { showError(error instanceof SyntaxError ? "Collection records must be a JSON array." : error.message); } });
    card.append(collectionForm);
    for (const collection of project.collections) card.append(node("p", "subtle", `▣ ${collection.title} · ${collection.records.length} records`));
    const lifecycle = node("details"); const lifecycleSummary=node("summary", "subtle", "Research lifecycle status"); lifecycle.append(lifecycleSummary);
    lifecycleSummary.addEventListener("click", async () => { if (lifecycle.dataset.loaded) return; try { const result=(await api(`/api/projects/${project.project_id}/lifecycle`)).lifecycle; for (const stage of result.stages) lifecycle.append(node("p", "subtle", `${stage.status.replaceAll("_", " ")} · ${stage.title} — ${stage.note}`)); if (result.stale_references.length) lifecycle.append(node("p", "error-banner", `${result.stale_references.length} collection references are unavailable.`)); lifecycle.dataset.loaded="true"; } catch(error) { showError(error.message); } });
    card.append(lifecycle);
    const revisions = node("details"); const summary = node("summary", "subtle", "Revision history"); revisions.append(summary);
    summary.addEventListener("click", async () => { if (revisions.dataset.loaded) return; try { const data=await api(`/api/projects/${project.project_id}/revisions?limit=100`); for (const revision of data.items) revisions.append(node("p", "subtle", `r${revision.revision} · ${revision.action} · ${revision.actor} · ${new Date(revision.at).toLocaleString()}`)); revisions.dataset.loaded="true"; } catch(error) { showError(error.message); } });
    card.append(revisions); panel.append(card);
  }
}

function labeledControl(title, control) {
  const label = node("label", "form-label", title);
  label.append(control);
  return label;
}

function buildKnowledgeGraph(graph) {
  const box = node("div", "graph-scroll");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const nodes = graph.nodes || []; const edges = graph.edges || [];
  const positions = new Map();
  let docRow = 0, claimRow = 0, otherRow = 0;
  nodes.forEach((item) => {
    let x, y;
    if (["document", "paper"].includes(item.node_type)) { x = 30; y = 30 + docRow++ * 76; }
    else if (item.node_type === "claim") { x = 380; y = 30 + claimRow++ * 76; }
    else { x = 205; y = 30 + otherRow++ * 76; }
    positions.set(item.node_id, {x, y, item});
  });
  const height = Math.max(170, Math.max(docRow, claimRow, otherRow) * 76 + 40);
  svg.setAttribute("viewBox", `0 0 700 ${height}`); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Research evidence graph");
  edges.forEach((edge) => {
    const a = positions.get(edge.source_id), b = positions.get(edge.target_id); if (!a || !b) return;
    const line = document.createElementNS(svg.namespaceURI, "line");
    line.setAttribute("x1", String(a.x + 132)); line.setAttribute("y1", String(a.y + 25)); line.setAttribute("x2", String(b.x)); line.setAttribute("y2", String(b.y + 25));
    line.setAttribute("class", `graph-edge ${edge.relationship === "contradicts" ? "possible-conflict" : ""}`); svg.append(line);
    const label = document.createElementNS(svg.namespaceURI, "text"); label.setAttribute("x", String((a.x + b.x + 132) / 2)); label.setAttribute("y", String((a.y + b.y + 50) / 2 - 4)); label.setAttribute("class", "graph-edge-label"); label.textContent = edge.relationship.replaceAll("_", " "); svg.append(label);
  });
  nodes.forEach((item) => {
    const point = positions.get(item.node_id); const group = document.createElementNS(svg.namespaceURI, "g"); group.setAttribute("class", `graph-node ${item.node_type}`);
    const rect = document.createElementNS(svg.namespaceURI, "rect"); rect.setAttribute("x", String(point.x)); rect.setAttribute("y", String(point.y)); rect.setAttribute("width", "132"); rect.setAttribute("height", "50"); rect.setAttribute("rx", "9"); group.append(rect);
    const type = document.createElementNS(svg.namespaceURI, "text"); type.setAttribute("x", String(point.x + 9)); type.setAttribute("y", String(point.y + 15)); type.setAttribute("class", "graph-node-type"); type.textContent = item.node_type; group.append(type);
    const text = document.createElementNS(svg.namespaceURI, "text"); text.setAttribute("x", String(point.x + 9)); text.setAttribute("y", String(point.y + 33)); text.setAttribute("class", "graph-node-label"); text.textContent = item.label.length > 18 ? `${item.label.slice(0, 18)}…` : item.label; group.append(text);
    const title = document.createElementNS(svg.namespaceURI, "title"); title.textContent = item.label; group.append(title); svg.append(group);
  });
  box.append(svg); return box;
}

async function renderLibrary(panel) {
  const data = await api("/api/documents");
  panel.append(panelHeading("Research library", "Researcher-provided PDFs are stored locally with a SHA-256 hash. Extracted page text is searchable and retains its page location."));
  const search = node("input", "library-search");
  search.type = "search"; search.placeholder = "Search extracted pages and saved claims…"; search.setAttribute("aria-label", "Search the local research library");
  panel.append(search);
  const results = node("div", "library-results");
  const viewer = node("section", "document-viewer");
  panel.append(results, viewer);
  const docs = data.documents;
  if (!docs.length) results.append(node("div", "empty-card", "No documents have been imported yet. Use Attach in a chat to add a PDF."));
  for (const doc of docs) {
    const card = node("article", "version-card doc-card");
    const copy = node("div"); copy.append(node("strong", "", doc.filename), node("small", "", `${doc.page_count || 0} extracted pages · SHA-256 ${doc.sha256.slice(0, 16)}…`));
    const open = node("button", "doc-open", doc.extraction_status.replaceAll("_", " "));
    open.addEventListener("click", () => {
      viewer.replaceChildren(node("h2", "viewer-title", doc.title || doc.filename));
      viewer.append(node("p", "viewer-meta", `${doc.filename} · ${doc.page_count} pages · ${doc.extraction_method || "No extractor"} · OCR ${doc.ocr_status || "not run"}`));
      const metadata = [doc.authors?.length ? `Authors: ${doc.authors.join(", ")}` : "",
                        doc.doi ? `DOI: ${doc.doi}` : "",
                        `${doc.references?.length || 0} reference lines · ${doc.table_candidates?.length || 0} possible table rows`].filter(Boolean).join(" · ");
      if (metadata) viewer.append(node("p", "viewer-meta", metadata));
      if (!doc.pages?.length) viewer.append(node("div", "empty-card", `Page text unavailable: ${doc.extraction_error || doc.extraction_status}.`));
      for (const page of doc.pages || []) {
        const pageBlock = node("article", "page-block");
        pageBlock.append(node("div", "page-label", `Page ${page.page}${page.section_name ? ` · ${page.section_name}` : ""}`), node("pre", "page-text", page.text || "No selectable text on this page."));
        viewer.append(pageBlock);
      }
      if (doc.references?.length) {
        viewer.append(node("h3", "subheading", "Reference candidates · heuristic"));
        doc.references.forEach((reference) => {
          const detail = [reference.doi_candidate ? `DOI candidate ${reference.doi_candidate}` : "",
            reference.year_candidate ? `year candidate ${reference.year_candidate}` : ""].filter(Boolean).join(" · ");
          viewer.append(node("p", "viewer-meta", `Page ${reference.page} · ${reference.raw_text || reference.text}${detail ? ` · ${detail}` : ""}`));
        });
      }
      if (doc.tables?.length) {
        viewer.append(node("h3", "subheading", "Possible table structure · heuristic"));
        doc.tables.forEach((table) => {
          const block = node("div", "table-candidate");
          block.append(node("p", "viewer-meta", `Page ${table.page} · candidate rows; original layout may differ`));
          table.rows.forEach((cells) => block.append(node("p", "table-candidate-row", cells.join(" · "))));
          viewer.append(block);
        });
      } else if (doc.table_candidates?.length) {
        viewer.append(node("h3", "subheading", "Possible table rows · heuristic"));
        doc.table_candidates.forEach((candidate) => viewer.append(node("p", "viewer-meta", `Page ${candidate.page} · ${candidate.text}`)));
      }
      viewer.scrollIntoView({behavior: "smooth", block: "start"});
    });
    const original = node("a", "doc-open", "Open PDF"); original.href = `/api/documents/${doc.document_id}/file`; original.target = "_blank"; original.rel = "noopener";
    const actions = node("div", "doc-actions"); actions.append(open, original);
    card.append(copy, actions); results.append(card);
  }
  let timer;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const query = search.value.trim();
      if (query.length < 2) { results.hidden = false; panel.querySelectorAll(".search-result").forEach((item) => item.remove()); return; }
      try {
        const found = await api(`/api/search?q=${encodeURIComponent(query)}`);
        results.hidden = true;
        panel.querySelectorAll(".search-result").forEach((item) => item.remove());
        const searchResults = node("div", "search-result");
        searchResults.append(node("h2", "viewer-title", `${found.matches.length} local match${found.matches.length === 1 ? "" : "es"}`));
        found.matches.forEach((match) => {
          const row = node("article", "search-match");
          row.append(node("strong", "", `${match.filename || "Research claim"} · page ${match.page}`), node("p", "", match.snippet));
          searchResults.append(row);
        });
        panel.insertBefore(searchResults, viewer);
      } catch (error) { showError(error.message); }
    }, 180);
  });
}

async function renderEvidence(panel) {
  const [graph, docs] = await Promise.all([api("/api/claims"), api("/api/documents")]);
  panel.append(panelHeading("Evidence map", "Claims remain provisional until reviewed. Each saved claim links to a selected source passage and page; the local graph records provenance."));
  if (graph.nodes.length) {
    panel.append(node("h2", "subheading", "Knowledge graph"), buildKnowledgeGraph(graph));
  }
  if (!docs.documents.length) {
    panel.append(node("div", "empty-card", "Import a researcher-provided PDF from a chat before adding a page-linked claim."));
    return;
  }
  const form = node("form", "claim-form");
  const documentSelect = node("select", "form-control");
  docs.documents.forEach((doc) => { const option = node("option", "", doc.filename); option.value = doc.document_id; documentSelect.append(option); });
  const pageNumber = node("input", "form-control"); pageNumber.type = "number"; pageNumber.min = "1"; pageNumber.value = "1";
  const sectionName = node("input", "form-control"); sectionName.placeholder = "e.g. Results (optional)";
  const category = node("select", "form-control");
  [["unverified_claim","Unverified claim"],["direct_experimental_evidence","Direct experimental evidence"],["observational_evidence","Observational evidence"],["theoretical_evidence","Theoretical evidence"],["statistical_evidence","Statistical evidence"],["literature_supported_finding","Literature-supported finding"],["researcher_interpretation","Researcher interpretation"],["hypothesis","Hypothesis"],["proposed_experiment","Proposed experiment"]].forEach(([value,label]) => { const option = node("option", "", label); option.value = value; category.append(option); });
  const claimText = node("textarea", "form-control"); claimText.required = true; claimText.maxLength = 4000; claimText.placeholder = "Write the claim exactly as you want it recorded";
  const passageText = node("textarea", "form-control"); passageText.required = true; passageText.maxLength = 20000; passageText.placeholder = "Paste the supporting or relevant passage from the selected page";
  form.append(labeledControl("Source document", documentSelect), labeledControl("Page number", pageNumber), labeledControl("Section", sectionName),
             labeledControl("Evidence classification", category), labeledControl("Claim", claimText), labeledControl("Source passage", passageText));
  const suggestionButton = node("button", "review-button", aiProviderConfigured ? "Suggest claims from selected page (AI)" : "AI claim suggestions not configured");
  suggestionButton.type = "button"; suggestionButton.disabled = !aiProviderConfigured;
  const suggestions = node("div", "claim-suggestions");
  if (aiProviderConfigured) suggestions.append(node("p", "hint", "When requested, this sends the selected page text to your configured AI provider. Suggestions are unverified."));
  suggestionButton.addEventListener("click", async () => {
    suggestionButton.disabled = true; suggestions.replaceChildren(node("p", "subtle", "Requesting provisional suggestions for this page…"));
    try {
      const result = await api("/api/claims/extract", {method: "POST", body: JSON.stringify({document_id: documentSelect.value, page_number: Number(pageNumber.value)})});
      suggestions.replaceChildren(node("p", "hint", result.notice));
      if (!result.candidates.length) suggestions.append(node("p", "empty-card", "No claim candidates were returned for this page."));
      result.candidates.forEach((candidate) => {
        const card = node("article", "claim-card");
        card.append(node("div", "claim-topline", `AI suggestion · provisional · ${candidate.evidence_category.replaceAll("_", " ")} · page ${candidate.page_number}`),
          node("p", "claim-text", candidate.claim_text), node("blockquote", "claim-passage", candidate.passage_text));
        const save = node("button", "review-button", "Save provisional claim"); save.type = "button";
        save.addEventListener("click", async () => {
          save.disabled = true;
          try {
            await api("/api/claims", {method: "POST", body: JSON.stringify(candidate)});
            card.append(node("span", "status-pill provisional", "Saved · provisional"));
            save.remove();
            await renderPanel("evidence");
          } catch (error) { showError(error.message); save.disabled = false; }
        });
        card.append(save); suggestions.append(card);
      });
    } catch (error) { suggestions.replaceChildren(node("div", "error-banner", error.message)); }
    finally { suggestionButton.disabled = false; }
  });
  form.append(suggestionButton, suggestions);
  const add = node("button", "dialog-submit", "Save provisional claim"); add.type = "submit"; form.append(add);
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); add.disabled = true;
    try {
      await api("/api/claims", {method: "POST", body: JSON.stringify({document_id: documentSelect.value, page_number: Number(pageNumber.value), section_name: sectionName.value,
        evidence_category: category.value, claim_text: claimText.value, passage_text: passageText.value})});
      await renderPanel("evidence");
    } catch (error) { showError(error.message); } finally { add.disabled = false; }
  });
  panel.append(form);
  const heading = node("h2", "subheading", `Saved claims (${graph.claims.length})`); panel.append(heading);
  if (graph.claims.length > 1) {
    const linkForm = node("form", "relationship-form");
    const source = node("select", "form-control"); const target = node("select", "form-control");
    graph.claims.forEach((claim) => {
      const label = claim.original_text.slice(0, 80);
      const a = node("option", "", label); a.value = claim.claim_id; source.append(a);
      const b = node("option", "", label); b.value = claim.claim_id; target.append(b);
    });
    const relation = node("select", "form-control");
    [["supports","Supports"],["contradicts","Possible contradiction"],["challenges","Challenges"],["extends","Extends"],["reproduces","Reproduces"]].forEach(([value,label]) => { const option=node("option","",label);option.value=value;relation.append(option); });
    const notes = node("textarea", "form-control"); notes.required = true; notes.placeholder = "Describe the methodological or contextual basis; links remain proposed for review";
    linkForm.append(labeledControl("Claim A", source), labeledControl("Relationship to Claim B", relation), labeledControl("Claim B", target), labeledControl("Review context", notes));
    const propose = node("button", "review-button", "Propose relationship"); propose.type="submit"; linkForm.append(propose);
    linkForm.addEventListener("submit", async (event) => {
      event.preventDefault(); propose.disabled=true;
      try { await api("/api/relationships", {method:"POST",body:JSON.stringify({source_id:source.value,target_id:target.value,relationship:relation.value,notes:notes.value})}); await renderPanel("evidence"); }
      catch(error){showError(error.message);} finally{propose.disabled=false;}
    });
    panel.append(node("h2", "subheading", "Review relationships"), linkForm);
    const analyze = node("button", "review-button", aiProviderConfigured ? "Compare selected claims (AI suggestion)" : "AI claim comparison not configured");
    analyze.type = "button"; analyze.disabled = !aiProviderConfigured;
    const analysisPanel = node("div", "relationship-analysis");
    if (aiProviderConfigured) analysisPanel.append(node("p", "hint", "When requested, the selected claims and their source passages are sent to your configured AI provider. Its suggestion is provisional."));
    analyze.addEventListener("click", async () => {
      analyze.disabled = true; analysisPanel.replaceChildren(node("p", "viewer-meta", "Comparing the two selected claims and source passages…"));
      try {
        const result = await api("/api/relationships/analyze", {method: "POST", body: JSON.stringify({left_claim_id: source.value, right_claim_id: target.value})});
        analysisPanel.replaceChildren(node("strong", "", `AI suggestion · ${result.candidate_relation.replaceAll("_", " ")} · ${result.confidence} confidence · requires researcher review`),
          node("p", "", result.rationale));
        if (result.contextual_differences.length) {
          const list = node("ul", "context-differences");
          result.contextual_differences.forEach((item) => list.append(node("li", "", item)));
          analysisPanel.append(node("p", "viewer-meta", "Possible context differences to check"), list);
        }
        const saveSuggestion = node("button", "review-button", "Save as proposed relationship"); saveSuggestion.type = "button";
        saveSuggestion.addEventListener("click", async () => {
          saveSuggestion.disabled = true;
          const details = [`AI suggestion (${result.confidence} confidence; not verified): ${result.rationale}`,
            ...result.contextual_differences.map((item) => `Context to check: ${item}`)].join("\n");
          try {
            await api("/api/relationships", {method: "POST", body: JSON.stringify({source_id: source.value, target_id: target.value,
              relationship: result.candidate_relation, notes: details})});
            await renderPanel("evidence");
          } catch (error) { showError(error.message); saveSuggestion.disabled = false; }
        });
        analysisPanel.append(saveSuggestion);
      } catch (error) { analysisPanel.replaceChildren(node("div", "error-banner", error.message)); }
      finally { analyze.disabled = false; }
    });
    panel.append(analyze, analysisPanel);
    const relations = graph.edges.filter((edge) => edge.relationship !== "derived_from");
    relations.forEach((edge) => {
      const row = node("div", "relationship-row");
      const left = graph.claims.find((claim) => claim.claim_id === edge.source_id);
      const right = graph.claims.find((claim) => claim.claim_id === edge.target_id);
      row.append(node("strong", "", `${edge.relationship.replaceAll("_", " ")} · ${edge.status}`));
      for (const claim of [left, right]) {
        if (!claim) continue;
        const doc = docs.documents.find((item) => item.document_id === claim.source.document_id);
        const evidence = node("div", "relationship-evidence");
        evidence.append(node("p", "claim-text", claim.original_text),
          node("p", "viewer-meta", `${doc?.filename || "Source document"} · page ${claim.source.page_number}${claim.source.section_name ? ` · ${claim.source.section_name}` : ""}`),
          node("blockquote", "claim-passage", claim.source.text));
        const source = node("a", "source-page-link", `Open source · page ${claim.source.page_number}`);
        source.href = `/api/documents/${claim.source.document_id}/file#page=${claim.source.page_number}`;
        source.target = "_blank"; source.rel = "noopener"; evidence.append(source); row.append(evidence);
      }
      row.append(node("small", "", `Researcher context: ${edge.notes || "Not supplied"}`));
      if (edge.reviewer_note) row.append(node("small", "", `Review note: ${edge.reviewer_note}`));
      if (edge.status === "proposed") {
        for (const [status, label] of [["reviewed", "Confirm relationship after review"], ["rejected", "Reject relationship"]]) {
          const decision = node("button", "review-button", label); decision.type = "button";
          decision.addEventListener("click", async () => {
            const reviewerNote = window.prompt("Record the review context (compare methods, populations, conditions, or definitions):");
            if (reviewerNote === null) return;
            if (!reviewerNote.trim()) { showError("Add review context before deciding this relationship."); return; }
            decision.disabled = true;
            try { await api(`/api/relationships/${edge.edge_id}/review`, {method: "PUT", body: JSON.stringify({status, reviewer_note: reviewerNote})}); await renderPanel("evidence"); }
            catch (error) { showError(error.message); decision.disabled = false; }
          });
          row.append(decision);
        }
      }
      panel.append(row);
    });
  }
  if (!graph.claims.length) panel.append(node("div", "empty-card", "No claims have been recorded. Add one above; it will be marked provisional until reviewed."));
  graph.claims.slice().reverse().forEach((claim) => {
    const card = node("article", "claim-card");
    const doc = docs.documents.find((item) => item.document_id === claim.source.document_id);
    card.append(node("div", "claim-topline", `${doc?.filename || "Source document"} · page ${claim.source.page_number} · ${claim.evidence_category.replaceAll("_", " ")}`));
    card.append(node("p", "claim-text", claim.original_text), node("blockquote", "claim-passage", claim.source.text));
    const footer = node("div", "claim-footer"); footer.append(node("span", `status-pill ${claim.verification_status}`, claim.verification_status));
    const sourceLink = node("a", "source-page-link", `Open source · page ${claim.source.page_number}`);
    sourceLink.href = `/api/documents/${claim.source.document_id}/file#page=${claim.source.page_number}`; sourceLink.target = "_blank"; sourceLink.rel = "noopener"; footer.append(sourceLink);
    if (claim.verification_status === "provisional") {
      const review = node("button", "review-button", "Mark reviewed");
      review.addEventListener("click", async () => {
        try { await api(`/api/claims/${claim.claim_id}/review`, {method: "PUT", body: JSON.stringify({status: "reviewed", reviewer_note: "Reviewed by researcher in local workspace"})}); await renderPanel("evidence"); }
        catch (error) { showError(error.message); }
      }); footer.append(review);
    }
    card.append(footer); panel.append(card);
  });
}

function renderEvaluationChart(visualization) {
  if (!visualization) return null;
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  const add = (name, attrs = {}, text = "") => {
    const element = document.createElementNS(ns, name);
    Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, String(value)));
    if (text) element.textContent = text;
    svg.append(element); return element;
  };
  if (visualization.type === "confusion_matrix") {
    const labels = visualization.labels; const matrix = visualization.matrix; const n = labels.length;
    const cell = n > 18 ? 22 : 34; const left = 150; const top = 30; const width = left + n * cell + 20; const height = top + n * cell + 70;
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`); svg.setAttribute("width", width); svg.setAttribute("height", height); svg.setAttribute("role", "img");
    add("title", {}, "Confusion matrix: actual labels by row, predicted labels by column");
    add("text", {x: left + n * cell / 2, y: height - 8, "text-anchor": "middle", class: "chart-label"}, "Predicted class");
    add("text", {x: 15, y: top + n * cell / 2, transform: `rotate(-90 15 ${top + n * cell / 2})`, "text-anchor": "middle", class: "chart-label"}, "Actual class");
    const maximum = Math.max(1, ...matrix.flat());
    labels.forEach((label, row) => {
      add("text", {x: left - 8, y: top + row * cell + cell * .67, "text-anchor": "end", class: "chart-label"}, label.length > 18 ? `${label.slice(0, 17)}…` : label);
      add("text", {x: left + row * cell + cell / 2, y: top - 7, "text-anchor": "middle", class: "chart-label"}, label.length > 9 ? `${label.slice(0, 8)}…` : label);
      labels.forEach((_, column) => {
        const value = matrix[row][column]; const opacity = value ? .18 + .82 * value / maximum : .06;
        const rect = document.createElementNS(ns, "rect"); rect.setAttribute("x", String(left + column * cell)); rect.setAttribute("y", String(top + row * cell));
        rect.setAttribute("width", String(cell - 2)); rect.setAttribute("height", String(cell - 2)); rect.setAttribute("rx", "3"); rect.setAttribute("fill", `rgba(11,112,80,${opacity})`); rect.setAttribute("aria-label", `${label} predicted as ${labels[column]}: ${value}`); svg.append(rect);
        add("text", {x: left + column * cell + cell / 2, y: top + row * cell + cell * .66, "text-anchor": "middle", class: "chart-count"}, String(value));
      });
    });
  } else if (visualization.type === "actual_predicted_scatter" && visualization.points?.length) {
    const points = visualization.points; const values = points.flat(); let min = Math.min(...values); let max = Math.max(...values);
    if (min === max) { min -= 1; max += 1; }
    const width = 500, height = 340, left = 58, right = 20, top = 24, bottom = 48, plot = Math.min(width - left - right, height - top - bottom);
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`); svg.setAttribute("width", width); svg.setAttribute("height", height); svg.setAttribute("role", "img");
    add("title", {}, `Actual versus predicted values${visualization.sampled ? " (deterministic sample)" : ""}`);
    add("line", {x1: left, y1: top + plot, x2: left + plot, y2: top + plot, stroke: "#697586"});
    add("line", {x1: left, y1: top, x2: left, y2: top + plot, stroke: "#697586"});
    const scale = (value) => left + (value - min) / (max - min) * plot;
    const yScale = (value) => top + plot - (value - min) / (max - min) * plot;
    add("line", {x1: scale(min), y1: yScale(min), x2: scale(max), y2: yScale(max), stroke: "#9aa5b1", "stroke-dasharray": "5 4"});
    points.forEach(([actual, predicted]) => { const dot = document.createElementNS(ns, "circle"); dot.setAttribute("cx", String(scale(actual))); dot.setAttribute("cy", String(yScale(predicted))); dot.setAttribute("r", "3"); dot.setAttribute("fill", "#167a5a"); dot.setAttribute("fill-opacity", ".65"); svg.append(dot); });
    add("text", {x: left + plot / 2, y: height - 8, "text-anchor": "middle", class: "chart-label"}, "Actual");
    add("text", {x: 16, y: top + plot / 2, transform: `rotate(-90 16 ${top + plot / 2})`, "text-anchor": "middle", class: "chart-label"}, "Predicted");
    if (visualization.sampled) add("text", {x: width - 20, y: 18, "text-anchor": "end", class: "chart-label"}, `Showing ${points.length} deterministic sample points`);
  } else return null;
  const wrapper = node("div", "evaluation-chart-scroll"); wrapper.append(svg); return wrapper;
}

async function renderAgentWorkflows(panel) {
  panel.replaceChildren();
  const [result, state] = await Promise.all([api("/api/agent-workflows"), api("/api/state")]);
  panel.append(panelHeading("Agents and workflows", "Typed tasks run with declared permissions, retain provenance and usage, and pause for researcher review before an approval-gated result can advance. The built-in planner works without an AI key."));
  const form = node("form", "methodology-form");
  const title = node("input", "form-control"); title.required = true; title.maxLength = 200; title.value = "Research plan";
  const question = node("textarea", "form-control"); question.required = true; question.maxLength = 1000; question.placeholder = "Describe the research question";
  const agent = node("select", "form-control");
  [["research_plan", "Local structured planner · no provider"], ["study_design_guidance", "Study design and feasibility reviewer · local rules"], ...(state.ai_provider_configured ? [["research_plan_ai", "Optional AI research planner"]] : [])].forEach(([value, label]) => { const option = node("option", "", label); option.value = value; agent.append(option); });
  const planInputs = node("div", "");
  const questionLabel = labeledControl("Research question", question); planInputs.append(questionLabel);
  const studyInputs = node("div", ""); studyInputs.hidden = true;
  const topic = node("input", "form-control"); topic.maxLength = 1000;
  const researchField = node("input", "form-control"); researchField.maxLength = 200;
  const researchType = node("select", "form-control");
  [["quantitative", "Quantitative"], ["qualitative", "Qualitative"], ["mixed_methods", "Mixed methods"], ["computational", "Computational"], ["theoretical", "Theoretical"], ["systematic_review", "Systematic review"]].forEach(([value, label]) => { const option = node("option", "", label); option.value = value; researchType.append(option); });
  const problem = node("textarea", "form-control"); problem.maxLength = 4000;
  const objectives = node("textarea", "form-control"); objectives.placeholder = "One objective per line";
  const researchQuestions = node("textarea", "form-control"); researchQuestions.placeholder = "One research question per line";
  studyInputs.append(labeledControl("Topic", topic), labeledControl("Research field", researchField),
    labeledControl("Research type", researchType), labeledControl("Specific problem statement", problem),
    labeledControl("Objectives", objectives), labeledControl("Research questions", researchQuestions));
  agent.addEventListener("change", () => { const isStudy = agent.value === "study_design_guidance"; planInputs.hidden = isStudy; studyInputs.hidden = !isStudy; });
  const submit = node("button", "review-button", "Create reviewable plan"); submit.type = "submit";
  form.append(labeledControl("Workflow title", title), labeledControl("Agent provider", agent), planInputs, studyInputs, submit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); submit.disabled = true;
    try {
      if (agent.value === "research_plan_ai" && !window.confirm("Send this research question to the configured AI provider? The result will remain proposed until you review it.")) { submit.disabled = false; return; }
      const agentInput = agent.value === "study_design_guidance" ? {topic: topic.value, research_field: researchField.value,
        research_type: researchType.value, problem_statement: problem.value,
        objectives: objectives.value.split("\n").map((line) => line.trim()).filter(Boolean),
        research_questions: researchQuestions.value.split("\n").map((line) => line.trim()).filter(Boolean)} : {question: question.value};
      await api("/api/agent-workflows", {method: "POST", body: JSON.stringify({title: title.value,
        provenance: {origin: "researcher_input"}, tasks: [{task_id: "plan", agent: agent.value,
          input: agentInput, approval_required: true}]})});
      await renderAgentWorkflows(panel);
    } catch (error) { showError(error.message); submit.disabled = false; }
  });
  panel.append(form);
  panel.append(node("h2", "subheading", `Saved workflows (${result.workflows.length})`));
  result.workflows.forEach((workflow) => {
    const card = node("article", "method-card");
    card.append(node("strong", "", `${workflow.title} · ${workflow.status}`),
      node("p", "viewer-meta", `Created ${workflow.created_at} · ${workflow.tasks.length} task(s)`));
    workflow.tasks.forEach((task) => {
      card.append(node("h3", "", `${task.task_id} · ${task.agent} · ${task.status}`));
      if (task.output) card.append(node("pre", "version-report", JSON.stringify(task.output, null, 2)));
      if (task.failure) card.append(node("p", "error-banner", task.failure));
      card.append(node("small", "viewer-meta", `Provider ${task.provider}/${task.model} · attempts ${task.attempts} · usage ${JSON.stringify(task.usage)} · provenance ${JSON.stringify(task.provenance)}`));
      if (task.status === "awaiting_approval") {
        const edit = node("textarea", "form-control"); edit.value = JSON.stringify(task.output, null, 2); edit.maxLength = 100000;
        const revisionInput = node("textarea", "form-control"); revisionInput.value = JSON.stringify(task.input, null, 2); revisionInput.maxLength = 100000;
        card.append(labeledControl("Edit proposed JSON output", edit));
        card.append(labeledControl("Revised task input · optional", revisionInput));
        for (const [action, label] of [["approve", "Approve"], ["edit", "Save edits and approve"], ["reject", "Reject"], ["revise", "Request revision"]]) {
          const button = node("button", "review-button", label); button.type = "button";
          button.addEventListener("click", async () => {
            button.disabled = true;
            try {
              const body = {action};
              if (action === "edit") body.output = JSON.parse(edit.value);
              if (action === "revise" && revisionInput.value.trim()) body.output = JSON.parse(revisionInput.value);
              await api(`/api/agent-workflows/${encodeURIComponent(workflow.workflow_id)}/tasks/${encodeURIComponent(task.task_id)}/review`, {method: "POST", body: JSON.stringify(body)});
              await renderAgentWorkflows(panel);
            } catch (error) { showError(error.message); button.disabled = false; }
          });
          card.append(button);
        }
      }
    });
    if (workflow.status === "failed" || workflow.status === "pending") {
      const resume = node("button", "review-button", "Resume workflow"); resume.type = "button";
      resume.addEventListener("click", async () => { resume.disabled = true; try { await api(`/api/agent-workflows/${encodeURIComponent(workflow.workflow_id)}/resume`, {method: "POST", body: "{}"}); await renderAgentWorkflows(panel); } catch (error) { showError(error.message); resume.disabled = false; } });
      card.append(resume);
    }
    panel.append(card);
  });
}

async function renderWriting(panel) {
  panel.replaceChildren();
  const [catalog, saved] = await Promise.all([api("/api/writing/catalog"), api("/api/writing/drafts")]);
  panel.append(panelHeading("Scientific writing", "Create an editable document from selected local records. Unreviewed citations are flagged, missing sections remain empty, and exports require approval of the current draft."));
  const form = node("form", "methodology-form");
  const title = node("input", "form-control"); title.required = true; title.maxLength = 300; title.placeholder = "Document title";
  const type = node("select", "form-control");
  [["article", "Research article"], ["review", "Literature review"], ["systematic_review", "Systematic review"],
    ["proposal", "Research proposal"], ["report", "Research report"], ["conference_paper", "Conference paper"],
    ["theoretical", "Theoretical paper"]].forEach(([value, label]) => { const option = node("option", "", label); option.value = value; type.append(option); });
  const question = node("textarea", "form-control"); question.maxLength = 4000; question.placeholder = "Research question supplied by you (optional)";
  const claims = node("select", "form-control"); claims.multiple = true; claims.size = Math.min(6, Math.max(2, catalog.claims.length));
  catalog.claims.forEach((item) => { const option = node("option", "", `${item.verification_status} · ${item.filename}, p.${item.page_number} · ${item.text.slice(0, 100)}`); option.value = item.claim_id; claims.append(option); });
  const designs = node("select", "form-control"); designs.multiple = true; designs.size = Math.min(4, Math.max(2, catalog.designs.length));
  catalog.designs.forEach((item) => { const option = node("option", "", item.title); option.value = item.design_id; designs.append(option); });
  const experiments = node("select", "form-control"); experiments.multiple = true; experiments.size = Math.min(4, Math.max(2, catalog.experiments.length));
  catalog.experiments.forEach((item) => { const option = node("option", "", item.title); option.value = item.experiment_id; experiments.append(option); });
  const evaluations = node("select", "form-control"); evaluations.multiple = true; evaluations.size = Math.min(4, Math.max(2, catalog.evaluations.length));
  catalog.evaluations.forEach((item) => { const option = node("option", "", `${item.task} · ${item.row_count} rows`); option.value = item.evaluation_id; evaluations.append(option); });
  const create = node("button", "review-button", "Create editable draft"); create.type = "submit";
  form.append(labeledControl("Title", title), labeledControl("Document type", type), labeledControl("Research question", question),
    labeledControl("Source-linked claims · unreviewed claims are flagged", claims),
    labeledControl("Approved study designs", designs), labeledControl("Successful experiments", experiments),
    labeledControl("Descriptive evaluations", evaluations), create);
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); create.disabled = true;
    try {
      await api("/api/writing/drafts", {method: "POST", body: JSON.stringify({title: title.value, document_type: type.value,
        research_question: question.value, claim_ids: Array.from(claims.selectedOptions, (item) => item.value),
        design_ids: Array.from(designs.selectedOptions, (item) => item.value),
        experiment_ids: Array.from(experiments.selectedOptions, (item) => item.value),
        evaluation_ids: Array.from(evaluations.selectedOptions, (item) => item.value)})});
      await renderWriting(panel);
    } catch (error) { showError(error.message); create.disabled = false; }
  });
  panel.append(form, node("h2", "subheading", `Saved drafts (${saved.drafts.length})`));
  for (const summary of saved.drafts) {
    const draft = (await api(`/api/writing/drafts/${encodeURIComponent(summary.draft_id)}`)).draft;
    const card = node("article", "method-card");
    card.append(node("strong", "", `${draft.title} · ${draft.status}`),
      node("p", "viewer-meta", `${draft.document_type} · ${draft.citations.length} citation(s) · ${draft.missing_sections.length} sections need researcher writing`));
    if (draft.missing_sections.length) card.append(node("p", "notice", `Unfilled sections: ${draft.missing_sections.join(", ")}`));
    (draft.figures || []).forEach((figure) => {
      const image = node("img", "writing-figure"); image.alt = figure.alt_text; image.src = `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(figure.svg)))}`;
      card.append(image, node("small", "viewer-meta", figure.title));
    });
    const editor = node("textarea", "form-control"); editor.value = draft.content; editor.maxLength = 2000000; editor.rows = 20; editor.readOnly = draft.status !== "draft" && draft.status !== "revision_requested";
    card.append(labeledControl("Editable Markdown draft", editor));
    if (draft.status === "draft" || draft.status === "revision_requested") {
      const save = node("button", "review-button", "Save edits"); save.type = "button";
      save.addEventListener("click", async () => { save.disabled = true; try { await api(`/api/writing/drafts/${encodeURIComponent(draft.draft_id)}`, {method: "PUT", body: JSON.stringify({content: editor.value})}); await renderWriting(panel); } catch (error) { showError(error.message); save.disabled = false; } }); card.append(save);
      const note = node("textarea", "form-control"); note.required = true; note.maxLength = 2000; note.placeholder = "Review note required"; card.append(labeledControl("Researcher review", note));
      for (const [action, label] of [["approve", "Approve for export"], ["request_revision", "Request revision"], ["reject", "Reject draft"]]) {
        const button = node("button", "review-button", label); button.type = "button";
        button.addEventListener("click", async () => { button.disabled = true; try { await api(`/api/writing/drafts/${encodeURIComponent(draft.draft_id)}/review`, {method: "POST", body: JSON.stringify({action, note: note.value})}); await renderWriting(panel); } catch (error) { showError(error.message); button.disabled = false; } }); card.append(button);
      }
    }
    if (draft.status === "approved") {
      const format = node("select", "form-control"); [["markdown", "Markdown"], ["latex", "LaTeX"], ["docx", "DOCX"], ["pdf", "PDF"]].forEach(([value, label]) => { const option = node("option", "", label); option.value = value; format.append(option); });
      const exportButton = node("button", "review-button", "Export approved draft"); exportButton.type = "button";
      exportButton.addEventListener("click", async () => { exportButton.disabled = true; try { await api(`/api/writing/drafts/${encodeURIComponent(draft.draft_id)}/export`, {method: "POST", body: JSON.stringify({format: format.value})}); await renderWriting(panel); } catch (error) { showError(error.message); exportButton.disabled = false; } });
      card.append(labeledControl("Export format", format), exportButton);
    }
    draft.exports.forEach((item) => { const link = node("a", "source-page-link", `Download ${item.filename} · ${item.byte_size} bytes · SHA-256 ${item.sha256.slice(0, 16)}`); link.href = `/api/writing/drafts/${encodeURIComponent(draft.draft_id)}/exports/${encodeURIComponent(item.filename)}`; card.append(link); });
    panel.append(card);
  }
}

async function renderExperiments(panel) {
  panel.replaceChildren();
  panel.append(panelHeading("Experiments and reproducibility", "Datasets are preserved by content hash. Python runs require a locally available, digest-pinned Docker image and use a network-disabled, resource-limited container. ResearchOS never runs submitted code directly on the host."));
  const [datasetResult, experimentResult, evaluationResult, state] = await Promise.all([api("/api/datasets"), api("/api/experiments"), api("/api/evaluations"), api("/api/state")]);
  const importForm = node("form", "methodology-form");
  const file = node("input", "form-control"); file.type = "file"; file.accept = ".csv,.json,application/json,text/csv";
  const importButton = node("button", "review-button", "Import dataset"); importButton.type = "submit";
  importForm.append(labeledControl("CSV or JSON row dataset · max 50 MiB", file), importButton);
  importForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!file.files?.[0]) { showError("Choose a CSV or JSON dataset first."); return; }
    importButton.disabled = true;
    try {
      const blob = file.files[0]; const bytes = new Uint8Array(await blob.arrayBuffer());
      let binary = ""; for (let offset = 0; offset < bytes.length; offset += 0x8000) binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
      await api("/api/datasets", {method: "POST", body: JSON.stringify({filename: blob.name, data_base64: btoa(binary)})});
      await renderExperiments(panel);
    } catch (error) { showError(error.message); importButton.disabled = false; }
  });
  panel.append(node("h2", "subheading", "Import and version datasets"), importForm);
  datasetResult.datasets.forEach((dataset) => {
    const card = node("article", "method-card");
    card.append(node("strong", "", dataset.filename), node("p", "viewer-meta", `${dataset.format.toUpperCase()} · ${dataset.summary.row_count} rows · ${dataset.byte_size} bytes`),
      node("small", "", `SHA-256 · ${dataset.sha256}`),
      node("p", "viewer-meta", `Heuristic profile · ${dataset.summary.profiled_row_count}/${dataset.summary.row_count} rows sampled${dataset.summary.profile_complete ? " (complete)" : " (partial)"}`));
    const profiledColumns = Object.entries(dataset.summary.profile || {}).slice(0, 20).map(([name, profile]) =>
      `${name}: ${profile.inferred_type}, ${profile.missing_count} missing${profile.minimum === null ? "" : `, range ${profile.minimum}–${profile.maximum}`}`);
    if (profiledColumns.length) card.append(node("small", "", `Columns · ${profiledColumns.join(" · ")}${Object.keys(dataset.summary.profile || {}).length > 20 ? " · …" : ""}`));
    if (dataset.parent_dataset_ids.length) card.append(node("p", "", `Derived from ${dataset.parent_dataset_ids.length} dataset(s): ${dataset.transformation_description}`));
    const link = node("a", "source-page-link", "Download preserved dataset"); link.href = `/api/datasets/${dataset.dataset_id}/file`; link.download = dataset.filename; card.append(link);
    if (dataset.summary.columns.length >= 2) {
      const form = node("form", "methodology-form");
      const task = node("select", "form-control"); [["classification", "Classification"], ["regression", "Regression"]].forEach(([value, label]) => { const option = node("option", "", label); option.value = value; task.append(option); });
      const actual = node("select", "form-control"); const predicted = node("select", "form-control");
      dataset.summary.columns.forEach((column) => { [actual, predicted].forEach((select) => { const option = node("option", "", column); option.value = column; select.append(option); }); });
      actual.value = dataset.summary.columns[0]; predicted.value = dataset.summary.columns[1];
      const score = node("button", "review-button", "Compute descriptive prediction metrics"); score.type = "submit";
      form.append(labeledControl("Task", task), labeledControl("Actual values", actual), labeledControl("Predictions", predicted), score);
      form.addEventListener("submit", async (event) => {
        event.preventDefault(); score.disabled = true;
        try {
          await api(`/api/datasets/${dataset.dataset_id}/evaluate`, {method: "POST", body: JSON.stringify({task: task.value, actual_column: actual.value, predicted_column: predicted.value})});
          await renderExperiments(panel);
        } catch (error) { showError(error.message); score.disabled = false; }
      });
      card.append(form);
    }
    panel.append(card);
  });
  panel.append(node("h2", "subheading", `Saved prediction evaluations (${evaluationResult.evaluations.length})`));
  evaluationResult.evaluations.forEach((record) => {
    const card = node("article", "method-card"); card.append(node("strong", "", `${record.task} · ${record.row_count} rows`),
      node("p", "viewer-meta", `Dataset ${record.dataset_sha256.slice(0, 16)} · ${record.actual_column} vs ${record.predicted_column}`),
      node("pre", "version-report", JSON.stringify(record.metrics, null, 2)), node("p", "notice", record.warning));
    const chart = renderEvaluationChart(record.visualization); if (chart) card.append(chart); panel.append(card);
  });

  const form = node("form", "methodology-form experiment-form");
  const title = node("input", "form-control"); title.required = true; title.maxLength = 200; title.placeholder = "Experiment name";
  const runType = node("select", "form-control");
  [["python", "Python source"], ["notebook", "Jupyter notebook (.ipynb)"]].forEach(([value, label]) => { const option = node("option", "", label); option.value = value; runType.append(option); });
  const source = node("textarea", "form-control"); source.required = true; source.maxLength = 512000; source.placeholder = "Python source to run inside the isolated sandbox";
  const notebookFile = node("input", "form-control"); notebookFile.type = "file"; notebookFile.accept = ".ipynb,application/json"; notebookFile.hidden = true;
  runType.addEventListener("change", () => { const notebook = runType.value === "notebook"; source.hidden = notebook; source.required = !notebook; notebookFile.hidden = !notebook; notebookFile.required = notebook; });
  const seed = node("input", "form-control"); seed.type = "number"; seed.min = "0"; seed.max = "4294967295"; seed.placeholder = "Optional random seed";
  const dependencies = node("textarea", "form-control"); dependencies.maxLength = 12000; dependencies.placeholder = '{"numpy":"2.1.3","pandas":"2.2.3"}';
  const selectedDatasets = node("select", "form-control"); selectedDatasets.multiple = true; selectedDatasets.size = Math.min(5, Math.max(2, datasetResult.datasets.length));
  datasetResult.datasets.forEach((dataset) => { const option = node("option", "", `${dataset.filename} · ${dataset.summary.row_count} rows · ${dataset.sha256.slice(0, 12)}`); option.value = dataset.dataset_id; selectedDatasets.append(option); });
  form.append(labeledControl("Experiment title", title), labeledControl("Execution format", runType),
    labeledControl("Python source · max 512 KB", source), labeledControl("Python notebook · max 2 MiB", notebookFile),
    labeledControl("Seed · optional", seed), labeledControl("Read-only datasets to mount", selectedDatasets),
    labeledControl("Approved dependencies · optional JSON exact pins", dependencies));
  const runButton = node("button", "review-button", state.sandbox_image_configured ? "Run in isolated Docker sandbox" : "Sandbox image not configured");
  runButton.type = "submit"; runButton.disabled = !state.sandbox_image_configured; form.append(runButton);
  const approved = state.sandbox_profile?.packages || {};
  form.append(node("p", "hint", `Execution requires a running Docker daemon, a digest-pinned image, and a matching local manifest. Python ${state.sandbox_profile?.python_version || "not configured"}; approved pins: ${Object.keys(approved).length ? Object.entries(approved).map(([name, version]) => `${name}==${version}`).join(", ") : "none"}. The manifest is an operator attestation; ResearchOS does not inspect packages inside the image or install dependencies. Write retained output files to /artifacts; up to 20 flat regular files and 16 MiB total are preserved. Network, host credentials, and writes to source/dataset mounts are disabled.`));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!window.confirm("Run this untrusted Python source inside the isolated Docker sandbox? Notebook markdown and saved outputs are not executed or treated as results.")) return;
    runButton.disabled = true;
    try {
      const requestedDependencies = dependencies.value.trim() ? JSON.parse(dependencies.value) : {};
      if (!requestedDependencies || typeof requestedDependencies !== "object" || Array.isArray(requestedDependencies)) throw new Error("Dependencies must be a JSON object of exact package pins.");
      let codeInput;
      if (runType.value === "notebook") {
        const file = notebookFile.files?.[0]; if (!file || file.size > 2 * 1024 * 1024) throw new Error("Choose a Jupyter notebook no larger than 2 MiB.");
        codeInput = {notebook: await file.text()};
      } else codeInput = {source: source.value};
      await api("/api/experiments", {method: "POST", body: JSON.stringify({title: title.value, ...codeInput,
        seed: seed.value === "" ? null : Number(seed.value), dependencies: requestedDependencies,
        dataset_ids: Array.from(selectedDatasets.selectedOptions, (option) => option.value)})});
      await renderExperiments(panel);
    } catch (error) { showError(error.message); runButton.disabled = false; }
  });
  panel.append(node("h2", "subheading", "Run Python experiment"), form);

  panel.append(node("h2", "subheading", `Execution records (${experimentResult.experiments.length})`));
  experimentResult.experiments.forEach((record) => {
    const card = node("article", "method-card");
    card.append(node("strong", "", `${record.title} · ${record.status}`), node("p", "viewer-meta", `Created ${record.created_at} · seed ${record.seed ?? "not set"}`),
      node("small", "", `Source SHA-256 · ${record.source_sha256}`));
    if (record.failure) card.append(node("p", "error-banner", record.failure));
    if (record.execution) {
      card.append(node("small", "", `Image · ${record.execution.image} · ${record.execution.duration_seconds}s`));
      const usage = record.execution.resource_usage;
      if (usage?.available) card.append(node("p", "viewer-meta", `Observed resource samples: ${usage.sample_count} · peak sampled CPU ${usage.max_cpu_percent}% · peak sampled memory ${(usage.max_memory_bytes / (1024 * 1024)).toFixed(1)} MiB of ${(usage.memory_limit_bytes / (1024 * 1024)).toFixed(1)} MiB`));
      else if (usage) card.append(node("p", "viewer-meta", "Container resource telemetry was unavailable for this run."));
      if (record.execution.stdout) { const output = node("pre", "version-report", record.execution.stdout); card.append(output); }
      if (record.execution.stderr) { const error = node("pre", "version-report", record.execution.stderr); card.append(error); }
    }
    if (record.artifacts?.length) {
      card.append(node("p", "viewer-meta", `Artifacts (${record.artifacts.length}, bounded to 16 MiB total):`));
      record.artifacts.forEach((artifact) => {
        const link = node("a", "report", `${artifact.filename} · ${artifact.byte_size} bytes · SHA-256 ${artifact.sha256.slice(0, 16)}`);
        link.href = `/api/experiments/${encodeURIComponent(record.experiment_id)}/artifacts/${encodeURIComponent(artifact.filename)}`;
        card.append(link);
      });
    }
    panel.append(card);
  });
}

async function renderMethodology(panel, editDesign = null) {
  panel.replaceChildren();
  const evidenceGraph = await api("/api/claims");
  const evidenceClaims = evidenceGraph.claims || [];
  panel.append(panelHeading("Methodology and study design", "Describe the research problem and select the study type. ResearchOS flags missing inputs and offers conditional guidance; it does not invent a research gap or approve a design for you."));
  let latestAnalysis = null;
  const analyzeForm = node("form", "methodology-form");
  const topic = node("input", "form-control"); topic.maxLength = 1000; topic.required = true; topic.placeholder = "Specific research topic";
  const field = node("input", "form-control"); field.maxLength = 200; field.required = true; field.placeholder = "e.g. Machine Learning, Medical, Social Sciences";
  const type = node("select", "form-control");
  [["", "Select research type"], ["quantitative", "Quantitative"], ["qualitative", "Qualitative"], ["mixed_methods", "Mixed methods"], ["computational", "Computational"], ["theoretical", "Theoretical"], ["systematic_review", "Systematic review"]].forEach(([value, label]) => { const option = node("option", "", label); option.value = value; type.append(option); });
  const problem = node("textarea", "form-control"); problem.maxLength = 4000; problem.placeholder = "State the specific problem; the system will not infer it from the topic.";
  const objectives = node("textarea", "form-control"); objectives.placeholder = "Objectives, one per line";
  const questions = node("textarea", "form-control"); questions.placeholder = "Research questions, one per line";
  const literatureEvidence = node("select", "form-control"); literatureEvidence.multiple = true; literatureEvidence.size = Math.min(6, Math.max(2, evidenceClaims.length));
  evidenceClaims.forEach((claim) => { const option=node("option","",`${claim.verification_status} · ${claim.original_text.slice(0,100)}`); option.value=claim.claim_id; literatureEvidence.append(option); });
  analyzeForm.append(labeledControl("Research topic", topic), labeledControl("Research field", field), labeledControl("Research type", type),
    labeledControl("Problem statement", problem), labeledControl("Objectives", objectives), labeledControl("Research questions", questions),
    labeledControl("Related literature claims · select reviewed evidence", literatureEvidence));
  const analyzeButton = node("button", "review-button", "Analyze problem and methodology fit"); analyzeButton.type = "submit"; analyzeForm.append(analyzeButton);
  const analysisBox = node("div", "methodology-analysis");
  analyzeForm.addEventListener("submit", async (event) => {
    event.preventDefault(); analyzeButton.disabled = true; analysisBox.replaceChildren(node("p", "viewer-meta", "Checking supplied inputs…"));
    try {
      latestAnalysis = await api("/api/methodology/analyze", {method: "POST", body: JSON.stringify({topic: topic.value, research_field: field.value,
        research_type: type.value || null, problem_statement: problem.value, objectives: objectives.value.split("\n").map((item) => item.trim()).filter(Boolean),
        research_questions: questions.value.split("\n").map((item) => item.trim()).filter(Boolean),
        evidence_claim_ids: Array.from(literatureEvidence.selectedOptions, (option) => option.value)})});
      analysisBox.replaceChildren(node("strong", "", `Domain pack: ${latestAnalysis.domain_pack.replaceAll("_", " ")} · ${latestAnalysis.research_type || "research type not selected"}`));
      if (latestAnalysis.missing_information.length) {
        analysisBox.append(node("h3", "subheading", "Information still needed"));
        const missing = node("ul", "context-differences"); latestAnalysis.missing_information.forEach((item) => missing.append(node("li", "", item))); analysisBox.append(missing);
      }
      if (latestAnalysis.recommendations.length) {
        analysisBox.append(node("h3", "subheading", "Conditional methodology guidance"));
        latestAnalysis.recommendations.forEach((item) => {
          const card = node("article", "method-card"); card.append(node("strong", "", item.method), node("p", "", item.rationale));
          item.assumptions.forEach((assumption) => card.append(node("small", "", `Assumption: ${assumption}`)));
          analysisBox.append(card);
        });
      }
      if (latestAnalysis.feasibility_checks.length) {
        analysisBox.append(node("h3", "subheading", "Feasibility and sampling checks"));
        latestAnalysis.feasibility_checks.forEach((item) => {
          const card = node("article", "method-card"); card.append(node("strong", "", item.area), node("p", "", item.check), node("small", "", item.rationale));
          analysisBox.append(card);
        });
      }
      if (latestAnalysis.literature_context.length) {
        analysisBox.append(node("h3", "subheading", "Linked literature evidence"));
        latestAnalysis.literature_context.forEach((item) => {
          const evidence=node("article","method-card"); evidence.append(node("strong","",`${item.verification_status} · ${item.evidence_category.replaceAll("_"," ")} · page ${item.page_number}`),
            node("p","",item.claim),node("small","",item.source_excerpt)); analysisBox.append(evidence);
        });
      }
      latestAnalysis.cautions.forEach((item) => analysisBox.append(node("p", "notice", item)));
      const refineButton = node("button", "review-button", aiProviderConfigured ? "Suggest questions and hypotheses from reviewed literature" : "AI question refinement not configured");
      refineButton.type = "button"; refineButton.disabled = !aiProviderConfigured;
      const refinementResults = node("div", "question-refinement-results");
      if (aiProviderConfigured) refinementResults.append(node("p", "hint", "This sends the stated problem, objectives, and selected reviewed passages to your configured provider. Suggestions must cite those claims and remain editable."));
      refineButton.addEventListener("click", async () => {
        const selectedIds = Array.from(literatureEvidence.selectedOptions, (option) => option.value);
        if (!problem.value.trim() || !type.value || !objectives.value.trim() || !selectedIds.length) {
          showError("Provide a problem statement, research type, objectives, and reviewed literature claims before refinement."); return;
        }
        refineButton.disabled = true; refinementResults.replaceChildren(node("p", "viewer-meta", "Preparing source-linked suggestions…"));
        try {
          const result = await api("/api/methodology/refine", {method: "POST", body: JSON.stringify({problem_statement: problem.value,
            research_type: type.value, objectives: objectives.value.split("\n").map((item) => item.trim()).filter(Boolean),
            evidence_claim_ids: selectedIds})});
          refinementResults.replaceChildren(node("strong", "", "Proposed wording · review before use"));
          const evidenceById = new Map(result.evidence_context.map((item) => [item.claim_id, item]));
          const renderProposals = (items, fieldKey, textarea) => items.forEach((proposal) => {
            const card = node("article", "method-card"); card.append(node("strong", "", proposal.text), node("p", "", proposal.rationale));
            proposal.evidence_claim_ids.forEach((claimId) => {
              const evidence = evidenceById.get(claimId); if (!evidence) return;
              card.append(node("small", "", `Source claim · page ${evidence.page_number}${evidence.section_name ? ` · ${evidence.section_name}` : ""}: ${evidence.claim}`));
              const sourceLink = node("a", "source-page-link", `Open source PDF · page ${evidence.page_number}`);
              sourceLink.href = `/api/documents/${evidence.document_id}/file#page=${evidence.page_number}`; sourceLink.target = "_blank"; sourceLink.rel = "noopener"; card.append(sourceLink);
            });
            const use = node("button", "review-button", `Add to ${fieldKey === "research_questions" ? "research questions" : "hypotheses"}`); use.type = "button";
            use.addEventListener("click", () => {
              const current = textarea.value.split("\n").map((item) => item.trim()).filter(Boolean);
              if (!current.includes(proposal.text)) textarea.value = [...current, proposal.text].join("\n");
              proposal.evidence_claim_ids.forEach((claimId) => Array.from(designEvidence.options).forEach((option) => { if (option.value === claimId) option.selected = true; }));
              use.textContent = "Added · editable in draft"; use.disabled = true;
            });
            refinementResults.append(card);
          });
          renderProposals(result.questions, "research_questions", questions);
          renderProposals(result.hypotheses, "hypotheses", controls.hypotheses);
          result.cautions.forEach((item) => refinementResults.append(node("p", "notice", item)));
          if (!result.questions.length && !result.hypotheses.length) refinementResults.append(node("p", "empty-card", "No source-supported wording was returned. Add or review relevant literature before refining."));
        } catch (error) { refinementResults.replaceChildren(node("div", "error-banner", error.message)); }
        finally { refineButton.disabled = false; }
      });
      analysisBox.append(refineButton, refinementResults);
      const startDraft = node("button", "review-button", "Use these inputs in a study design draft"); startDraft.type = "button";
      startDraft.addEventListener("click", () => {
        editDesign = {title: topic.value.slice(0, 300), research_field: field.value, research_type: type.value,
          problem_statement: problem.value, objectives: objectives.value.split("\n").map((item) => item.trim()).filter(Boolean),
          research_questions: questions.value.split("\n").map((item) => item.trim()).filter(Boolean),
          hypotheses: controls.hypotheses.value.split("\n").map((item) => item.trim()).filter(Boolean),
          evidence_claim_ids: Array.from(literatureEvidence.selectedOptions, (option) => option.value)};
        renderMethodology(panel, editDesign);
      });
      analysisBox.append(startDraft);
    } catch (error) { analysisBox.replaceChildren(node("div", "error-banner", error.message)); }
    finally { analyzeButton.disabled = false; }
  });
  panel.append(analyzeForm, analysisBox);

  const designForm = node("form", "methodology-form study-design-form");
  const controls = {};
  const makeInput = (key, label, multiline = false, placeholder = "") => {
    const control = node(multiline ? "textarea" : "input", "form-control"); control.placeholder = placeholder;
    controls[key] = control; designForm.append(labeledControl(label, control)); return control;
  };
  makeInput("title", "Design title"); makeInput("research_field", "Research field");
  const designType = node("select", "form-control");
  [["quantitative", "Quantitative"], ["qualitative", "Qualitative"], ["mixed_methods", "Mixed methods"], ["computational", "Computational"], ["theoretical", "Theoretical"], ["systematic_review", "Systematic review"]].forEach(([value, label]) => { const option=node("option", "", label); option.value=value; designType.append(option); });
  controls.research_type = designType; designForm.append(labeledControl("Research type", designType));
  const designEvidence = node("select", "form-control"); designEvidence.multiple=true; designEvidence.size=Math.min(6,Math.max(2,evidenceClaims.length));
  evidenceClaims.forEach((claim)=>{const option=node("option","",`${claim.verification_status} · ${claim.original_text.slice(0,100)}`);option.value=claim.claim_id;designEvidence.append(option);});
  controls.evidence_claim_ids = designEvidence; designForm.append(labeledControl("Literature claims · approval requires reviewed source evidence", designEvidence));
  makeInput("problem_statement", "Research problem", true); makeInput("objectives", "Objectives · one per line", true);
  makeInput("research_questions", "Research questions · one per line", true); makeInput("hypotheses", "Hypotheses · one per line", true);
  makeInput("methodology", "Proposed methodology", true); makeInput("methodology_rationale", "Why this method fits the question", true);
  makeInput("population_or_material", "Population or study material", true); makeInput("sample_size", "Sample size or justification", true);
  makeInput("sampling_method", "Sampling or selection method", true);
  makeInput("independent_variables", "Independent variables · one per line", true);
  makeInput("dependent_variables", "Dependent variables · one per line", true);
  makeInput("control_variables", "Control variables · one per line", true);
  makeInput("conditions", "Experimental or comparison conditions · one per line", true);
  makeInput("data_collection", "Data collection", true); makeInput("analysis_plan", "Analysis plan", true);
  makeInput("evaluation_metrics", "Evaluation metrics · one per line", true);
  makeInput("limitations", "Known limitations · one per line", true); makeInput("ethics_notes", "Ethical considerations / approval status", true);
  const saveDesign = node("button", "dialog-submit", editDesign?.design_id ? "Update draft" : "Save study design draft"); saveDesign.type = "submit"; designForm.append(saveDesign);
  if (editDesign) {
    for (const key of Object.keys(controls)) {
      if (["objectives", "research_questions", "hypotheses", "conditions", "evaluation_metrics", "limitations"].includes(key)) controls[key].value = (editDesign[key] || []).join("\n");
      else if (["independent_variables", "dependent_variables", "control_variables"].includes(key)) controls[key].value = (editDesign.variables?.[key.replace("_variables", "")] || []).join("\n");
      else if (key === "evidence_claim_ids") Array.from(controls[key].options).forEach((option) => { option.selected = (editDesign.evidence_claim_ids || []).includes(option.value); });
      else controls[key].value = editDesign[key] || "";
    }
  }
  designForm.addEventListener("submit", async (event) => {
    event.preventDefault(); saveDesign.disabled = true;
    const lines = (key) => controls[key].value.split("\n").map((item) => item.trim()).filter(Boolean);
    const payload = {title: controls.title.value, research_field: controls.research_field.value, research_type: controls.research_type.value,
      problem_statement: controls.problem_statement.value, objectives: lines("objectives"), research_questions: lines("research_questions"), hypotheses: lines("hypotheses"),
      methodology: controls.methodology.value, methodology_rationale: controls.methodology_rationale.value,
      population_or_material: controls.population_or_material.value, sample_size: controls.sample_size.value, sampling_method: controls.sampling_method.value,
      variables: {independent: lines("independent_variables"), dependent: lines("dependent_variables"), control: lines("control_variables")},
      conditions: lines("conditions"), data_collection: controls.data_collection.value, analysis_plan: controls.analysis_plan.value,
      evaluation_metrics: lines("evaluation_metrics"), limitations: lines("limitations"), ethics_notes: controls.ethics_notes.value,
      evidence_claim_ids: Array.from(controls.evidence_claim_ids.selectedOptions, (option) => option.value)};
    try {
      const saved = editDesign?.design_id
        ? await api(`/api/methodology/designs/${editDesign.design_id}`, {method: "PUT", body: JSON.stringify(payload)})
        : await api("/api/methodology/designs", {method: "POST", body: JSON.stringify(payload)});
      renderMethodology(panel, saved.design);
    } catch (error) { showError(error.message); saveDesign.disabled = false; }
  });
  panel.append(node("h2", "subheading", editDesign ? "Edit study design draft" : "Study design draft"), designForm);

  const statisticForm = node("form", "methodology-form statistics-form");
  const outcome = node("select", "form-control");
  [["unknown", "Select outcome"], ["continuous", "Continuous"], ["binary", "Binary"], ["categorical", "Categorical"], ["count", "Count"], ["ordinal", "Ordinal"], ["time_to_event", "Time to event"]].forEach(([value,label])=>{const option=node("option","",label);option.value=value;outcome.append(option);});
  const design = node("select", "form-control");
  [["unknown", "Select design"], ["two_independent_groups", "Two independent groups"], ["paired_two_conditions", "Paired conditions"], ["multiple_groups", "Multiple groups"], ["association", "Association"], ["one_sample", "One sample"]].forEach(([value,label])=>{const option=node("option","",label);option.value=value;design.append(option);});
  const n = node("input", "form-control"); n.type = "number"; n.min = "1"; n.placeholder = "Total sample size";
  const summary = node("textarea", "form-control"); summary.placeholder = 'Optional descriptive summary JSON, e.g. {"missing_fraction":0.05,"group_sizes":[30,32]}';
  const independent = node("select", "form-control"); [["unknown","Not assessed"],["yes","Independent"],["no","Dependent / clustered"]].forEach(([value,label])=>{const option=node("option","",label);option.value=value;independent.append(option);});
  const distribution = node("input"); distribution.type="checkbox"; const variance = node("input"); variance.type="checkbox";
  statisticForm.append(labeledControl("Outcome type", outcome), labeledControl("Study design", design), labeledControl("Total sample size", n), labeledControl("Observation independence", independent),
    labeledControl("Distribution / residuals assessed", distribution), labeledControl("Variance structure assessed", variance),
    labeledControl("Optional dataset summary (descriptive checks only)", summary));
  const recommendStats = node("button", "review-button", "Check statistical method assumptions"); recommendStats.type="submit"; statisticForm.append(recommendStats);
  const statisticResults = node("div", "methodology-analysis");
  statisticForm.addEventListener("submit", async (event) => {
    event.preventDefault(); recommendStats.disabled=true;
    try {
      const result = await api("/api/methodology/statistics", {method:"POST",body:JSON.stringify({outcome_type:outcome.value,design:design.value,
        sample_size:n.value ? Number(n.value) : null, independent_observations: independent.value === "unknown" ? null : independent.value === "yes",
        distribution_assessed:distribution.checked,variance_assessed:variance.checked,
        dataset_summary:summary.value.trim() ? JSON.parse(summary.value) : null})});
      statisticResults.replaceChildren(node("strong", "", "Conditional candidates · guidance only"));
      result.candidate_methods.forEach((method) => statisticResults.append(node("p", "", method)));
      if (result.assumptions.length) { statisticResults.append(node("h3", "subheading", "Assumptions to inspect")); result.assumptions.forEach((item)=>statisticResults.append(node("p","viewer-meta",`• ${item}`))); }
      if (result.missing_information.length) { statisticResults.append(node("h3", "subheading", "Required checks / missing inputs")); result.missing_information.forEach((item)=>statisticResults.append(node("p","viewer-meta",`• ${item}`))); }
      if (result.dataset_diagnostics.length) { statisticResults.append(node("h3", "subheading", "Descriptive summary checks")); result.dataset_diagnostics.forEach((item)=>statisticResults.append(node("p","viewer-meta",`• ${item}`))); }
      statisticResults.append(node("p", "notice", result.warning));
    } catch (error) { statisticResults.replaceChildren(node("div", "error-banner", error.message)); }
    finally { recommendStats.disabled=false; }
  });
  panel.append(node("h2", "subheading", "Statistical method assumptions"), statisticForm, statisticResults);

  const saved = await api("/api/methodology/designs");
  panel.append(node("h2", "subheading", `Saved study designs (${saved.designs.length})`));
  saved.designs.forEach((record) => {
    const card = node("article", "method-card");
    card.append(node("strong", "", record.title), node("p", "viewer-meta", `${record.research_field} · ${record.research_type.replaceAll("_", " ")} · ${record.status}`));
    if (record.problem_statement) card.append(node("p", "", record.problem_statement));
    if (record.status === "draft") {
      const edit = node("button", "review-button", "Edit draft"); edit.type="button"; edit.addEventListener("click",()=>renderMethodology(panel,record)); card.append(edit);
      const approve = node("button", "review-button", "Approve design"); approve.type="button";
      approve.addEventListener("click",async()=>{const note=window.prompt("Approval note: confirm that you reviewed the research problem, methods, assumptions, and limitations.");if(note===null)return;
        try{await api(`/api/methodology/designs/${record.design_id}/approve`,{method:"PUT",body:JSON.stringify({reviewer_note:note})});renderMethodology(panel);}
        catch(error){showError(error.message);}});card.append(approve);
    } else if (record.reviewer_note) card.append(node("small", "", `Reviewer note: ${record.reviewer_note}`));
    panel.append(card);
  });
}

async function uploadPdf(file) {
  if (!file) return;
  if (file.size > 20 * 1024 * 1024) { showError("PDFs must be 20 MB or smaller."); return; }
  const strip = el("#attachments");
  const pill = node("div", "attachment-pill", `Uploading ${file.name}…`);
  strip.append(pill);
  try {
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(file);
    });
    const encoded = String(dataUrl).split(",")[1] || "";
    const result = await api("/api/upload", {method: "POST", body: JSON.stringify({filename: file.name, data: encoded})});
    const doc = result.document;
    pill.replaceChildren(node("span", "", `PDF saved · ${doc.extraction_status.replaceAll("_", " ")}`));
    const remove = node("button", "", "×"); remove.title = "Dismiss"; remove.addEventListener("click", () => pill.remove()); pill.append(remove);
    if (activeChatId) {
      const note = `Imported document: ${doc.filename}\nSHA-256: ${doc.sha256}\nExtraction status: ${doc.extraction_status}${doc.extraction_status === "extractor_unavailable" ? " (install the optional pypdf dependency to extract page text)" : ""}`;
      const sent = await api("/api/chat", {method: "POST", body: JSON.stringify({chat_id: activeChatId, message: note})});
      activeChat = sent.chat; renderMessages(); await loadChats();
    }
  } catch (error) {
    pill.textContent = `Upload failed · ${error.message}`;
    pill.classList.add("upload-error");
  }
  el("#fileInput").value = "";
}

async function submitResearch(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const topic = String(form.get("topic") || "").trim();
  const field = String(form.get("field") || "").trim();
  const button = event.currentTarget.querySelector(".dialog-submit");
  el("#dialogError").hidden = true; el("#dialogError").textContent = "";
  button.disabled = true; button.textContent = "Searching providers…";
  try {
    const data = await api("/api/research", {method: "POST", body: JSON.stringify({topic, field, chat_id: activeChatId, search: form.has("search"), max_papers: 10})});
    el("#researchDialog").close();
    if (data.chat) { activeChat = data.chat; renderMessages(); await loadChats(); }
    setView("chat");
  } catch (error) {
    el("#dialogError").textContent = error.message;
    el("#dialogError").hidden = false;
  } finally {
    button.disabled = false; button.innerHTML = 'Create research brief <span>→</span>';
  }
}

el("#newChat").addEventListener("click", newChat);
el("#refreshChats").addEventListener("click", loadChats);
el("#sendButton").addEventListener("click", () => sendMessage());
inputEl.addEventListener("input", resizeInput);
inputEl.addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendMessage(); } });
el("#fileInput").addEventListener("change", (event) => uploadPdf(event.target.files[0]));
el("#inputStyle").addEventListener("change", (event) => {
  inputEl.placeholder = event.target.value === "command" ? "Enter a ResearchOS command or /help…" : event.target.value === "note" ? "Write a research note to save in this chat…" : "Ask about your research or describe a topic…";
});
el("#researchForm").addEventListener("submit", submitResearch);
el("#newResearch").addEventListener("click", () => el("#researchDialog").showModal());
el("#showVersions").addEventListener("click", () => setView("versions"));
document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
document.querySelectorAll(".starter").forEach((button) => button.addEventListener("click", () => {
  const prompt = button.dataset.prompt;
  if (prompt.startsWith("Create a research brief")) { el("#researchDialog").showModal(); return; }
  inputEl.value = prompt; resizeInput(); inputEl.focus();
}));
el("#micButton").addEventListener("click", () => {
  const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Speech) { showError("Voice input is not available in this browser. Try a browser that supports Web Speech."); return; }
  const recognition = new Speech(); recognition.lang = navigator.language || "en-US"; recognition.interimResults = true;
  recognition.onresult = (event) => { inputEl.value = Array.from(event.results).map((item) => item[0].transcript).join(""); resizeInput(); };
  recognition.onerror = () => showError("Voice input could not be completed."); recognition.start();
});

(async function init() {
  try {
    await loadState();
    const data = await api("/api/chats");
    if (data.chats.length) await openChat(data.chats[0].id);
    else await newChat();
  } catch (error) { showError(error.message); }
})();

setInterval(async () => {
  try { await loadState(); if (!el("#panelView").hidden && el("#viewTitle").textContent === "Versions & progress") renderPanel("versions"); }
  catch (_) { /* Keep the last visible state if a polling request fails. */ }
}, 12000);
