import os
import json
import hashlib

from strands import Agent
from strands.models.bedrock import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp

MOUNT = "/mnt/s3files"
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

model = BedrockModel(model_id=MODEL_ID, region_name=REGION)
app = BedrockAgentCoreApp()

agent = Agent(
    model=model,
    system_prompt="""
You are an executive summary agent. Read all compliance reports and produce a
concise executive summary. Return JSON with: overall_status, total_documents,
high_risk_count, key_findings, recommendations, executive_narrative.
""",
)


@app.entrypoint
def handle_request(payload):
    reports_dir = f"{MOUNT}/reports"
    if not os.path.isdir(reports_dir):
        return {"status": "no_reports"}

    report_files = sorted(f for f in os.listdir(reports_dir) if f.endswith(".json"))
    if not report_files:
        return {"status": "no_reports"}

    batch_id = hashlib.md5("".join(report_files).encode()).hexdigest()[:8]
    output_path = f"{MOUNT}/summaries/summary-{batch_id}.json"
    if os.path.exists(output_path):
        return {"batch_id": batch_id, "status": "already_summarized"}

    reports = []
    for report_file in report_files:
        with open(f"{reports_dir}/{report_file}") as f:
            reports.append(json.load(f))

    result = agent(f"Generate executive summary: {json.dumps(reports)}")
    envelope = {
        "batch_id": batch_id,
        "stage": "summary",
        "content": json.loads(result.message["content"][0]["text"]),
    }
    with open(output_path, "w") as f:
        json.dump(envelope, f, indent=2)
    return {"batch_id": batch_id, "status": "summarized"}


if __name__ == "__main__":
    app.run()
