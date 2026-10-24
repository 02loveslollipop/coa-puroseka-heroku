FROM python:3.15-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ app/
COPY services/ services/
COPY shared/ shared/
COPY infra/heroku/start.py start.py
RUN useradd --uid 10001 --create-home appuser && chown -R appuser:appuser /app
USER appuser
CMD ["python", "start.py"]
