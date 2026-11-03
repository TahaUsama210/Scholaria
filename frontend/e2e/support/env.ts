// Ports are fixed so the e2e stack never collides with a dev stack on 8000/5173.
export const API_PORT = 8100;
export const WEB_PORT = 5174;
export const API_URL = `http://127.0.0.1:${API_PORT}`;
export const WEB_URL = `http://127.0.0.1:${WEB_PORT}`;
