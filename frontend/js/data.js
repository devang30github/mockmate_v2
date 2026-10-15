import { api } from "./api.js";

export const listDocuments = () => api("/documents");

export function uploadResume(file) {
  const form = new FormData();
  form.append("file", file);
  return api("/documents/resume", { method: "POST", form });
}

export const createJobDescription = (title, text) =>
  api("/documents/job-description", {
    method: "POST",
    body: { title: title || null, text },
  });

export const deleteDocument = (id) => api(`/documents/${id}`, { method: "DELETE" });

export const createInterview = (payload) =>
  api("/interviews", { method: "POST", body: payload });

export const listInterviews = () => api("/interviews");

export const deleteInterview = (id) => api(`/interviews/${id}`, { method: "DELETE" });

export const preparePlan = (id) => api(`/interviews/${id}/plan`, { method: "POST" });

export const getPlan = (id) => api(`/interviews/${id}/plan`);