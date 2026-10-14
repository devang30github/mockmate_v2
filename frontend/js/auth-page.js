import { getMe, login, register } from "./auth.js";

if (await getMe()) location.replace("/dashboard.html");

const params = new URLSearchParams(location.search);
let mode = params.get("mode") === "register" ? "register" : "login";

const form = document.getElementById("auth-form");
const title = document.getElementById("title");
const submit = document.getElementById("submit");
const toggleText = document.getElementById("toggle-text");
const toggleLink = document.getElementById("toggle-link");
const errorBox = document.getElementById("error");
const passwordInput = document.getElementById("password");
const hint = document.getElementById("password-hint");

function render() {
  const isRegister = mode === "register";
  title.textContent = isRegister ? "Create your account" : "Welcome back";
  submit.textContent = isRegister ? "Create account" : "Log in";
  toggleText.textContent = isRegister ? "Already have an account?" : "New to MockMate?";
  toggleLink.textContent = isRegister ? "Log in" : "Create an account";
  passwordInput.autocomplete = isRegister ? "new-password" : "current-password";
  hint.hidden = !isRegister;
  errorBox.hidden = true;
}

toggleLink.addEventListener("click", (e) => {
  e.preventDefault();
  mode = mode === "register" ? "login" : "register";
  render();
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errorBox.hidden = true;
  submit.disabled = true;
  const email = form.elements.email.value.trim();
  const password = form.elements.password.value;
  try {
    await (mode === "register" ? register : login)(email, password);
    location.assign("/dashboard.html");
  } catch (err) {
    errorBox.textContent = err.message;
    errorBox.hidden = false;
    submit.disabled = false;
  }
});

render();