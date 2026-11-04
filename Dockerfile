FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends iputils-ping tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY netwatch ./netwatch

ENV PYTHONUNBUFFERED=1 \
    NETWATCH_DB=/data/netwatch.db \
    NETWATCH_PORT=8080

VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ['NETWATCH_PORT'], timeout=4)"

CMD ["python", "-m", "netwatch"]
