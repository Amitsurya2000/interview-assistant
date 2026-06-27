FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

ENV SERVER_HOST=0.0.0.0
ENV SERVER_PORT=8123
ENV WHISPER_MODEL=base
ENV WHISPER_COMPUTE_TYPE=int8
ENV OLLAMA_BASE_URL=http://host.docker.internal:11434
ENV OLLAMA_MODEL=llama3.2
ENV AUTH_TOKEN=""
ENV DEBUG=1

EXPOSE 8123

CMD ["python", "server.py"]
