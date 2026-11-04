# Hub + MCP image for Linux (k3s on jw). The same image runs either:
#   pa-hub serve                         (HTTPS API on :7700)
#   pa-mcp --http --host <ip> --port 7702 (MCP over streamable HTTP)
# State lives under $LOCALAPPDATA/personal-automations (mount a volume at /data).
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY core core
COPY agent agent
COPY hub hub
COPY mcp mcp
RUN uv sync --frozen --no-dev --all-packages --no-editable \
    && rm -rf /root/.cache
ENV PATH=/app/.venv/bin:$PATH \
    LOCALAPPDATA=/data \
    PYTHONUNBUFFERED=1
CMD ["pa-hub", "serve"]
