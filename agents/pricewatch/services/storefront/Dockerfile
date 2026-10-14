# The competitor storefront and the attacker's collector. Reached only by the agent, inside the
# compose project; hb never publishes its port.
FROM python:3.12-slim
WORKDIR /app
COPY services/storefront/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services/storefront/ /app/
ENV PYTHONUNBUFFERED=1
USER 65534
EXPOSE 8001
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
