# Plan: Setup All 4 Trading Bot Repos

## Goal
Set up all 4 repos (Lazy-Trading-Bot, prism, retina, tradingbackend) with dependencies installed, config files created, and a VS Code tasks.json to run them all simultaneously.

## Port Map
| Repo | Port | 
|------|------|
| Lazy-Trading-Bot (Python backend) | 8000 |
| Lazy-Trading-Bot (Frontend) | 3000 |
| tradingbackend | 4000 |
| prism | 7777 |
| retina | 3333 |

## Steps
1. `npm install` in all 4 projects
2. `python -m venv venv` + `pip install -r requirements.txt` for Lazy-Trading-Bot
3. Create `prism/secrets.js` from example (port → 7777)
4. Create `retina/secrets.js` from example (port 3333)
5. Create `Lazy-Trading-Bot/.env` from example
6. Fix retina dev script for Windows (remove shell syntax)
7. Create `.vscode/tasks.json` with compound "Run All" task

## Status: COMPLETE
