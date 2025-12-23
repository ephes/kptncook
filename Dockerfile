FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KPTNCOOK_HOME=/data

RUN pip install --no-cache-dir kptncook

VOLUME ["/data"]

ENTRYPOINT ["kptncook"]
