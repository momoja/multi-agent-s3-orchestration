import aws_cdk as cdk
from aws_cdk import Stack, Duration
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_s3files as s3files
from aws_cdk import aws_s3 as s3
from aws_cdk.aws_lambda_python_alpha import PythonFunction
from constructs import Construct


class LambdaRiskStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, *,
        vpc: ec2.Vpc,
        nfs_security_group: ec2.SecurityGroup,
        access_point: s3files.CfnAccessPoint,
        bucket: s3.Bucket,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        role = iam.Role(
            self, "RiskLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole"),
            ],
        )
        fs_arn = (
            f"arn:{cdk.Aws.PARTITION}:s3files:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:"
            f"file-system/{access_point.file_system_id}"
        )
        role.add_to_policy(iam.PolicyStatement(
            actions=["s3files:ClientMount", "s3files:ClientWrite"],
            resources=[fs_arn],
        ))
        role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=["*"],
        ))
        bucket.grant_read(role)

        fn = PythonFunction(
            self, "RiskAnalysisFunction",
            entry="agents/risk_lambda",
            index="handler.py",
            handler="handler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            memory_size=2048,
            timeout=Duration.minutes(10),
            role=role,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[nfs_security_group],
            filesystem=_lambda.FileSystem(
                arn=access_point.attr_access_point_arn,
                local_mount_path="/mnt/s3files",
            ),
            environment={
                "MODEL_ID": "us.anthropic.claude-sonnet-4-6",
            },
        )

        rule = events.Rule(
            self, "RiskScheduleRule",
            schedule=events.Schedule.rate(Duration.minutes(1)),
        )
        rule.add_target(targets.LambdaFunction(fn))

        cdk.CfnOutput(self, "RiskFunctionName", value=fn.function_name)
