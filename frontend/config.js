// Switch between local dev and production
const _isLocal = location.hostname === "localhost" || location.hostname === "127.0.0.1";
window.API_BASE = _isLocal
  ? "http://localhost:8000"
  : "https://senator-stock-trading.onrender.com";
