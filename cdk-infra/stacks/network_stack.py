from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from constructs import Construct


class NetworkStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = ec2.Vpc(
            self, "Vpc",
            max_azs=2,
            nat_gateways=1,  
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="Private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=24
                ),
            ],
        )

        self.nfs_security_group = ec2.SecurityGroup(
            self, "NfsSecurityGroup",
            vpc=self.vpc,
            description="Allows NFS (2049) traffic to S3 Files mount targets",
            allow_all_outbound=True,
        )
        self.nfs_security_group.add_ingress_rule(
            peer=self.nfs_security_group,
            connection=ec2.Port.tcp(2049),
            description="NFS from other members of this SG",
        )
