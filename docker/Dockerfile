FROM python:3.12-slim

WORKDIR /app

COPY *.py .
COPY dashboard ./dashboard

RUN useradd --system --uid 1000 --no-create-home --shell /usr/sbin/nologin walita && \
    mkdir -p /app/srv && \
    chown -R 1000:1000 /app

USER 1000:1000

CMD ["/bin/sh", "-c", "/usr/local/bin/python3 walita.py --no-open -o /app/srv/index.html && exec /usr/local/bin/python3 -m http.server 8080 --directory /app/srv"]
