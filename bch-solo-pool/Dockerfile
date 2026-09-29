FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir pyzmq==27.0.2

COPY app /app/app

RUN useradd --create-home --uid 10001 pooluser \
    && mkdir -p /data \
    && chown -R pooluser:pooluser /app /data

USER pooluser

EXPOSE 3334 8080

CMD ["python", "-m", "app.main"]
