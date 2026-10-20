#!/usr/bin/env python3
"""
aws-s3-multi-agent — home lab CDK app
Builds the 5-stage S3-Files multi-agent pipeline from the AWS blog post:
"Orchestrating multi-agent AI architectures with Amazon S3 Files"
(EC2 -> Lambda -> EKS -> ECS Fargate -> Bedrock AgentCore Runtime)

Usage:
    cd cdk-infra
    uv venv && source .venv/bin/activate    # or python -m venv .venv
    uv pip install -r requirements.txt      # or pip install -r requirements.txt
    cdk bootstrap
    cdk deploy --all

Stacks can also be deployed individually and incrementally — see README.md.
"""
import os
import aws_cdk as cdk

from stacks.network_stack import NetworkStack
from stacks.storage_stack import StorageStack
from stacks.ec2_intake_stack import Ec2IntakeStack
from stacks.lambda_risk_stack import LambdaRiskStack
from stacks.ecs_report_stack import EcsReportStack
from stacks.eks_compliance_stack import EksComplianceStack
from stacks.agentcore_summary_stack import AgentCoreSummaryStack

app = cdk.App()

env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)

# Toggle heavy/optional stages off if you want a cheaper / faster first deploy.
# EKS in particular adds ~15 min deploy time and a control-plane charge.
DEPLOY_EKS = os.environ.get("DEPLOY_EKS", "true").lower() == "true"
DEPLOY_AGENTCORE = os.environ.get("DEPLOY_AGENTCORE", "true").lower() == "true"

prefix = "S3MultiAgent"

network = NetworkStack(app, f"{prefix}-Network", env=env)

storage = StorageStack(
    app, f"{prefix}-Storage", env=env,
    vpc=network.vpc,
    nfs_security_group=network.nfs_security_group,
)

ec2_intake = Ec2IntakeStack(
    app, f"{prefix}-Ec2Intake", env=env,
    vpc=network.vpc,
    nfs_security_group=network.nfs_security_group,
    file_system=storage.file_system,
    bucket=storage.bucket,
    queue=storage.intake_queue,
)

lambda_risk = LambdaRiskStack(
    app, f"{prefix}-LambdaRisk", env=env,
    vpc=network.vpc,
    nfs_security_group=network.nfs_security_group,
    access_point=storage.access_point,
    bucket=storage.bucket,
)

ecs_report = EcsReportStack(
    app, f"{prefix}-EcsReport", env=env,
    vpc=network.vpc,
    nfs_security_group=network.nfs_security_group,
    file_system=storage.file_system,
    access_point=storage.access_point,
    bucket=storage.bucket,
)

if DEPLOY_EKS:
    eks_compliance = EksComplianceStack(
        app, f"{prefix}-EksCompliance", env=env,
        vpc=network.vpc,
        nfs_security_group=network.nfs_security_group,
        file_system=storage.file_system,
        bucket=storage.bucket,
    )

if DEPLOY_AGENTCORE:
    agentcore_summary = AgentCoreSummaryStack(
        app, f"{prefix}-AgentCoreSummary", env=env,
        vpc=network.vpc,
        nfs_security_group=network.nfs_security_group,
        access_point=storage.access_point,
        bucket=storage.bucket,
    )

app.synth()
