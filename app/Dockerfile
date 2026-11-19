# Used by `wam verify` only: the installed app runs the same source from runtipi/docker-compose.yml (package: source).
FROM python:3.14-alpine
WORKDIR /app
COPY app /app/app
COPY engines /app/engines
COPY web /app/web
COPY openapi.json /app/openapi.json
RUN mkdir /data && chown 1000:1000 /data
USER 1000:1000
ENV DATA_DIR=/data MAPS_WORKER=0
EXPOSE 8799
CMD ["python", "/app/app/server.py"]
