#!/bin/zsh
# Start the Amazon Review Intelligence dashboard.
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  uv venv -p 3.12 .venv && uv pip install -p .venv -r requirements.txt
fi
[ -f .env ] || cp .env.example .env
.venv/bin/streamlit run app.py
