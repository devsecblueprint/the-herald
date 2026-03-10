# Discord Bot Lambda Infrastructure

This directory contains Terraform configuration for deploying the Discord bot Lambda function and associated AWS infrastructure.

## Prerequisites

- [Terraform](https://www.terraform.io/downloads.html) >= 1.0 or [Terraform Cloud](https://app.terraform.io/) account
- [AWS CLI](https://aws.amazon.com/cli/) configured with appropriate credentials (or AWS credentials configured in Terraform Cloud)
- Lambda deployment package and layer ZIP files built using `tasks.py` from project root

## Infrastructure Components

This Terraform configuration creates the following AWS resources:

- **Lambda Function**: `the-herald-handler` with Python 3.11 runtime
- **Lambda Layer**: Python dependencies package
- **EventBridge Rules**: Two scheduled rules for newsletter and event notifications
- **DynamoDB Table**: `the-herald-reminders` for tracking sent reminders
- **Parameter Store**: Secure storage for Discord bot token and guild ID
- **IAM Role**: Lambda execution role with least-privilege permissions
- **CloudWatch Log Group**: For Lambda function logs

## Quick Start

1. **Build deployment packages** (from project root):
   ```bash
   python tasks.py build-all
   ```

2. **Create your variables file** (for local Terraform) or configure in Terraform Cloud:
   ```bash
   cd terraform
   cp terraform.tfvars.example terraform.tfvars
   ```

3. **Edit `terraform.tfvars`** with your actual values:
   - Set `discord_token` to your Discord bot token
   - Set `discord_guild_id` to your Discord server ID
   - Adjust other variables as needed (region, environment, etc.)

4. **Initialize Terraform**:
   ```bash
   terraform init
   ```

5. **Review the plan**:
   ```bash
   terraform plan
   ```

6. **Apply the configuration**:
   ```bash
   terraform apply
   ```

## Configuration Variables

See `variables.tf` for all available configuration options. Key variables include:

| Variable | Description | Default |
|----------|-------------|---------|
| `aws_region` | AWS region for resources | `us-east-1` |
| `environment` | Environment name (dev/staging/prod) | `prod` |
| `discord_token` | Discord bot authentication token | **Required** |
| `discord_guild_id` | Discord server (guild) ID | **Required** |
| `lambda_deployment_package_path` | Path to Lambda ZIP file | `lambda_deployment_package.zip` |
| `lambda_layer_package_path` | Path to layer ZIP file | `lambda_layer.zip` |
| `log_level` | Lambda logging level | `INFO` |
| `parameter_store_prefix` | Prefix for Parameter Store keys | `/the-herald/prod/` |

## Outputs

After applying, Terraform will output important resource identifiers:

- Lambda function ARN and name
- Lambda layer ARN and version
- DynamoDB table name and ARN
- Parameter Store parameter names
- CloudWatch log group name
- EventBridge rule ARNs
- IAM role ARN

View outputs anytime with:
```bash
terraform output
```

## Updating the Infrastructure

### Update Lambda Function Code

1. Rebuild the deployment package:
   ```bash
   # From project root
   python tasks.py build-package
   ```

2. Apply the changes:
   ```bash
   cd terraform
   terraform apply
   ```

Terraform will detect the new ZIP file hash and update the Lambda function.

### Update Lambda Layer

1. Rebuild the layer:
   ```bash
   # From project root
   python tasks.py build-layer
   ```

2. Apply the changes:
   ```bash
   cd terraform
   terraform apply
   ```

A new layer version will be created and the Lambda function will be updated to use it.

### Update Secrets

To update the Discord bot token or guild ID:

1. Update the values in `terraform.tfvars`
2. Apply the changes:
   ```bash
   terraform apply
   ```

**Note**: The `discord_token` parameter has `ignore_changes` lifecycle rule, so Terraform won't update it after initial creation. To force an update, use:
```bash
terraform taint aws_ssm_parameter.discord_token
terraform apply
```

## Monitoring

### View Lambda Logs

```bash
aws logs tail /aws/lambda/the-herald-handler --follow
```

### Check Lambda Invocations

```bash
aws lambda get-function --function-name the-herald-handler
```

### View DynamoDB Items

```bash
aws dynamodb scan --table-name the-herald-reminders
```

## Cleanup

To destroy all resources:

```bash
terraform destroy
```

**Warning**: This will permanently delete all resources including the DynamoDB table and Parameter Store parameters.

## File Structure

```
terraform/
├── provider.tf                  # Terraform and AWS provider configuration
├── data.tf                      # Data sources (AWS account info)
├── main.tf                      # All infrastructure resources (Lambda, EventBridge, DynamoDB, IAM, etc.)
├── variables.tf                 # Input variable definitions
├── terraform.tfvars.example     # Example variables file
├── README.md                    # This file
├── lambda_layer.zip             # Lambda layer (built by tasks.py)
└── lambda_deployment_package.zip # Lambda code (built by tasks.py)
```

## Security Considerations

- **Never commit `terraform.tfvars`** to version control (it contains sensitive data)
- The Discord bot token is stored as a SecureString in Parameter Store with KMS encryption
- IAM policies follow the principle of least privilege
- Lambda function has no public access (invoked only by EventBridge)
- DynamoDB table uses on-demand billing to minimize costs

## Troubleshooting

### "No such file or directory" error for ZIP files

Ensure you've built the deployment packages from the project root:
```bash
python tasks.py build-all
```

The ZIP files should be located at:
- `terraform/lambda_layer.zip`
- `terraform/lambda_deployment_package.zip`

### Permission denied errors

Ensure your AWS credentials have sufficient permissions to create:
- Lambda functions and layers
- EventBridge rules
- DynamoDB tables
- Parameter Store parameters
- IAM roles and policies
- CloudWatch log groups

### Lambda function fails to start

Check CloudWatch logs for errors:
```bash
aws logs tail /aws/lambda/the-herald-handler --follow
```

Common issues:
- Missing or invalid Parameter Store values
- Incorrect DynamoDB table name
- Missing dependencies in Lambda layer

## Support

For issues or questions, refer to:
- [AWS Lambda Documentation](https://docs.aws.amazon.com/lambda/)
- [Terraform AWS Provider Documentation](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)
- Project README in the root directory
