# aws-s3-multi-agent — home lab (AWS CDK, Python)

Implements the 5-stage document-intelligence pipeline from the AWS blog post
*"Orchestrating multi-agent AI architectures with Amazon S3 Files"*:

```
customer-forms/ --(S3 event -> SQS)--> EC2 intake/ --> Lambda analyzed/
    --> EKS validated/ --> ECS Fargate reports/,documents/ --> AgentCore summaries/
```

Every stage mounts the same S3 Files file system at `/mnt/s3files` and hands
off through directories. Cost is not optimized here — this is a "turn on
everything the post describes" home lab, on purpose.

## 0. Before you start

- **S3 Files region availability** is limited — confirm it in your target
  region before deploying: `aws s3files list-file-systems --region us-east-1`
  (or check the AWS Regional Services List). This project defaults to
  `us-east-1`.
- **Docker Desktop / daemon running** — `aws_lambda_python_alpha.PythonFunction`
  and `DockerImageAsset` both build images locally at `cdk deploy` time.
- **Bedrock model access** — enable `us.anthropic.claude-sonnet-4-6` (or
  whichever cross-region inference profile you want) in the Bedrock console
  under Model access, in this account/region, *before* deploying, or every
  agent call will fail with an AccessDenied error.
- **This is genuinely a lot of infrastructure.** Read the "Deploy
  incrementally" section below rather than firing `cdk deploy --all` blind
  the first time.

## 1. Install prerequisites

```bash
# Node (for the CDK CLI) + the CDK CLI itself
npm install -g aws-cdk

# Python deps — this project already has a uv-managed venv/pyproject at the
# repo root for the agent code; the CDK app gets its own venv here:
cd cdk-infra
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Confirm your AWS CLI is configured (`aws sts get-caller-identity`) and that
`CDK_DEFAULT_ACCOUNT` / region resolve correctly, then bootstrap once per
account/region:

```bash
cdk bootstrap aws://ACCOUNT_ID/us-east-1
```

## 2. Deploy incrementally

Stacks are independent CDK constructs wired together in `app.py`, so you can
bring the pipeline up stage by stage and sanity-check each one:

```bash
cdk deploy S3MultiAgent-Network S3MultiAgent-Storage
# confirms VPC + S3 Files file system + mount targets + SQS queue come up

cdk deploy S3MultiAgent-Ec2Intake
# SSH (or SSM Session Manager) in, check: sudo systemctl status intake-agent
#                                        ls -la /mnt/s3files

cdk deploy S3MultiAgent-LambdaRisk
cdk deploy S3MultiAgent-EcsReport

# EKS is the slowest and most likely to need a docs double-check — see the
# note in stacks/eks_compliance_stack.py before running this one:
cdk deploy S3MultiAgent-EksCompliance

# AgentCore is the newest service in the stack (post-dates most model
# knowledge) — see the note in stacks/agentcore_summary_stack.py:
cdk deploy S3MultiAgent-AgentCoreSummary
```

Or skip the two hardest stages on a first pass:

```bash
DEPLOY_EKS=false DEPLOY_AGENTCORE=false cdk deploy --all
```

and manually drop a file into `validated/` on the mount to test stages 4+5
in isolation once you're ready to tackle them.

## 3. Drive the pipeline end-to-end

Upload a test form to trigger stage 1:

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name S3MultiAgent-Storage \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text)

cat > form.json <<'EOF'
{
  "document_type": "loan_application",
  "content": {
    "applicant": "Jane Doe",
    "amount": 250000,
    "term_years": 30,
    "notes": "Standard fixed-rate mortgage application."
  }
}
EOF

aws s3 cp form.json "s3://$BUCKET/customer-forms/doc-0001.json"
```

Watch it move through the pipeline (SSM into the EC2 instance, or use
`aws logs tail` on the Lambda/ECS/EKS/AgentCore log groups):

```bash
aws ssm start-session --target <IntakeInstanceId-from-stack-output>
ls -la /mnt/s3files/intake/ /mnt/s3files/analyzed/ /mnt/s3files/validated/ \
       /mnt/s3files/reports/ /mnt/s3files/documents/ /mnt/s3files/summaries/
```

Remember the timing from the blog post: NFS close-to-open consistency means
downstream agents on the mount see a new file immediately, but each polling
stage only checks every 10s–1min, and it takes ~60s of write-inactivity
before a file exported through the mount shows up as an S3 object (that only
matters if you're watching the bucket via the console instead of the mount).

## 4. Clean up

Destroy in the reverse order stacks were created (CDK will generally get the
dependency order right with `--all`, but if something's stuck, e.g. EKS
node groups or ENIs left behind by Lambda/ECS/AgentCore Runtime blocking VPC
teardown, go stack by stack):

```bash
cdk destroy S3MultiAgent-AgentCoreSummary
cdk destroy S3MultiAgent-EksCompliance
cdk destroy S3MultiAgent-EcsReport
cdk destroy S3MultiAgent-LambdaRisk
cdk destroy S3MultiAgent-Ec2Intake
cdk destroy S3MultiAgent-Storage
cdk destroy S3MultiAgent-Network
```

The storage stack's bucket has `RemovalPolicy.DESTROY` + `auto_delete_objects`
for home-lab convenience — flip that if you ever point this at real data.

## Repo layout

```
cdk-infra/
  app.py                       # wires all 7 stacks together
  stacks/
    network_stack.py           # VPC, NFS security group
    storage_stack.py           # S3 bucket, S3 Files FS + access point + mount targets, SQS
    ec2_intake_stack.py        # Stage 1
    lambda_risk_stack.py       # Stage 2
    eks_compliance_stack.py    # Stage 3
    ecs_report_stack.py        # Stage 4
    agentcore_summary_stack.py # Stage 5
  agents/
    intake_ec2/                # Stage 1 agent code (systemd service on EC2)
    risk_lambda/                # Stage 2 agent code (Lambda handler)
    compliance_eks/              # Stage 3 agent code (container, 2 replicas)
    report_ecs/                  # Stage 4 agent code (container + python-docx)
    summary_agentcore/            # Stage 5 agent code (container) + invoker Lambda
```

## Known rough edges to expect

This targets an AWS feature (S3 Files) and service (Bedrock AgentCore
Runtime filesystem mounts) that are very new, so treat the CDK code as a
strong starting point rather than something guaranteed to `cdk deploy`
clean on the first try:

- `stacks/ecs_report_stack.py` patches `S3FilesVolumeConfiguration` onto the
  task definition via `add_property_override` because the L2 ECS constructs
  don't model it yet — if CDK ships native support before you deploy, switch
  to that instead.
- `stacks/agentcore_summary_stack.py` creates the AgentCore Runtime through
  an `AwsCustomResource` calling `bedrock-agentcore-control` directly with
  boto3, because the CDK L1 `CfnRuntime` doesn't yet expose
  `filesystemConfigurations`. Check `aws bedrock-agentcore-control
  create-agent-runtime help` for the current parameter shape before deploying.
- `stacks/eks_compliance_stack.py` assumes the `AmazonS3FilesCSIDriverPolicy`
  and `AmazonS3FilesClientFullAccess` managed policies (named in the blog
  post) exist in your account/partition — confirm with `aws iam
  get-policy --policy-arn arn:aws:iam::aws:policy/AmazonS3FilesClientFullAccess`.
