import aws_cdk as cdk
from aws_cdk import Stack, Duration, RemovalPolicy
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3files as s3files
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_s3_notifications as s3n
from constructs import Construct


class StorageStack(Stack):   
    def __init__(
        self, scope: Construct, construct_id: str, *,
        vpc: ec2.Vpc,
        nfs_security_group: ec2.SecurityGroup,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        # Versioning is required: S3 Files relies on object versions for consistency.
        self.bucket = s3.Bucket(
            self, "PipelineBucket",
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,   # home lab: ok to nuke on teardown
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
        )
        # S3 Files assumes this role to sync data between the bucket and the file system.
        s3files_role = iam.Role(
            self, "S3FilesRole",
            assumed_by=iam.ServicePrincipal("elasticfilesystem.amazonaws.com"),
        )
        self.bucket.grant_read_write(s3files_role)
        s3files_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "events:DeleteRule", "events:DisableRule", "events:EnableRule",
                "events:PutRule", "events:PutTargets", "events:RemoveTargets",
            ],
            resources=[f"arn:{cdk.Aws.PARTITION}:events:*:*:rule/DO-NOT-DELETE-S3-Files*"],
            conditions={"StringEquals": {"events:ManagedBy": "elasticfilesystem.amazonaws.com"}},
        ))
        s3files_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "events:DescribeRule", "events:ListRuleNamesByTarget",
                "events:ListRules", "events:ListTargetsByRule",
            ],
            resources=[f"arn:{cdk.Aws.PARTITION}:events:*:*:rule/*"],
        ))

        self.file_system = s3files.CfnFileSystem(
            self, "PipelineFileSystem",
            bucket=self.bucket.bucket_arn,
            role_arn=s3files_role.role_arn,
        )

        # Mount targets: one per AZ, in the private subnets, so EC2 and EKS
        # (which mount the file system directly, no access point) can reach it.
        for i, subnet in enumerate(vpc.private_subnets):
            s3files.CfnMountTarget(
                self, f"MountTarget{i}",
                file_system_id=self.file_system.attr_file_system_id,
                subnet_id=subnet.subnet_id,
                security_groups=[nfs_security_group.security_group_id],
            )

        # Shared access point for Lambda / ECS Fargate / AgentCore Runtime.
        # UID/GID 1000 matches AgentCore's container user; EC2 creates the
        # pipeline directories world-writable (777) so this identity can write too.
        self.access_point = s3files.CfnAccessPoint(
            self, "SharedAccessPoint",
            file_system_id=self.file_system.attr_file_system_id,
            posix_user=s3files.CfnAccessPoint.PosixUserProperty(
                uid="1000",
                gid="1000",
            ),
            root_directory=s3files.CfnAccessPoint.RootDirectoryProperty(
                path="/",
            ),
        )

        #  SQS trigger for the intake stage 
        self.intake_dlq = sqs.Queue(
            self, "IntakeDLQ",
            retention_period=Duration.days(14),
        )
        self.intake_queue = sqs.Queue(
            self, "IntakeQueue",
            visibility_timeout=Duration.seconds(60),
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=5, queue=self.intake_dlq),
        )

        # New objects under customer-forms/ trigger the EC2 intake agent.
        self.bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.SqsDestination(self.intake_queue),
            s3.NotificationKeyFilter(prefix="customer-forms/", suffix=".json"),
        )

        cdk.CfnOutput(self, "BucketName", value=self.bucket.bucket_name)
        cdk.CfnOutput(self, "FileSystemId", value=self.file_system.attr_file_system_id)
        cdk.CfnOutput(self, "AccessPointArn", value=self.access_point.attr_access_point_arn)
        cdk.CfnOutput(self, "IntakeQueueUrl", value=self.intake_queue.queue_url)
