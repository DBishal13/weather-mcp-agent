# Build from the repo root: docker build -f mcp_server/Dockerfile -t weather-mcp-server .
FROM python:3.11-slim

WORKDIR /app

COPY mcp_server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp_server/ ./

ENV PORT=8000
EXPOSE 8000

CMD ["python", "weather_mcp_server.py"]
