FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV DATABASE_URL=sqlite:////tmp/clean-python.db
EXPOSE 8000
# Access logs are disabled: uvicorn's access log records client IP addresses, which are
# personal data. Keeping them off is what makes this fixture genuinely clean.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
