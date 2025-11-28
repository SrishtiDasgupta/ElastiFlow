# Data source for existing IAM instance profile
data "aws_iam_instance_profile" "existing" {
  name = var.iam_instance_profile
}

# Data source for FSx filesystem to get mount name
data "aws_fsx_lustre_file_system" "existing" {
  id = "fs-04a4223998940b3ef"
}

# EC2 Instances
resource "aws_instance" "hpo_worker" {
  count = var.instance_count

  ami                    = var.ami_id
  instance_type          = var.instance_type
  key_name               = var.key_name
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [var.security_group_id]
  iam_instance_profile   = data.aws_iam_instance_profile.existing.name

  root_block_device {
    volume_size = 100
    volume_type = "gp3"
  }

  user_data = base64encode(templatefile("${path.module}/user_data.sh", {
    git_token      = var.git_token
    git_repo       = var.git_repo
    git_branch     = var.git_branch
    fsx_dns_name   = data.aws_fsx_lustre_file_system.existing.dns_name
    fsx_mount_name = data.aws_fsx_lustre_file_system.existing.mount_name
    s3_script_path = var.s3_script_path
  }))

  tags = {
    Name        = "hpo-worker-${count.index + 1}"
    Project     = "HPO-Workflow"
    Environment = "experiment"
  }
}
