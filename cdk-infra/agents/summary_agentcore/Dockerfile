FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir strands-agents boto3 bedrock-agentcore
COPY summary_agent.py .

CMD ["python", "summary_agent.py"]
