import { api } from "./api.js";

export async function getMe() {
  try {
    return await api("/auth/me");
  } catch {
    return null;
  }
}

export const login = (email, password) =>
  api("/auth/login", { method: "POST", body: { email, password } });

export const register = (email, password) =>
  api("/auth/register", { method: "POST", body: { email, password } });

export const logout = () => api("/auth/logout", { method: "POST" });