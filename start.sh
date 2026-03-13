playwright install chromium
gunicorn app:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT
