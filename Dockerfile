# Debian stable (bookworm) + Python 3.12.
# Calibre uvnitř NENÍ potřeba: převod formátů běží na Content Serveru
# (endpointy /conversion/*), tenhle kontejner s ním jen mluví přes HTTP.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8765 \
    MCP_PATH=/mcp

WORKDIR /app

# zavislosti zvlast, at se vrstva cachuje
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY calibre_mcp.py .

# bezet jako neprivilegovany uzivatel
RUN useradd --create-home --uid 10001 mcp \
 && chown -R mcp:mcp /app
USER mcp

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import socket,os,sys; s=socket.create_connection(('127.0.0.1', int(os.environ.get('MCP_PORT','8765'))), 3); s.close()" || exit 1

CMD ["python", "calibre_mcp.py"]
