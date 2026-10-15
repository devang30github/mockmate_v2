import { getMe, logout } from "./auth.js";
import { deleteInterview, getPlan, listInterviews, preparePlan } from "./data.js";

const $ = (id) => document.getElementById(id);
const STATE_LABELS = { DRAFT: "Draft", READY: "Ready" };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function showError(message) {
  const box = $("dash-error");
  box.textContent = message;
  box.hidden = false;
}

function hideError() {
  $("dash-error").hidden = true;
}

// textContent only: plan text comes from a model reading resume content.
function renderPlan(panel, data) {
  const { plan } = data;
  panel.replaceChildren(
    el(
      "p",
      "muted",
      `About ${plan.budget.total_questions} questions in ${plan.budget.usable_minutes} minutes, plus intro and wrap-up.`,
    ),
    el("p", null, `Opening: ${plan.opening_question}`),
  );
  for (const s of plan.sections) {
    const block = el("div", "plan-section");
    block.append(
      el("strong", null, s.competency),
      el(
        "span",
        "muted",
        ` · ${s.minutes} min · about ${s.target_questions} questions · ${s.category.replaceAll("_", " ")}`,
      ),
    );
    if (s.rationale) block.append(el("div", "muted", s.rationale));
    const list = el("ul");
    for (const q of s.seed_questions) list.append(el("li", null, q));
    block.append(list);
    panel.append(block);
  }
}

async function renderInterviews() {
  const box = $("interviews");
  const items = await listInterviews();
  box.replaceChildren();
  if (!items.length) {
    box.append(el("div", "empty muted", "No interviews yet."));
    return;
  }
  for (const i of items) {
    const wrap = el("div", "interview-item");
    const row = el("div", "row");
    const panel = el("div", "plan");
    panel.hidden = true;

    const info = el("div", "grow");
    info.append(
      el("strong", null, i.settings.role_title),
      el(
        "div",
        "muted",
        `${i.settings.duration_minutes} min · ${i.settings.difficulty} · ${i.settings.interview_type}` +
          ` · ${i.resume_title} · ${i.jd_title}`,
      ),
    );

    const badge = el("span", "badge", STATE_LABELS[i.state] || i.state);
    const actions = el("div", "actions");

    if (i.state === "DRAFT") {
      const prepare = el("button", "btn small primary", "Prepare");
      prepare.type = "button";
      prepare.addEventListener("click", async () => {
        hideError();
        prepare.disabled = true;
        prepare.textContent = "Preparing… this can take a minute";
        try {
          await preparePlan(i.id);
          await renderInterviews();
        } catch (err) {
          showError(err.message);
          prepare.disabled = false;
          prepare.textContent = "Prepare";
        }
      });
      actions.append(prepare);
    } else if (i.state === "READY") {
      const view = el("button", "btn small", "View plan");
      view.type = "button";
      view.addEventListener("click", async () => {
        hideError();
        if (!panel.hidden) {
          panel.hidden = true;
          view.textContent = "View plan";
          return;
        }
        try {
          if (!panel.dataset.loaded) {
            renderPlan(panel, await getPlan(i.id));
            panel.dataset.loaded = "1";
          }
          panel.hidden = false;
          view.textContent = "Hide plan";
        } catch (err) {
          showError(err.message);
        }
      });
      actions.append(view);
    }

    const del = el("button", "btn small", "Delete");
    del.type = "button";
    del.addEventListener("click", async () => {
      hideError();
      try {
        await deleteInterview(i.id);
        await renderInterviews();
      } catch (err) {
        showError(err.message);
      }
    });
    actions.append(del);

    row.append(info, badge, actions);
    wrap.append(row, panel);
    box.append(wrap);
  }
}

const me = await getMe();
if (!me) {
  location.replace("/auth.html");
} else {
  $("user-email").textContent = me.email;
  $("app").hidden = false;
  $("logout").addEventListener("click", async () => {
    try { await logout(); } finally { location.assign("/"); }
  });
  await renderInterviews();
}