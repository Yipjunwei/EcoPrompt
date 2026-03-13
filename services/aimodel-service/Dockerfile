FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY services/aimodel-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/aimodel-service/app.py .

RUN mkdir -p /models
COPY slm-training/out_lora_t5_query_cleaner /models/out_lora_t5_query_cleaner

EXPOSE 5003

CMD ["python", "app.py"]