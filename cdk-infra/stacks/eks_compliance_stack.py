import aws_cdk as cdk
from aws_cdk import Stack, Duration
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_eks as eks
from aws_cdk import aws_iam as iam
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_s3files as s3files
from aws_cdk import aws_s3 as s3
from constructs import Construct


class EksComplianceStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, *,
        vpc: ec2.Vpc,
        nfs_security_group: ec2.SecurityGroup,
        file_system: s3files.CfnFileSystem,
        bucket: s3.Bucket,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cluster = eks.Cluster(
            self, "ComplianceCluster",
            vpc=vpc,
            vpc_subnets=[ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)],
            version=eks.KubernetesVersion.V1_30,
            default_capacity=0,
            cluster_name="s3-multi-agent-compliance",
        )
        cluster.cluster_security_group.add_ingress_rule(
            peer=nfs_security_group, connection=ec2.Port.tcp(2049),
            description="Allow cluster nodes to speak NFS to S3 Files mount targets",
        )
        node_role = iam.Role(
            self, "NodeRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEKSWorkerNodePolicy"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEC2ContainerRegistryReadOnly"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEKS_CNI_Policy"),
            ],
        )
        fs_arn = (
            f"arn:{cdk.Aws.PARTITION}:s3files:{cdk.Aws.REGION}:{cdk.Aws.ACCOUNT_ID}:"
            f"file-system/{file_system.attr_file_system_id}"
        )
        node_role.add_to_policy(iam.PolicyStatement(
            actions=["s3files:ClientMount", "s3files:ClientWrite"],
            resources=[fs_arn],
        ))
        # Agent pods also call Bedrock directly, via the node role for simplicity
        # (use a dedicated IRSA role per pod if you want tighter scoping).
        node_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=["*"],
        ))
        bucket.grant_read(node_role)

        cluster.add_nodegroup_capacity(
            "ComplianceNodeGroup",
            instance_types=[ec2.InstanceType("t3.medium")],
            min_size=2,
            max_size=2,
            desired_size=2,
            node_role=node_role,
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
        )

        csi_role = iam.Role(
            self, "EfsCsiControllerRole",
            assumed_by=iam.FederatedPrincipal(
                cluster.open_id_connect_provider.open_id_connect_provider_arn,
                conditions={
                    "StringEquals": {
                        f"{cluster.cluster_open_id_connect_issuer}:sub":
                            "system:serviceaccount:kube-system:efs-csi-controller-sa",
                        f"{cluster.cluster_open_id_connect_issuer}:aud": "sts.amazonaws.com",
                    }
                },
                assume_role_action="sts:AssumeRoleWithWebIdentity",
            ),
        )
        csi_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3FilesCSIDriverPolicy")
        )

        eks.CfnAddon(
            self, "EfsCsiAddon",
            addon_name="aws-efs-csi-driver",
            cluster_name=cluster.cluster_name,
            service_account_role_arn=csi_role.role_arn,
            resolve_conflicts="OVERWRITE",
        )

        storage_class = cluster.add_manifest("S3FilesStorageClass", {
            "apiVersion": "storage.k8s.io/v1",
            "kind": "StorageClass",
            "metadata": {"name": "s3files-sc"},
            "provisioner": "efs.csi.aws.com",
        })

        pv = cluster.add_manifest("S3FilesPV", {
            "apiVersion": "v1",
            "kind": "PersistentVolume",
            "metadata": {"name": "s3files-pv"},
            "spec": {
                "capacity": {"storage": "5Gi"},
                "accessModes": ["ReadWriteMany"],
                "storageClassName": "s3files-sc",
                "csi": {
                    "driver": "efs.csi.aws.com",
                    "volumeHandle": f"s3files:{file_system.attr_file_system_id}",
                },
            },
        })
        pv.node.add_dependency(storage_class)

        pvc = cluster.add_manifest("S3FilesPVC", {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": "s3files-pvc"},
            "spec": {
                "accessModes": ["ReadWriteMany"],
                "storageClassName": "s3files-sc",
                "resources": {"requests": {"storage": "5Gi"}},
                "volumeName": "s3files-pv",
            },
        })
        pvc.node.add_dependency(pv)

        image = ecr_assets.DockerImageAsset(
            self, "ComplianceAgentImage",
            directory="agents/compliance_eks",
        )

        deployment = cluster.add_manifest("ComplianceDeployment", {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "compliance-agent"},
            "spec": {
                "replicas": 2,
                "selector": {"matchLabels": {"app": "compliance-agent"}},
                "template": {
                    "metadata": {"labels": {"app": "compliance-agent"}},
                    "spec": {
                        "containers": [{
                            "name": "compliance-agent",
                            "image": image.image_uri,
                            "env": [
                                {"name": "MODEL_ID", "value": "us.anthropic.claude-sonnet-4-6"},
                                {"name": "AWS_DEFAULT_REGION", "value": cdk.Aws.REGION},
                            ],
                            "volumeMounts": [{"name": "s3files", "mountPath": "/mnt/s3files"}],
                        }],
                        "volumes": [{
                            "name": "s3files",
                            "persistentVolumeClaim": {"claimName": "s3files-pvc"},
                        }],
                    },
                },
            },
        })
        deployment.node.add_dependency(pvc)

        cdk.CfnOutput(self, "ClusterName", value=cluster.cluster_name)
