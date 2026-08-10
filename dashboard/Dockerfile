# Build from the repo root: docker build -f dashboard/Dockerfile -t weather-dashboard .
#
# Needs mcp_server/query_log.py alongside dashboard/ (app.py imports it via a
# relative sys.path insert), so the build context is the repo root, not
# dashboard/ alone.
FROM python:3.11-slim

WORKDIR /app

COPY dashboard/requirements.txt ./dashboard/requirements.txt
RUN pip install --no-cache-dir -r dashboard/requirements.txt

COPY mcp_server/query_log.py ./mcp_server/query_log.py
COPY dashboard/ ./dashboard/

WORKDIR /app/dashboard
ENV PORT=8001
EXPOSE 8001

CMD ["python", "app.py"]
