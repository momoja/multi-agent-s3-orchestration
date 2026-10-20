import aws_cdk as cdk
from aws_cdk import Stack, Duration
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_s3files as s3files
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import custom_resources as cr
from constructs import Construct


class AgentCoreSummaryStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, *,
        vpc: ec2.Vpc,
        nfs_security_group: ec2.SecurityGroup,
        access_point: s3files.CfnAccessPoint,
        bucket: s3.Bucket,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        image = ecr_assets.DockerImageAsset(
            self, "SummaryAgentImage",
            directory="agents/summary_agentcore",
        )

        runtime_role = iam.Role(
            self, "AgentRuntimeRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
        )
        fs_arn = (
            f"arn:{cdk.Aws.PARTITION}:s3files:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:"
            f"file-system/{access_point.file_system_id}"
        )
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=["s3files:ClientMount", "s3files:ClientWrite", "s3files:GetAccessPoint"],
            resources=[fs_arn, access_point.attr_access_point_arn],
        ))
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=["*"],
        ))
        bucket.grant_read(runtime_role)
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=["ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage", "ecr:GetAuthorizationToken"],
            resources=["*"],
        ))

        agentcore_sg = ec2.SecurityGroup(
            self, "AgentCoreSecurityGroup", vpc=vpc, allow_all_outbound=True,
        )
        agentcore_sg.add_egress_rule(nfs_security_group, ec2.Port.tcp(2049), "NFS to S3 Files mount targets")
        nfs_security_group.add_ingress_rule(agentcore_sg, ec2.Port.tcp(2049), "NFS from AgentCore Runtime")

        create_params = {
            "agentRuntimeName": "summary_agent",
            "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": image.image_uri}},
            "roleArn": runtime_role.role_arn,
            "networkConfiguration": {
                "networkMode": "VPC",
                "networkModeConfig": {
                    "subnets": [s.subnet_id for s in vpc.private_subnets],
                    "securityGroups": [agentcore_sg.security_group_id],
                },
            },
            "filesystemConfigurations": [{
                "s3FilesAccessPoint": {
                    "accessPointArn": access_point.attr_access_point_arn,
                    "mountPath": "/mnt/s3files",
                },
            }],
        }

        runtime_resource = cr.AwsCustomResource(
            self, "AgentRuntimeResource",
            on_create=cr.AwsSdkCall(
                service="bedrock-agentcore-control",
                action="createAgentRuntime",
                parameters=create_params,
                physical_resource_id=cr.PhysicalResourceId.from_response("agentRuntimeId"),
            ),
            on_update=cr.AwsSdkCall(
                service="bedrock-agentcore-control",
                action="updateAgentRuntime",
                parameters={
                    **create_params,
                    "agentRuntimeId": cr.PhysicalResourceIdReference(),
                },
                physical_resource_id=cr.PhysicalResourceId.from_response("agentRuntimeId"),
            ),
            on_delete=cr.AwsSdkCall(
                service="bedrock-agentcore-control",
                action="deleteAgentRuntime",
                parameters={"agentRuntimeId": cr.PhysicalResourceIdReference()},
            ),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(
                resources=cr.AwsCustomResourcePolicy.ANY_RESOURCE,
            ),
            timeout=Duration.minutes(10),
        )
        runtime_role.grant_pass_role(runtime_resource.grant_principal)

        agent_runtime_id = runtime_resource.get_response_field("agentRuntimeId")

        # --- 1-minute invoker (EventBridge -> Lambda -> InvokeAgentRuntime) --
        invoker_role = iam.Role(
            self, "InvokerRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
            ],
        )
        invoker_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:InvokeAgentRuntime"],
            resources=["*"],
        ))

        invoker_fn = _lambda.Function(
            self, "AgentRuntimeInvoker",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="invoker.handler",
            code=_lambda.Code.from_asset("agents/summary_agentcore/invoker"),
            role=invoker_role,
            timeout=Duration.minutes(2),
            environment={"AGENT_RUNTIME_ID": agent_runtime_id},
        )

        rule = events.Rule(self, "SummaryScheduleRule", schedule=events.Schedule.rate(Duration.minutes(1)))
        rule.add_target(targets.LambdaFunction(invoker_fn))

        cdk.CfnOutput(self, "AgentRuntimeId", value=agent_runtime_id)
