import aws_cdk as cdk
from aws_cdk import Stack, Duration
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_s3files as s3files
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_logs as logs
from constructs import Construct


class EcsReportStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, *,
        vpc: ec2.Vpc,
        nfs_security_group: ec2.SecurityGroup,
        file_system: s3files.CfnFileSystem,
        access_point: s3files.CfnAccessPoint,
        bucket: s3.Bucket,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cluster = ecs.Cluster(self, "ReportCluster", vpc=vpc, container_insights=True)

        task_role = iam.Role(
            self, "ReportTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        fs_arn = (
            f"arn:{cdk.Aws.PARTITION}:s3files:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:"
            f"file-system/{file_system.attr_file_system_id}"
        )
        task_role.add_to_policy(iam.PolicyStatement(
            actions=["s3files:ClientMount", "s3files:ClientWrite"],
            resources=[fs_arn],
        ))
        task_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=["*"],
        ))
        bucket.grant_read(task_role)

        task_definition = ecs.FargateTaskDefinition(
            self, "ReportTaskDef",
            cpu=1024,
            memory_limit_mib=2048,
            task_role=task_role,
        )

        image = ecr_assets.DockerImageAsset(
            self, "ReportAgentImage",
            directory="agents/report_ecs",
        )

        volume_name = "s3files-vol"
        task_definition.add_volume(name=volume_name)

        container = task_definition.add_container(
            "ReportAgentContainer",
            image=ecs.ContainerImage.from_docker_image_asset(image),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="report-agent", log_retention=logs.RetentionDays.ONE_WEEK),
            environment={
                "MODEL_ID": "us.anthropic.claude-sonnet-4-6",
                "AWS_DEFAULT_REGION": cdk.Aws.REGION,
            },
        )
        container.add_mount_points(ecs.MountPoint(
            container_path="/mnt/s3files",
            source_volume=volume_name,
            read_only=False,
        ))

        # The L2 constructs don't yet model S3FilesVolumeConfiguration, so
        # patch it into the synthesized CloudFormation Volumes[0] entry.
        cfn_task_def: ecs.CfnTaskDefinition = task_definition.node.default_child
        cfn_task_def.add_property_override(
            "Volumes.0.S3FilesVolumeConfiguration",
            {
                "FileSystemArn": fs_arn,
                "AccessPointArn": access_point.attr_access_point_arn,
                "RootDirectory": "/",
            },
        )

        service = ecs.FargateService(
            self, "ReportService",
            cluster=cluster,
            task_definition=task_definition,
            desired_count=1,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[nfs_security_group],
        )

        cdk.CfnOutput(self, "ReportClusterName", value=cluster.cluster_name)
        cdk.CfnOutput(self, "ReportServiceName", value=service.service_name)
