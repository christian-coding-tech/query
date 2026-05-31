# TODO - Queue System Improvements

- [x] Clarify UI purpose text (base.html + index.html + dashboard.html)
- [x] Improve UI styling (static/style.css)
- [x] Add AI assistance:
  - [x] Add `/ai/suggest` endpoint in queue_web.py
  - [x] Implement fallback suggestions when `OPENAI_API_KEY` is not set
  - [x] Add AI widget UI on dashboard
- [ ] Update documentation (README_SYSTEM.md and README.md)
- [ ] Install AI dependency (optional): `pip install openai` (only needed for OpenAI mode)
- [ ] Test:
  - [ ] start `python queue_web.py` and verify queue add/serve/clear
  - [ ] verify AI widget works (fallback without OPENAI_API_KEY)
  - [ ] verify OpenAI mode when `OPENAI_API_KEY` is set

