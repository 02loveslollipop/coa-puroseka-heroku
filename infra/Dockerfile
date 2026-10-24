FROM python:3.15-slim
ARG COA_SERVICE=chat
ENV COA_SERVICE=$COA_SERVICE
WORKDIR /app
COPY shared/ shared/
COPY services/${COA_SERVICE}/ services/${COA_SERVICE}/
RUN pip install --no-cache-dir -r services/${COA_SERVICE}/requirements.txt
COPY infra/heroku/start.py infra/heroku/start.py
RUN useradd --uid 10001 --create-home appuser && chown -R appuser:appuser /app
USER appuser
CMD ["python", "infra/heroku/start.py"]
