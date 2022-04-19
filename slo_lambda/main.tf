data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    sid    = "assume"
    effect = "Allow"
    actions = [
      "sts:AssumeRole"
    ]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "lambda_policy" {
  statement {
    sid    = "AllowWritesToCloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]
    resources = [
      "${aws_cloudwatch_log_group.windowed_slo_lambda.arn}:*"
    ]
  }

  statement {
    sid    = "ReadWriteCloudWatchMetrics"
    effect = "Allow"
    actions = [
      "cloudwatch:PutMetricData",
      "cloudwatch:GetMetricStatistics",
    ]
    resources = [
      # Change this once we know what the resources are, from errors.
      "*"
    ]
  }
}

# Default CW encryption is adequate for this low-impact Lambda
# tfsec:ignore:aws-cloudwatch-log-group-customer-key
resource "aws_cloudwatch_log_group" "windowed_slo_lambda" {
  name              = "/aws/lambda/${local.name}_windowed_slo"
  retention_in_days = 365
}

resource "aws_iam_role" "windowed_slo_lambda" {
  name_prefix        = "${local.name}_lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "windowed_slo_lambda" {
  name   = "${local.name}_lambda"
  role   = aws_iam_role.windowed_slo_lambda.id
  policy = data.aws_iam_policy_document.lambda_policy.json
}

resource "aws_iam_role_policy_attachment" "windowed_slo_lambda_execution_role" {
  role       = aws_iam_role.windowed_slo_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

module "windowed_slo" {
  source = "github.com/18F/identity-terraform//null_lambda?ref=aef6c906e3d298281a2d00b943aa8452a5c0e7be"
  #source = "../identity-terraform/null_lambda"

  # Ignore missing XRay warning
  # tfsec:ignore:aws-lambda-enable-tracing
  source_code_filename  = "windowed_slo.py"
  source_dir            = "${path.module}/src/"
  zip_filename          = var.slo_lambda_code
  external_role_arn     = aws_iam_role.windowed_slo_lambda.arn
  function_name         = local.name
  description           = "Writes CloudWatch metrics that aggregate over WINDOW_DAYS."
  handler               = "windowed_slo.lambda_handler"
  memory_size           = 128
  runtime               = var.lambda_runtime
  timeout               = 30
  perm_id               = "AllowExecutionFromCloudWatch"
  permission_principal  = ["events.amazonaws.com"]
  permission_source_arn = aws_cloudwatch_event_rule.every_one_day.arn

  env_var_map = {
    WINDOW_DAYS       = var.window_days
    SLI_NAMESPACE     = var.namespace == "" ? "${var.env_name}/sli" : var.namespace
    LOAD_BALANCER_ARN = var.load_balancer_arn
    SLI_PREFIX        = var.sli_prefix
  }
}

resource "aws_cloudwatch_event_rule" "every_one_day" {
  name                = "every-one-day_${local.name}"
  description         = "Fires every day"
  schedule_expression = "rate(1 day)"
}

resource "aws_cloudwatch_event_target" "check_foo_every_one_day" {
  rule      = aws_cloudwatch_event_rule.every_one_day.name
  target_id = module.windowed_slo.lambda_id
  arn       = module.windowed_slo.lambda_arn
}
