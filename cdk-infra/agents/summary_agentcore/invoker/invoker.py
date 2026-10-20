"""
Triggered by EventBridge every minute. Invokes the AgentCore Runtime
(EventBridge has no native AgentCore target, so a thin Lambda bridges it).
"""
import os
import json
import boto3

AGENT_RUNTIME_ID = os.environ["AGENT_RUNTIME_ID"]
REGION = os.environ.get("AWS_REGION", "us-east-1")

client = boto3.client("bedrock-agentcore", region_name=REGION)


def handler(event, context):
    response = client.invoke_agent_runtime(
        agentRuntimeId=AGENT_RUNTIME_ID,
        payload=json.dumps({"trigger": "scheduled"}).encode("utf-8"),
    )
    body = response["response"].read() if hasattr(response.get("response"), "read") else response.get("response")
    print(f"invoked agent runtime, response={body}", flush=True)
    return {"status": "invoked"}
