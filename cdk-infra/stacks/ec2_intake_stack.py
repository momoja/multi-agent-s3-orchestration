import aws_cdk as cdk
from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3_assets as s3_assets
from aws_cdk import aws_s3files as s3files
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_sqs as sqs
from constructs import Construct


class Ec2IntakeStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, *,
        vpc: ec2.Vpc,
        nfs_security_group: ec2.SecurityGroup,
        file_system: s3files.CfnFileSystem,
        bucket: s3.Bucket,
        queue: sqs.Queue,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        role = iam.Role(
            self, "IntakeInstanceRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
            ],
        )
        role.add_to_policy(iam.PolicyStatement(
            actions=["s3files:ClientMount", "s3files:ClientWrite", "s3files:DescribeFileSystems"],
            resources=[
                f"arn:{cdk.Aws.PARTITION}:s3files:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:"
                f"file-system/{file_system.attr_file_system_id}"
            ],
        ))
        role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=["*"],
        ))
        bucket.grant_read(role)
        queue.grant_consume_messages(role)

        # Ship the agent code to the instance as a CDK asset.
        agent_asset = s3_assets.Asset(
            self, "IntakeAgentAsset",
            path="agents/intake_ec2",
        )
        agent_asset.grant_read(role)

        user_data = ec2.UserData.for_linux()
        local_zip = "/opt/intake-agent.zip"
        user_data.add_s3_download_command(
            bucket=agent_asset.bucket,
            bucket_key=agent_asset.s3_object_key,
            local_file=local_zip,
        )
        user_data.add_commands(
            "set -eux",
            "dnf install -y amazon-efs-utils python3.12 python3.12-pip unzip",
            "mkdir -p /mnt/s3files",
            f"echo '{file_system.attr_file_system_id}:/ /mnt/s3files s3files _netdev,tls 0 0' >> /etc/fstab",
            "mount -a -t s3files || mount -t s3files "
            f"{file_system.attr_file_system_id}:/ /mnt/s3files",
            # Shared pipeline directories, world-writable so the UID-1000
            # access point (Lambda / ECS / AgentCore) can also write.
            "mkdir -p /mnt/s3files/customer-forms /mnt/s3files/intake "
            "/mnt/s3files/analyzed /mnt/s3files/validated /mnt/s3files/reports "
            "/mnt/s3files/documents /mnt/s3files/summaries",
            "chmod -R 777 /mnt/s3files",
            "mkdir -p /opt/intake-agent",
            f"unzip -o {local_zip} -d /opt/intake-agent",
            "python3.12 -m pip install --upgrade pip",
            "python3.12 -m pip install strands-agents boto3",
            f"echo 'QUEUE_URL={queue.queue_url}' > /etc/intake-agent.env",
            f"echo 'AWS_DEFAULT_REGION={cdk.Aws.REGION}' >> /etc/intake-agent.env",
            "cat > /etc/systemd/system/intake-agent.service <<'EOF'\n"
            "[Unit]\nDescription=S3 Files intake agent\nAfter=network.target\n\n"
            "[Service]\nType=simple\n"
            "EnvironmentFile=/etc/intake-agent.env\n"
            "ExecStart=/usr/bin/python3.12 /opt/intake-agent/intake_agent.py\n"
            "Restart=always\nRestartSec=5\n\n"
            "[Install]\nWantedBy=multi-user.target\nEOF",
            "systemctl daemon-reload",
            "systemctl enable --now intake-agent.service",
        )

        self.instance = ec2.Instance(
            self, "IntakeInstance",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.SMALL),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(),
            role=role,
            security_group=nfs_security_group,
            user_data=user_data,
            require_imdsv2=True,
        )

        cdk.CfnOutput(self, "IntakeInstanceId", value=self.instance.instance_id)
