FROM python:3.11-slim

WORKDIR /app

# サブディレクトリをコピー
COPY threads-auto/threads-auto/ /app/

RUN pip install --no-cache-dir -r requirements.txt

CMD ["sh","-c","uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
