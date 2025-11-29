variable "region" {
  description = "AWS region"
  type        = string
  default     = "eu-north-1"
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "g4dn.xlarge"
}

variable "instance_count" {
  description = "Number of instances to create"
  type        = number
  default     = 2
}

variable "ami_id" {
  description = "AMI ID for instances"
  type        = string
  default     = "ami-0bace95aa4cfc84f8"
}

variable "key_name" {
  description = "SSH key pair name"
  type        = string
  default     = "hpc-exp"
}

variable "vpc_id" {
  description = "Existing VPC ID"
  type        = string
  default     = "vpc-0448f632477be3efa"
}

variable "subnet_id" {
  description = "Existing subnet ID"
  type        = string
  default     = "subnet-098f73921fcc5fbe1"
}

variable "security_group_id" {
  description = "Existing security group ID"
  type        = string
  default     = "sg-0d61f6325a433891b"
}

variable "iam_instance_profile" {
  description = "IAM instance profile name"
  type        = string
}

variable "fsx_dns_name" {
  description = "FSx Lustre DNS name"
  type        = string
}

variable "fsx_mount_name" {
  description = "FSx Lustre mount name"
  type        = string
}

variable "s3_script_path" {
  description = "S3 path to startup script"
  type        = string
  default     = "s3://hpoexp/instance_setup_hpo.sh"
}

variable "git_token" {
  description = "GitHub Personal Access Token"
  type        = string
  sensitive   = true
}

variable "git_repo" {
  description = "GitHub repository URL (without token)"
  type        = string
  default     = "https://github.com/SrishtiDasgupta/Vortex-mid.git"
}

variable "git_branch" {
  description = "Git branch to checkout"
  type        = string
  default     = "hpo_exp"
}
