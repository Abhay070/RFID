FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV GHOST_DB=/data/ghost.db GHOST_PROFILE=/data/profile.json
# Hosts (Render, Fly, Railway...) inject $PORT. Fall back to 8000 locally.
EXPOSE 8000
CMD ["sh", "-c", "mkdir -p /data && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'"]
