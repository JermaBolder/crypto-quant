# Runtime image for the streaming app (producer + consumer share one image).
# Slim on purpose: the live pipeline only needs websockets + redis.
FROM python:3.12-slim

# unbuffered stdout/stderr so `docker logs` shows output live (Python
# block-buffers stdout when it isn't a TTY — the classic container gotcha)
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# deps first, so code edits don't bust the dependency layer cache
COPY requirements-app.txt .
RUN pip install --no-cache-dir -r requirements-app.txt

# only the modules the runtime actually imports
COPY config.py sources.py qdb_sink.py producer.py consumer_questdb.py consumer_metrics.py ./

# default; each compose service overrides this
CMD ["python", "producer.py"]
