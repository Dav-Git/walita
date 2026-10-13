FROM python:3.12-slim

WORKDIR /app

COPY *.py .
COPY dashboard ./dashboard
COPY docker/start.sh /start.sh
RUN chmod +x /start.sh

RUN apt update && \
    apt install -y cron && \
    rm -rf /var/lib/apt/lists/*

CMD [ "/start.sh"]
