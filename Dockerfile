FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements_v2.txt .
RUN pip install --no-cache-dir -r requirements_v2.txt
COPY app ./app
ENV PORT=8080
ENV HF_HOME=/models/huggingface
ENV WHISPER_MODEL=base
CMD ["sh", "-c", "uvicorn app.main_v2:app --host 0.0.0.0 --port ${PORT:-8080}"]
