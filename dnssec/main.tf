# https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-configuring-dnssec-troubleshoot.html

# -- Data Sources --

data "aws_caller_identity" "current" {
}

data "aws_iam_policy_document" "ksk_policy" {
  statement {
    sid    = "Allow Route 53 DNSSEC Service Access To KMS Keys"
    effect = "Allow"
    actions = [
      "kms:DescribeKey",
      "kms:GetPublicKey",
      "kms:Sign",
    ]
    principals {
      type = "Service"
      identifiers = [
        "dnssec-route53.amazonaws.com"
      ]
    }
    resources = [
      "*"
    ]
  }
  statement {
    sid    = "Allow Route 53 DNSSEC Service To CreateGrant"
    effect = "Allow"
    actions = [
      "kms:CreateGrant"
    ]
    principals {
      type = "Service"
      identifiers = [
        "dnssec-route53.amazonaws.com"
      ]
    }
    resources = [
      "*"
    ]
    condition {
      test     = "Bool"
      variable = "kms:GrantIsForAWSResource"
      values = [
        "true",
      ]
    }
  }
  statement {
    sid    = "KMS IAM User Permissions"
    effect = "Allow"
    actions = [
      "kms:*"
    ]
    principals {
      type = "AWS"
      identifiers = [
        "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
      ]
    }
    resources = [
      "*"
    ]
  }
}

# -- Providers

terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
      configuration_aliases = [
        aws.usw2,
        aws.use1
      ]
    }
  }
}

# -- Locals

locals {
  dnssec_alarms = {
    "dnssec_ksks_action_req" = {
      metric_name = "KSKActionRequired"
      desc        = join("",[
        "1+ DNSSEC KSKs require attention in <24h",
        var.dnssec_ksks_action_req_alarm_desc
      ])
    }
    "dnssec_ksk_age" = {
      statistic   = "Maximum"
      threshold   = var.dnssec_ksk_max_days * 24 * 60 * 60
      metric_name = "KSKAge"
      desc        = join("",[
        "1+ DNSSEC KSKs are >${var.dnssec_ksk_max_days} days old",
        var.dnssec_ksk_age_alarm_desc
      ])
    }
    "dnssec_errors" = {
      metric_name = "Errors"
      desc        = join("",[
        "DNSSEC encountered 1+ errors in <24h",
        var.dnssec_errors_alarm_desc
      ])
    }
  }
}

# -- Resources

resource "aws_kms_key" "dnssec" {
  for_each = var.dnssec_ksks
  provider = aws.use1

  customer_master_key_spec = "ECC_NIST_P256"
  deletion_window_in_days  = 7
  key_usage                = "SIGN_VERIFY"
  policy                   = data.aws_iam_policy_document.ksk_policy.json

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_kms_alias" "dnssec" {
  for_each = var.dnssec_ksks
  provider = aws.use1

  name          = "alias/${replace(var.dnssec_zone_name, "/\\./", "_")}-ksk-${each.key}"
  target_key_id = aws_kms_key.dnssec[each.key].key_id

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_route53_key_signing_key" "dnssec" {
  for_each = var.dnssec_ksks

  hosted_zone_id             = var.dnssec_zone_id
  key_management_service_arn = aws_kms_key.dnssec[each.key].arn
  name                       = "${var.dnssec_zone_name}-ksk-${each.key}"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_route53_hosted_zone_dnssec" "dnssec" {
  depends_on = [
    aws_route53_key_signing_key.dnssec
  ]
  hosted_zone_id = var.dnssec_zone_id

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_cloudwatch_metric_alarm" "dnssec" {
  for_each = local.dnssec_alarms

  alarm_name        = "${var.dnssec_zone_name}-${each.key}"
  alarm_description = each.value.desc
  namespace         = "AWS/Route53"
  metric_name       = "DNSSEC${each.value.metric_name}"

  dimensions = {
    HostedZoneId = var.dnssec_zone_id
  }

  statistic           = lookup(each.value, "statistic", "Sum")
  comparison_operator = "GreaterThanThreshold"
  threshold           = lookup(each.value, "threshold", 0)
  period              = 86400
  evaluation_periods  = 1
  alarm_actions       = var.alarm_actions
  ok_actions          = var.alarm_actions
}

