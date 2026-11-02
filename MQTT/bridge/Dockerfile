FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY furnace_bridge.py ./

# Runs as a non-root user; the bridge keeps no state on disk.
RUN useradd --system --uid 10001 bridge
USER bridge

ENTRYPOINT ["python3", "furnace_bridge.py"]
