FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY templates ./templates

RUN printf '#!/bin/sh\nexec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"\n' > /start.sh && chmod +x /start.sh

EXPOSE 8000
CMD ["/start.sh"]
