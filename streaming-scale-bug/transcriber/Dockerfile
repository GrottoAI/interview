FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir websockets
COPY server.py .
CMD ["python", "server.py"]
