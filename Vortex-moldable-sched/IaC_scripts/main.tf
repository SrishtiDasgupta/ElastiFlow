locals {
  fsx_dns_name   = "fs-04a4223998940b3ef.fsx.eu-north-1.amazonaws.com"
  fsx_mount_name = "5ynhnbev"
}

data "aws_iam_instance_profile" "existing" {
  name = var.iam_instance_profile
}

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
    fsx_dns_name   = local.fsx_dns_name
    fsx_mount_name = local.fsx_mount_name
    s3_script_path = var.s3_script_path
  }))

  tags = {
    Name        = "hpo-worker-${count.index + 1}"
    Project     = "HPO-Workflow"
    Environment = "experiment"
  }
}