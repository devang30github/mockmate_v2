import { getMe, logout } from "./auth.js";

const me = await getMe();
if (!me) {
  location.replace("/auth.html");
} else {
  document.getElementById("user-email").textContent = me.email;
  document.getElementById("app").hidden = false;
  document.getElementById("logout").addEventListener("click", async () => {
    try { await logout(); } finally { location.assign("/"); }
  });
}