FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/app/data
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -c constraints.txt -r requirements.txt
COPY app ./app
COPY migrations ./migrations
COPY curricula ./curricula
COPY seed ./seed
COPY content ./content
COPY config/*.example.toml ./config/
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
