FROM python:3.12-slim
WORKDIR /srv
COPY mock_server.py .
EXPOSE 8099
CMD ["python", "mock_server.py", "--host", "0.0.0.0", "--port", "8099", "--out", "/records/requests.jsonl"]
