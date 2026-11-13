---
title: AI Data Analyst API
emoji: 📊
colorFrom: blue
colorTo: indigo
sdk: gradio
python_version: "3.12"
app_file: app.py
pinned: false
---

Backend API for the AI Data Analyst Agent (FastAPI + Celery + Redis; the
web app is deployed separately). `app.py` starts the services; it does not
use Gradio. Health check: `/health/ready`.
