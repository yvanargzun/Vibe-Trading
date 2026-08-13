FROM python:3.12-slim
WORKDIR /app
COPY app.py /app/app.py
ENV PYTHONUNBUFFERED=1
EXPOSE 4000
CMD ["python", "-u", "/app/app.py"]
