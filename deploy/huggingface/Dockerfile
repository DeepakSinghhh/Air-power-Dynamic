# Hugging Face Space for VAYU-SARTHI (free CPU hardware). Builds the app straight from the public
# GitHub repo, so the Space needs only this file and README.md. After new commits are merged into the
# branch below, use Settings -> "Factory rebuild" on the Space to pick them up.

FROM node:22-slim AS src
ARG REPO=https://github.com/DeepakSinghhh/Air-power-Dynamic.git
ARG REF=main
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "$REF" "$REPO" /src
WORKDIR /src/frontend
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
RUN npm ci --no-audit --no-fund && npm run build

FROM python:3.12-slim
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1 PORT=7860
WORKDIR /home/user/app
COPY --chown=user --from=src /src/engine/pyproject.toml /src/engine/README.md engine/
COPY --chown=user --from=src /src/engine/sarthi engine/sarthi
RUN pip install --no-cache-dir --user -e "./engine[api]"
COPY --chown=user --from=src /src/frontend/dist frontend/dist
WORKDIR /home/user/app/engine
EXPOSE 7860
CMD ["sh", "-c", "exec uvicorn sarthi.api:app --host 0.0.0.0 --port ${PORT}"]
