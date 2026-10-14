import { getMe } from "./auth.js";

const me = await getMe();
if (me) {
  const link = document.createElement("a");
  link.href = "/dashboard.html";
  link.className = "btn primary";
  link.textContent = "Go to dashboard";
  document.getElementById("nav").replaceChildren(link);
  const cta = document.getElementById("cta");
  if (cta) {
    cta.href = "/dashboard.html";
    cta.textContent = "Go to dashboard";
  }
}