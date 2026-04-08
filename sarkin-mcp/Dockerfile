FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sarkin_mcp/ ./sarkin_mcp/
CMD ["python", "-m", "sarkin_mcp"]
