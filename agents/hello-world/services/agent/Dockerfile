# Built with the agent folder (agents/hello-world) as the context.
FROM python:3.12-slim
# Standard library only: nothing to install.
COPY services/agent/app.py /app/app.py
USER 65534
EXPOSE 8080
ENV PYTHONUNBUFFERED=1
CMD ["python", "/app/app.py"]
