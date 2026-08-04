FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir flask==3.0.3
ENV PYTHONUNBUFFERED=1
EXPOSE 8787
CMD ["python", "/app/app.py"]
