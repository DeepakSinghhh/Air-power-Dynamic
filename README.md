# VAYU-SARTHI: Dynamic Air Operations & Resource Optimisation

SIH 2026 · Problem Statement 26250 · MoD / Defence Services Staff College

An explainable, offline-capable decision-support system. It fuses air-operations data into one
picture, predicts disruptions and generates optimal, *minimally disruptive* air tasking and
retasking options in seconds, for a human commander to approve.

- **Plan & roadmap:** [`docs/PLAN.md`](docs/PLAN.md)
- **Core engine:** [`engine/`](engine/) (CP-SAT allocation, threat-aware routing, minimal-disruption retasking)
- **UI:** [`frontend/`](frontend/) (offline COP map + synchronisation matrix + human-in-the-loop retask review +
  fog forecast + courses of action + robustness + flood-relief scenario)
- **Idea deck:** [`docs/VAYU-SARTHI_SIH2026_idea.pptx`](docs/VAYU-SARTHI_SIH2026_idea.pptx) (rebuild with
  `python -I tools/build_deck.py` after `npm run e2e`); demo video: `npm run demo-video` in `frontend/`

```bash
./run.sh          # builds the UI once, then serves everything at http://127.0.0.1:8000
```

All scenario data is notional.
