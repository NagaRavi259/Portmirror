# Container B - threaded HTTPS server, self-signed cert baked in at build.
FROM python:3.12-alpine
RUN apk add --no-cache openssl \
 && mkdir /certs \
 && openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
      -subj "/CN=container-b" -keyout /certs/key.pem -out /certs/cert.pem
COPY https_server.py /https_server.py
CMD ["python3", "-u", "/https_server.py"]
