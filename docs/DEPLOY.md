# Deploying VAYU-SARTHI for a public link

The whole prototype (engine + UI) is one Docker container serving one port. Each browser tab gets its own
session, so judges and teammates opening the link at the same time do not see each other's changes.
Sessions live in memory: a restart or a sleeping instance starts everyone fresh.

| Host | Cost | Hardware | Plan / retask time | Notes |
|---|---|---|---|---|
| **Render** (recommended free) | Free | 0.1 CPU, 512 MB | ~30–40 s | Sleeps after 15 min idle; the next visit wakes it in about a minute |
| Hugging Face Space (Docker) | Needs HF PRO (paid) | 2 vCPU, 16 GB | ~10 s | Fastest; files in `deploy/huggingface/` |
| Your laptop | Free | your CPU | ~10 s | `docker build -t vayu-sarthi . && docker run --rm -p 7860:7860 vayu-sarthi` |

On a fractional CPU the engine notices the container's CPU quota. It uses one solver worker and gives each
solve 2.5× the time, runs the three courses of action one after another, and keeps the greedy plan whenever
the solver could not beat it in time. The UI tells the viewer it is on a free server.

Measured in a container limited like Render's free instance (0.1 CPU, 512 MB; seed 7):

| Scenario | Plan time | Fulfilment on 0.1 CPU | On a laptop |
|---|---|---|---|
| Western front | 38 s | 95.3% | 95.3% |
| Flood relief | 34 s | 91.0% | 100% |
| Earthquake relief | 29 s | 83.5% (greedy plan kept) | 91.5% |

Memory stays around 150 MB. The numbers in the deck come from the laptop benchmark (docs/PLAN.md §8).

## Render (free)

1. **Get the code onto `main`.** Render builds the `main` branch. Merge the pull request first.
2. Sign up at [render.com](https://render.com) with your GitHub account.
3. **New → Blueprint**, choose the `Air-power-Dynamic` repository, then **Apply**. Render reads `render.yaml`:
   a free Docker web service called `vayu-sarthi`, with health check `/api/health`.
   - Without a Blueprint: **New → Web Service → Public Git repository** →
     `https://github.com/DeepakSinghhh/Air-power-Dynamic` → Language **Docker** → Instance type **Free**.
4. The first build takes about 5–10 minutes (build logs are on the service page). When it says **Live**, the
   app is at `https://vayu-sarthi.onrender.com` (Render adds a suffix if the name is taken; the dashboard
   shows the exact URL).
5. Every new commit on `main` redeploys automatically.

**Before an evaluation:** open the link a few minutes early so the instance is awake. The first plan of a
session takes about 30–40 seconds on the free CPU. Optionally, a free uptime monitor (for example
UptimeRobot) pinging `https://<your-app>.onrender.com/api/health` every 10 minutes keeps it awake during
the evaluation window. One always-on service fits Render's 750 free hours a month.

## Hugging Face Space (faster, needs a paid plan)

Hugging Face now requires a PRO account to create Docker Spaces; only static Spaces are free.

1. [Create a Space](https://huggingface.co/new-space): SDK **Docker**, template **Blank**, hardware
   **CPU basic**, visibility **Public**.
2. In the Space's **Files** tab, add `Dockerfile` with the contents of `deploy/huggingface/Dockerfile`, and
   replace `README.md` with `deploy/huggingface/README.md` (keep its header block).
3. The Space builds from the GitHub repo's `main` branch. The app is at
   `https://<hf-username>-<space-name>.hf.space` (full screen; use this one in the deck).
4. After new commits reach `main`: **Settings → Factory rebuild**.

## Put the link in the deck

```bash
python -I tools/build_sih_deck.py --live https://vayu-sarthi.onrender.com
soffice --headless --convert-to pdf --outdir docs docs/NexaBuild_VAYU-SARTHI_SIH2026.pptx
```

The link appears on the title slide and next to the source-code link on the references slide.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | 7860 | Port to serve on (Render sets it) |
| `SARTHI_SOLVER_WORKERS` | 8, or 1 below 2 CPUs | CP-SAT search workers |
| `SARTHI_TIME_SCALE` | 1, or up to 3 below 1 CPU | Multiplies every solver time limit |
| `SARTHI_MAX_SOLVE_S` | 20 | Longest solve a client may ask for (before scaling) |
| `SARTHI_MAX_SESSIONS` | 40 | Sessions kept in memory; the least recently used is dropped |
| `SARTHI_SESSION_IDLE_H` | 6 | Idle sessions expire after this many hours |
| `SARTHI_LLM_URL` | unset | Optional local model for the copilot; the parser works without it |
