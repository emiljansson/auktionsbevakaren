import axios from "axios";
import { getDeviceId } from "./push";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// Attach the per-device identity to every request (no login needed).
axios.interceptors.request.use((config) => {
  if (config.url && config.url.startsWith(API)) {
    config.headers = config.headers || {};
    config.headers["X-Device-Id"] = getDeviceId();
  }
  return config;
});

export const api = {
  listAuctions: (source) =>
    axios.get(`${API}/auctions`, { params: source ? { source } : {} }).then((r) => r.data),
  getAuction: (id) => axios.get(`${API}/auctions/${id}`).then((r) => r.data),
  createAuction: (payload) => axios.post(`${API}/auctions`, payload).then((r) => r.data),
  preview: (payload) => axios.post(`${API}/scrape/preview`, payload).then((r) => r.data),
  analyze: (id) => axios.post(`${API}/auctions/${id}/analyze`).then((r) => r.data),
  refresh: (id) => axios.post(`${API}/auctions/${id}/refresh`).then((r) => r.data),
  remove: (id) => axios.delete(`${API}/auctions/${id}`).then((r) => r.data),
  stats: () => axios.get(`${API}/stats`).then((r) => r.data),
  liveStatus: () => axios.get(`${API}/live/status`).then((r) => r.data),
  liveAuctions: (params) => axios.get(`${API}/live/auctions`, { params }).then((r) => r.data),
  liveSync: () => axios.post(`${API}/live/sync`).then((r) => r.data),
  liveWatch: (url) => axios.post(`${API}/live/watch`, { url }).then((r) => r.data),
  liveAnalyze: (url) => axios.post(`${API}/live/analyze`, { url }).then((r) => r.data),
  deleteSoldItem: (url) => axios.delete(`${API}/sold-items`, { data: { url } }).then((r) => r.data),
  pushStatus: () => axios.get(`${API}/push/status`).then((r) => r.data),
  kwList: () => axios.get(`${API}/keyword-watches`).then((r) => r.data),
  kwCreate: (keyword) => axios.post(`${API}/keyword-watches`, { keyword }).then((r) => r.data),
  kwGet: (id) => axios.get(`${API}/keyword-watches/${id}`).then((r) => r.data),
  kwDelete: (id) => axios.delete(`${API}/keyword-watches/${id}`).then((r) => r.data),
};

export default api;
