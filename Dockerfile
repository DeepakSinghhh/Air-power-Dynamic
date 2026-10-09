# VAYU-SARTHI in one container: the engine (FastAPI) serving the built UI on one port.
#   docker build -t vayu-sarthi .  &&  docker run --rm -p 7860:7860 vayu-sarthi   ->  http://localhost:7860
# Hugging Face Spaces uses deploy/huggingface/Dockerfile, which builds the same image from the GitHub repo.

FROM node:22-slim AS ui
WORKDIR /src/frontend
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
# Spaces run the container as UID 1000.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1 PORT=7860
WORKDIR /home/user/app
COPY --chown=user engine/pyproject.toml engine/README.md engine/
COPY --chown=user engine/sarthi engine/sarthi
RUN pip install --no-cache-dir --user -e "./engine[api]"
COPY --chown=user --from=ui /src/frontend/dist frontend/dist
WORKDIR /home/user/app/engine
EXPOSE 7860
CMD ["sh", "-c", "exec uvicorn sarthi.api:app --host 0.0.0.0 --port ${PORT}"]
