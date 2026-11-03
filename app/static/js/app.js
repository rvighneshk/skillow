document.addEventListener("DOMContentLoaded", () => {
  const $ = (selector) => document.querySelector(selector);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const id = window.SKILLOW_TUTORIAL_ID;
  const video = $("#tutorial-video");

  async function readApiResponse(response) {
    const raw = await response.text();
    let data;
    try { data = raw ? JSON.parse(raw) : {}; }
    catch {
      const clean = raw.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
      throw new Error(`Server error (${response.status}): ${(clean || response.statusText || "empty response").slice(0, 240)}`);
    }
    if (!response.ok) {
      const detail = data.detail || data.message || data.error || `Request failed (${response.status}).`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function markdownToHtml(source) {
    const escHtml = (v) => esc(v);
    const safeInline = (raw) => {
      let value = escHtml(raw);
      value = value.replace(/`([^`]+)`/g, "<code>$1</code>");
      value = value.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
      value = value.replace(/__([^_]+)__/g, "<strong>$1</strong>");
      value = value.replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>");
      value = value.replace(/(^|[^_])_([^_]+)_(?!_)/g, "$1<em>$2</em>");
      value = value.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
      return value;
    };
    const lines = String(source || "").replace(/\r/g, "").replace(/\\+n/g, "\n").split("\n");
    const blocks = [];
    let paragraph = [], list = null, code = [], quote = [];
    const closeList = () => { if (list) { blocks.push(`<${list.tag}>${list.items.map(x => `<li>${safeInline(x)}</li>`).join("")}</${list.tag}>`); list = null; } };
    const flushParagraph = () => { if (paragraph.length) { blocks.push(`<p>${paragraph.map(safeInline).join("<br>")}</p>`); paragraph = []; } };
    const flushQuote = () => { if (quote.length) { blocks.push(`<blockquote>${quote.map(x => `<p>${safeInline(x)}</p>`).join("")}</blockquote>`); quote = []; } };
    const flushText = () => { flushParagraph(); closeList(); flushQuote(); };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (/^\s*```/.test(line)) {
        flushText();
        if (code.length || (i > 0 && /^\s*```/.test(lines[i-1]))) {
          blocks.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`); code = [];
        } else {
          i++;
          while (i < lines.length && !/^\s*```/.test(lines[i])) { code.push(lines[i]); i++; }
          blocks.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`); code = [];
        }
        continue;
      }
      if (!line.trim()) { flushText(); continue; }
      const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
      if (heading) { flushText(); const n = heading[1].length; blocks.push(`<h${n}>${safeInline(heading[2])}</h${n}>`); continue; }
      if (/^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/.test(line)) { flushText(); blocks.push("<hr>"); continue; }
      const q = line.match(/^\s*>\s?(.*)$/);
      if (q) { flushParagraph(); closeList(); quote.push(q[1]); continue; }
      if (quote.length) flushQuote();
      const bullet = line.match(/^\s*[-*+]\s+(.+)$/);
      const numbered = line.match(/^\s*\d+[.)]\s+(.+)$/);
      if (bullet || numbered) {
        flushParagraph();
        const wanted = bullet ? "ul" : "ol";
        if (list && list.tag !== wanted) closeList();
        if (!list) list = {tag:wanted, items:[]};
        list.items.push((bullet || numbered)[1]);
        continue;
      }
      closeList();
      paragraph.push(line.trim());
    }
    flushText();
    return blocks.join("\n");
  }
  document.querySelectorAll(".markdown-content").forEach((el) => { el.innerHTML = markdownToHtml(el.textContent); });

  const stageIcon = {pending: "", done: "✓", skipped: "–", failed: "!"};
  function renderStages(el, stages) {
    if (!el) return;
    el.hidden = false;
    el.innerHTML = (stages || []).map((s) => `<li class="job-stage is-${esc(s.state)}"><span class="job-dot">${s.state === "running" ? '<span class="spinner"></span>' : stageIcon[s.state] ?? ""}</span><span><b>${esc(s.label)}</b>${s.detail ? `<small>${esc(s.detail)}</small>` : ""}</span></li>`).join("");
  }

  const videoInput = $("#video"), videoDrop = $("#video-drop");
  const sourceModes = document.querySelectorAll('input[name="video_source_mode"]');
  const uploadField = $("#video-upload-field"), urlField = $("#video-url-field"), videoUrl = $("#video_url");
  function syncVideoSource() {
    const mode = document.querySelector('input[name="video_source_mode"]:checked')?.value || "upload";
    if (uploadField) uploadField.hidden = mode !== "upload";
    if (urlField) urlField.hidden = mode !== "url";
    if (videoInput) videoInput.required = mode === "upload";
    if (videoUrl) videoUrl.required = mode === "url";
  }
  sourceModes.forEach((radio) => radio.addEventListener("change", syncVideoSource));
  syncVideoSource();


  if (videoInput && videoDrop) {
    const setName = (file) => { $("#file-name").textContent = file ? file.name : "Drop your tutorial here"; videoDrop.classList.toggle("has-file", !!file); };
    videoInput.addEventListener("change", () => setName(videoInput.files?.[0]));
    ["dragenter", "dragover"].forEach((t) => videoDrop.addEventListener(t, (e) => { e.preventDefault(); videoDrop.classList.add("dragover"); }));
    ["dragleave", "drop"].forEach((t) => videoDrop.addEventListener(t, (e) => { e.preventDefault(); videoDrop.classList.remove("dragover"); }));
    videoDrop.addEventListener("drop", (e) => {
      const file = e.dataTransfer?.files?.[0];
      if (file && file.type.startsWith("video/")) { videoInput.files = e.dataTransfer.files; setName(file); }
    });
    videoInput.closest("form").addEventListener("submit", (e) => {
      const button = e.target.querySelector("button[type=submit]");
      if (e.target.dataset.busy === "1") { e.preventDefault(); return; }
      e.target.dataset.busy = "1";
      if (button) { button.disabled = true; button.firstChild.textContent = "Uploading… "; }
    });
  }
  document.querySelectorAll(".demo-case-footer form").forEach((form) => form.addEventListener("submit", (e) => {
    if (form.dataset.busy === "1") { e.preventDefault(); return; }
    form.dataset.busy = "1";
    const button = form.querySelector("button");
    if (button) { button.disabled = true; button.firstChild.textContent = "Opening… "; }
  }));

  const photoInput = $("#photo");
  if (photoInput) photoInput.addEventListener("change", () => {
    const file = photoInput.files?.[0], drop = $("#photo-drop"), name = $("#photo-name");
    if (name) name.textContent = file ? file.name : "Add a photo of your situation";
    if (drop && file && file.type.startsWith("image/")) {
      drop.querySelector(".prepared-photo-preview")?.remove();
      drop.querySelector(".prepared-photo-status")?.remove();
      const preview = document.createElement("img");
      preview.className = "prepared-photo-preview";
      preview.alt = "Selected setup photo preview";
      preview.src = URL.createObjectURL(file);
      drop.insertBefore(preview, drop.firstChild);
    }
  });

  if (id && $("#process-status")) {
    const poll = async () => {
      try {
        const data = await readApiResponse(await fetch(`/api/tutorials/${id}/status`));
        renderStages($("#process-stages"), data.stages);
        if (data.status === "completed" || data.status === "failed") { location.reload(); return; }
        $("#process-status").textContent = `Current status: ${data.status}`;
      } catch { /* retry on the next tick */ }
      setTimeout(poll, 2000);
    };
    poll();
  }

  if (video) {
    const start = Number(video.dataset.start || 0);
    if (start > 0) video.addEventListener("loadedmetadata", () => { video.currentTime = start; }, {once: true});
    document.querySelectorAll(".step-row").forEach((b) => b.addEventListener("click", () => {
      video.currentTime = Number(b.dataset.seek || 0); video.play().catch(() => {});
    }));
  }

  document.querySelectorAll(".prompt-suggestions").forEach((group) => {
    const target = document.getElementById(group.dataset.target);
    group.querySelectorAll(".prompt-chip").forEach((chip) => chip.addEventListener("click", () => {
      if (target) { target.value = chip.textContent.trim(); target.focus(); }
    }));
  });
  document.querySelectorAll(".demo-question").forEach((chip) => chip.addEventListener("click", () => {
    const target = document.getElementById(chip.dataset.target);
    if (target) { target.value = chip.textContent.trim(); target.focus(); }
  }));

  async function pollJob(jobId, stages, container) {
    renderStages(container, stages);
    let failures = 0;
    for (;;) {
      await sleep(1500);
      let data;
      try { data = await readApiResponse(await fetch(`/api/jobs/${jobId}`)); failures = 0; }
      catch (error) { if (++failures >= 5) throw error; continue; }
      renderStages(container, data.stages);
      if (data.status === "done") return data.result;
      if (data.status === "failed") throw new Error(data.error || "The comparison could not be completed.");
    }
  }

  const compareForm = $("#compare-form");
  if (compareForm) compareForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (compareForm.dataset.busy === "1") return;
    compareForm.dataset.busy = "1";
    const out = $("#compare-result"), stages = $("#compare-stages"), submit = compareForm.querySelector("button[type=submit]");
    out.innerHTML = "";
    if (submit) { submit.disabled = true; submit.innerHTML = '<span>Working on your guide…</span>'; }
    let redirecting = false;
    try {
      const data = await readApiResponse(await fetch(`/tutorials/${id}/compare`, {method: "POST", body: new FormData(compareForm)}));
      const result = data.comparison_url ? data : await pollJob(data.job_id, data.stages, stages);
      redirecting = true;
      window.location.assign(result.comparison_url);
    } catch (error) {
      out.innerHTML = `<div class="error-inline">${esc(error.message)}</div>`;
    } finally {
      if (!redirecting) {
        compareForm.dataset.busy = "0";
        if (submit) { submit.disabled = false; submit.innerHTML = '<span>Compare & build my guide</span>'; }
      }
    }
  });

  const toggle = $("#overlay-toggle");
  if (toggle) toggle.addEventListener("change", () => {
    document.querySelectorAll(".comparison-images img[data-plain]").forEach((img) => {
      img.src = toggle.checked && img.dataset.overlay ? img.dataset.overlay : img.dataset.plain;
    });
  });

  const retry = $("#retry-illustration");
  if (retry) retry.addEventListener("click", async () => {
    retry.disabled = true; retry.textContent = "Generating…";
    try {
      await readApiResponse(await fetch(`/api/comparisons/${retry.dataset.comparison}/illustration`, {method: "POST"}));
      location.reload();
    } catch (error) { retry.disabled = false; retry.textContent = "Try again"; retry.insertAdjacentHTML("afterend", `<span class="error-inline">${esc(error.message)}</span>`); }
  });

  function bindGuideAudio(steps) {
    if (!Array.isArray(steps) || !steps.length) return;
    if (!("speechSynthesis" in window) || !("SpeechSynthesisUtterance" in window)) {
      document.querySelectorAll(".audio-btn").forEach((b) => b.disabled = true);
      $(".guide-audio-toolbar")?.insertAdjacentHTML("beforeend", '<small class="audio-unavailable">Read aloud is not supported by this browser.</small>');
      return;
    }
    let playingAll = false;
    const stop = $("#guide-stop"), playAll = $("#guide-play-all");
    const clear = () => document.querySelectorAll(".guide-step").forEach((el) => el.classList.remove("is-speaking"));
    function speak(index, chain = false) {
      if (index >= steps.length) { playingAll = false; if (stop) stop.disabled = true; clear(); return; }
      playingAll = chain;
      window.speechSynthesis.cancel();
      document.querySelectorAll(".guide-step").forEach((el, i) => el.classList.toggle("is-speaking", i === index));
      const s = steps[index];
      const cleanSpeech = (value) => String(value || "").replace(/```[\s\S]*?```/g, " code omitted ")
        .replace(/^#{1,6}\s+/gm, "").replace(/\*\*(.*?)\*\*/g, "$1")
        .replace(/[*_`>#]/g, "").replace(/\[([^\]]+)\]\([^)]*\)/g, "$1").replace(/\s+/g, " ").trim();
      const text = [`Step ${index + 1}. ${s.title || ""}`, s.instruction,
        s.why_it_matters ? `Why this matters. ${s.why_it_matters}` : "",
        s.check_before_next_step ? `Before moving on. ${s.check_before_next_step}` : ""]
        .map(cleanSpeech).filter(Boolean).join(". ");
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = 0.94;
      utterance.lang = document.documentElement.lang || "en-US";
      const voice = window.speechSynthesis.getVoices().find((v) => /^en(-|_)/i.test(v.lang));
      if (voice) utterance.voice = voice;
      utterance.onend = () => { if (playingAll) speak(index + 1, true); else { if (stop) stop.disabled = true; clear(); } };
      utterance.onerror = () => { playingAll = false; if (stop) stop.disabled = true; clear(); };
      if (stop) stop.disabled = false;
      window.speechSynthesis.resume();
      window.speechSynthesis.speak(utterance);
    }
    document.querySelectorAll(".audio-one").forEach((b) => b.addEventListener("click", () => speak(Number(b.dataset.speak), false)));
    playAll?.addEventListener("click", () => speak(0, true));
    stop?.addEventListener("click", () => { playingAll = false; window.speechSynthesis.cancel(); stop.disabled = true; clear(); });
  }
  if (Array.isArray(window.SKILLOW_GUIDE_STEPS) && document.querySelector(".guide-step")) bindGuideAudio(window.SKILLOW_GUIDE_STEPS);

  const chatForm = $("#chat-form");
  if (chatForm) chatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (chatForm.dataset.busy === "1") return;
    const input = $("#question"), question = input.value.trim();
    if (!question) return;
    chatForm.dataset.busy = "1";
    const log = $("#chat-log"), user = document.createElement("div");
    user.className = "chat-bubble user-bubble"; user.textContent = question; log.appendChild(user); input.value = "";
    const pending = document.createElement("div");
    pending.className = "chat-bubble ai-bubble chat-pending";
    pending.innerHTML = '<div class="chat-thinking"><span></span><span></span><span></span></div><b class="thinking-label">Thinking through your question…</b><small>Checking the tutorial context and relevant concepts</small>';
    log.appendChild(pending); log.scrollTop = log.scrollHeight;
    try {
      const data = await readApiResponse(await fetch(`/api/tutorials/${id}/ask`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({question, current_timestamp: video?.currentTime ?? null})}));
      pending.innerHTML = `<div class="markdown-content">${markdownToHtml(data.answer || "I couldn't form a useful answer.")}</div><button type="button" class="audio-btn audio-read-markdown">▶ Read answer aloud</button>`;
    } catch (error) { pending.textContent = error.message; }
    pending.classList.remove("chat-pending");
    chatForm.dataset.busy = "0"; log.scrollTop = log.scrollHeight;
  });

  const diagramForm = $("#diagram-form");
  document.addEventListener("click", (event) => {
    const button = event.target.closest(".audio-read-markdown");
    if (!button) return;
    if (!("speechSynthesis" in window) || !("SpeechSynthesisUtterance" in window)) {
      button.textContent = "Read aloud unavailable"; button.disabled = true; return;
    }
    const content = button.closest(".chat-bubble, .diagram-explanation");
    const rendered = content?.querySelector(".markdown-content");
    const plain = (rendered?.innerText || "").replace(/\\s+/g, " ").trim();
    if (!plain) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(plain);
    utterance.lang = document.documentElement.lang || "en-US";
    utterance.rate = 0.95;
    const voice = window.speechSynthesis.getVoices().find((v) => /^en(-|_)/i.test(v.lang));
    if (voice) utterance.voice = voice;
    utterance.onend = () => { button.disabled = false; };
    utterance.onerror = () => { button.disabled = false; };
    button.disabled = true; window.speechSynthesis.speak(utterance);
  });

  if (diagramForm) diagramForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (diagramForm.dataset.busy === "1") return;
    const prompt = $("#diagram-prompt").value.trim(), out = $("#diagram-result");
    if (!prompt) return;
    diagramForm.dataset.busy = "1";
    out.innerHTML = '<div class="loading-state"><span class="spinner"></span><b>Building your answer and visual…</b><p>First-time explanations can take a moment. Repeated prompts use your saved result.</p></div>';
    const submit = diagramForm.querySelector('button[type="submit"]');
    if (submit) { submit.disabled = true; submit.textContent = "Creating…"; }
    try {
      const form = new FormData(); form.append("prompt", prompt);
      const data = await readApiResponse(await fetch(`/tutorials/${id}/diagram`, {method: "POST", body: form}));
      out.innerHTML = `<div class="diagram-result-card"><section class="diagram-explanation"><span class="eyebrow">YOUR ANSWER</span><h3>${esc(data.prompt || prompt)}</h3><div class="markdown-content">${markdownToHtml(data.explanation)}</div><button type="button" class="audio-btn audio-read-markdown">▶ Read answer aloud</button></section>${data.image_url ? `<figure class="diagram-output-image"><img src="${esc(data.image_url)}" alt="Instructional visual for ${esc(data.prompt || prompt)}"><figcaption class="diagram-caption">ILLUSTRATIVE ONLY · Not a verified technical diagram. Check critical details against the original tutorial</figcaption></figure>` : `<div class="visual-unavailable">${esc(data.image_error || "Visual unavailable. The written answer is still ready.")}</div>`}<small class="diagram-disclaimer">${data.cached ? "Saved response · no new AI generation. " : ""}The image is illustrative only, not a verified technical diagram. Check critical measurements, wiring and safety steps against the original tutorial and component instructions.</small></div>`;
    } catch (error) { out.innerHTML = `<div class="error-inline">${esc(error.message)}</div>`; }
    finally { diagramForm.dataset.busy = "0"; if (submit) { submit.disabled = false; submit.textContent = "Get answer + visual"; } }
  });
});
