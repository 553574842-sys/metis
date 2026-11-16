FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/backend
COPY backend/requirements.lock.txt ./requirements.lock.txt
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY backend/ ./
COPY fixtures/ /srv/fixtures/
COPY scripts/manage.py /srv/scripts/manage.py
COPY .env.example /srv/.env.example
RUN useradd --create-home --uid 10001 atlas && mkdir -p /data /backup && chown -R atlas:atlas /data /backup /srv
USER atlas
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
