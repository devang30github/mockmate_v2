import { getMe } from "./auth.js";
import {
  createInterview, createJobDescription, deleteDocument, listDocuments, uploadResume,
} from "./data.js";

if (!(await getMe())) location.replace("/auth.html");

const $ = (id) => document.getElementById(id);
const state = { docs: [], resumeId: null, jdId: null };

function show(el, message) { el.textContent = message; el.hidden = false; }
function hide(el) { el.hidden = true; }

function renderList(type, listEl, key, emptyText, errorEl) {
  listEl.replaceChildren();
  const items = state.docs.filter((d) => d.doc_type === type);
  if (!items.length) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = emptyText;
    listEl.append(p);
    return;
  }
  if (!items.some((d) => d.id === state[key])) state[key] = items[0].id;
  for (const d of items) {
    const row = document.createElement("div");
    row.className = "row";

    const pick = document.createElement("div");
    pick.className = "pick grow";
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = type;
    radio.id = `doc-${d.id}`;
    radio.checked = state[key] === d.id;
    radio.addEventListener("change", () => { state[key] = d.id; updateCreate(); });
    const label = document.createElement("label");
    label.htmlFor = radio.id;
    label.textContent = `${d.title} (${d.char_count.toLocaleString()} characters)`;
    pick.append(radio, label);

    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn small";
    del.textContent = "Delete";
    del.addEventListener("click", async () => {
      hide(errorEl);
      try {
        await deleteDocument(d.id);
        if (state[key] === d.id) state[key] = null;
        await refresh();
      } catch (err) {
        show(errorEl, err.message);
      }
    });

    row.append(pick, del);
    listEl.append(row);
  }
}

function updateCreate() {
  $("create").disabled = !(state.resumeId && state.jdId);
}

async function refresh() {
  state.docs = await listDocuments();
  renderList("resume", $("resume-list"), "resumeId", "No resumes uploaded yet.", $("resume-error"));
  renderList("job_description", $("jd-list"), "jdId", "No job descriptions saved yet.", $("jd-error"));
  updateCreate();
}

$("resume-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  hide($("resume-error"));
  const file = $("resume-file").files[0];
  if (!file) return;
  $("resume-submit").disabled = true;
  try {
    const doc = await uploadResume(file);
    state.resumeId = doc.id;
    $("resume-form").reset();
    await refresh();
  } catch (err) {
    show($("resume-error"), err.message);
  } finally {
    $("resume-submit").disabled = false;
  }
});

$("jd-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  hide($("jd-error"));
  $("jd-submit").disabled = true;
  try {
    const doc = await createJobDescription($("jd-title").value.trim(), $("jd-text").value);
    state.jdId = doc.id;
    $("jd-form").reset();
    await refresh();
  } catch (err) {
    show($("jd-error"), err.message);
  } finally {
    $("jd-submit").disabled = false;
  }
});

$("create").addEventListener("click", async () => {
  hide($("create-error"));
  $("create").disabled = true;
  try {
    await createInterview({
      resume_document_id: state.resumeId,
      jd_document_id: state.jdId,
      settings: {
        role_title: $("role").value.trim(),
        duration_minutes: Number($("duration").value),
        difficulty: $("difficulty").value,
        interview_type: $("itype").value,
        language: "en",
      },
    });
    location.assign("/dashboard.html");
  } catch (err) {
    show($("create-error"), err.message);
    updateCreate();
  }
});

await refresh();