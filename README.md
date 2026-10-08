# VAYU-SARTHI: Dynamic Air Operations & Resource Optimisation

SIH 2026 · Problem Statement 26250 · MoD / Defence Services Staff College

An explainable, offline-capable decision-support system. It fuses air-operations data into one
picture, predicts disruptions and generates optimal, *minimally disruptive* air tasking and
retasking options in seconds, for a human commander to approve.

- **Plan & roadmap:** [`docs/PLAN.md`](docs/PLAN.md)
- **Core engine:** [`engine/`](engine/) (CP-SAT allocation, threat-aware routing, minimal-disruption retasking)
- **UI:** [`frontend/`](frontend/) (offline COP map + synchronisation matrix + human-in-the-loop retask review +
  fog forecast + courses of action + robustness + readiness board + flood-relief scenario + copilot)
- **Idea deck:** [`docs/VAYU-SARTHI_SIH2026_idea.pptx`](docs/VAYU-SARTHI_SIH2026_idea.pptx) (rebuild with
  `python -I tools/build_deck.py` after `npm run e2e`; PDF preview alongside)
- **Demo video (backup):** [`docs/media/VAYU-SARTHI_demo.mp4`](docs/media/VAYU-SARTHI_demo.mp4), a captioned 3-minute
  walkthrough recorded against the live engine (re-record with `npm run demo-video` in `frontend/`)

```bash
./run.sh          # Linux/macOS: builds the UI once, then serves everything at http://127.0.0.1:8000
run.bat           # Windows: double-click (or: powershell -ExecutionPolicy Bypass -File run.ps1)
```

Needs Python 3.11+ and Node.js 20+. After the first install everything runs offline on one laptop. It is laid out
for 1280×720 projectors and up.

All scenario data is notional.
