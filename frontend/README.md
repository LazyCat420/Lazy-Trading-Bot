# Frontend Dashboard

The Lazy Trading Bot dashboard SPA. Served via `live-server` on port **3000** during development.

## How to Run

```bash
npm run dev
```

This starts `live-server` on `:3000` and proxies all `/api/*` and `/ws/*` requests to the backend at `http://localhost:4000`.

## Files

- `index.html` — Main dashboard page (CDN React + Tailwind + Babel)
- `static/terminal_app.js` — Full React SPA (all components/pages)
- `static/style.css` — Custom CSS styles
- `static/retro_sfx.js` — Sound effects module

## Requirements

The `tradingbackend` server must be running on `:4000` for API calls to work.
