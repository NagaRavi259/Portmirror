FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends iproute2 iputils-arping && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir psycopg2-binary==2.9.10 websockets==13.1
COPY traffic_gen.py /traffic_gen.py
CMD ["python3", "-u", "/traffic_gen.py"]
