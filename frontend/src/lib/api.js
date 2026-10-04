import axios from "axios";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

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
};

export default api;
