FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir flask==3.0.3
COPY app.py data.py chat_history.py strategy_briefs.json /app/
COPY templates /app/templates
COPY static /app/static
ENV PYTHONUNBUFFERED=1
EXPOSE 8787
CMD ["python", "/app/app.py"]
