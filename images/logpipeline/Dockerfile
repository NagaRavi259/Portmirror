FROM python:3.12-slim
RUN pip install --no-cache-dir psycopg2-binary==2.9.10 Faker==30.8.2 websockets==13.1
WORKDIR /app
COPY log_generator.py ws_db_log_stream.py ./
ENV PYTHONUNBUFFERED=1
CMD ["python3", "ws_db_log_stream.py"]
