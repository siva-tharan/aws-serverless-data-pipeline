#!/usr/bin/env bash
# Builds the Lambda package, uploads it, and deploys the CloudFormation stack.
#
# Usage:
#   NOTIFICATION_EMAIL=you@example.com ./scripts/deploy.sh [stack-name]
#
# The stack name is also used as ProjectName (3-20 lowercase characters).
set -euo pipefail

STACK_NAME="${1:-serverless-pipeline}"
: "${NOTIFICATION_EMAIL:?Set NOTIFICATION_EMAIL to the address that should receive pipeline emails}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region)}}"
if [ -z "$REGION" ]; then
  echo "No AWS region configured. Run 'aws configure' or set AWS_REGION." >&2
  exit 1
fi
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ARTIFACT_BUCKET="${STACK_NAME}-artifacts-${ACCOUNT_ID}-${REGION}"

"$ROOT/scripts/build.sh"

# Private bucket that holds the zipped Lambda code for CloudFormation
if ! aws s3api head-bucket --bucket "$ARTIFACT_BUCKET" --region "$REGION" >/dev/null 2>&1; then
  echo "Creating artifact bucket $ARTIFACT_BUCKET"
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$ARTIFACT_BUCKET" --region "$REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$ARTIFACT_BUCKET" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
  fi
  aws s3api put-public-access-block --bucket "$ARTIFACT_BUCKET" --region "$REGION" \
    --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
fi

echo "Packaging template"
aws cloudformation package \
  --region "$REGION" \
  --template-file "$ROOT/infrastructure/template.yaml" \
  --s3-bucket "$ARTIFACT_BUCKET" \
  --s3-prefix lambda \
  --output-template-file "$ROOT/build/packaged.yaml"

echo "Deploying stack $STACK_NAME (RDS takes about 10 minutes on first deploy)"
aws cloudformation deploy \
  --region "$REGION" \
  --stack-name "$STACK_NAME" \
  --template-file "$ROOT/build/packaged.yaml" \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides "ProjectName=$STACK_NAME" "NotificationEmail=$NOTIFICATION_EMAIL"

aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs" --output table

echo "Check your inbox and confirm the SNS subscription before testing."
