FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system monitor && adduser --system --ingroup monitor monitor

COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt

COPY app ./app

RUN mkdir /data && chown monitor:monitor /data

USER monitor

VOLUME ["/data"]

CMD ["python", "-m", "app.main"]
