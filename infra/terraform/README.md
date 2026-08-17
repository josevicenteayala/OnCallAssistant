# Terraform Capture For Existing Lambda Infra

This folder now includes a safe import workflow for capturing an already-deployed
Lambda Function URL setup into Terraform.

## What is included

- `import_existing_lambda.tf`: import blocks + minimal resource stubs
- `terraform.tfvars.example`: variable template

## Why this approach

Instead of manually rewriting AWS settings, Terraform can read the live resource
configuration and generate Terraform code directly from AWS.

## Prerequisites

1. Terraform `>= 1.5`
2. AWS CLI authenticated to the target account
3. Access to Lambda in `us-east-2` (or your target region)

## 1) Refresh AWS credentials

Validate credentials first:

```bash
aws sts get-caller-identity
```

If this fails with an invalid/expired token, refresh credentials first (for
example: `aws sso login --profile <profile>` or update access keys).

## 2) Map Function URL to Lambda function name

Set your function URL and discover the function name:

```bash
export TARGET_FUNCTION_URL="https://<your-function-url-id>.lambda-url.us-east-2.on.aws/"
export REGION="us-east-2"

for fn in $(aws lambda list-functions --region "$REGION" --query 'Functions[].FunctionName' --output text); do
	url=$(aws lambda get-function-url-config --region "$REGION" --function-name "$fn" --query 'FunctionUrl' --output text 2>/dev/null || true)
	if [ "$url" = "$TARGET_FUNCTION_URL" ]; then
		echo "MATCHED_FUNCTION_NAME=$fn"
	fi
done
```

## 3) Prepare tfvars

```bash
cp terraform.tfvars.example terraform.tfvars
```

Update `lambda_function_name` with the discovered value.

## 4) Import and auto-generate Terraform config

```bash
terraform init
terraform plan -var-file=terraform.tfvars -generate-config-out=generated_lambda.tf
```

This creates `generated_lambda.tf` with the live Lambda and Function URL
configuration read from AWS.

## 5) Review and harden

Review generated values before apply:

1. Confirm runtime, timeout, memory, and env vars
2. Add tags and lifecycle rules as needed
3. Add IAM role/policies/S3/Bedrock resources if you want full infra ownership

## Note

Current project roadmap still includes broader Terraform modules for S3 + Bedrock
KB + DynamoDB. This import flow is focused on safely capturing existing Lambda
infrastructure and configuration first.
