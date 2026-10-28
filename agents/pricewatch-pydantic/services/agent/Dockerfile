# Built with the agent folder (agents/pricewatch-pydantic) as the context.
FROM python:3.12-slim
WORKDIR /app
COPY services/agent/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/agent/ /app/
ENV PRICEWATCH_DB=/tmp/pricewatch.db PYTHONUNBUFFERED=1 PYDANTIC_AI_NO_BANNER=1
USER 65534
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
