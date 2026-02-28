FROM python:3.11-slim

WORKDIR /app

# 依存インストール
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Playwright（Threads用）
RUN playwright install --with-deps chromium

# リポジトリ全部コピー（main.py含む）
COPY . /app/

# main.pyを起動
CMD ["sh","-c","python -m uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
