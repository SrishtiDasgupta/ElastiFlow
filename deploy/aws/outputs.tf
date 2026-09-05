output "instance_ids" {
  description = "IDs of created instances"
  value       = aws_instance.hpo_worker[*].id
}

output "instance_public_ips" {
  description = "Public IPs of created instances"
  value       = aws_instance.hpo_worker[*].public_ip
}

output "instance_private_ips" {
  description = "Private IPs of created instances"
  value       = aws_instance.hpo_worker[*].private_ip
}

output "ssh_commands" {
  description = "SSH commands to connect to instances"
  value       = [for ip in aws_instance.hpo_worker[*].public_ip : "ssh -i ~/.ssh/aws/hpc-exp.pem ubuntu@${ip}"]
}
