"""
Given SLIs that contain ratios of CloudWatch metrics, writes new metrics that
aggregate over window_days and calculate the ratios.
"""

from typing import Any, Dict, List, Optional, TypedDict, Union
import datetime
import json
import os
import boto3  # type: ignore
import botocore  # type: ignore


# ENVIRONMENT VARIABLES
# Namespace of resulting SLI metrics
SLI_NAMESPACE = os.getenv("SLI_NAMESPACE")
# Prefix (usually env name) of resulting metrics
SLI_PREFIX = os.getenv("SLI_PREFIX")
# JSON-encoded SLI config, generated by Terraform
SLIS = os.getenv("SLIS")
# If no window is specified, how long to calculate the SLI for
WINDOW_DAYS = int(os.getenv("WINDOW_DAYS"))


class Cloudwatch:
    """
    Cloudwatch contains a single Cloudwatch client so we don't have to
    reinitialize it or pass it as an argument everywhere.
    """

    cloudwatch_client = None

    @classmethod
    def client(cls):
        """
        client returns an initialized Cloudwatch client or creates a new one.
        """
        if cls.cloudwatch_client is None:
            cls.cloudwatch_client = boto3.client("cloudwatch")
        return cls.cloudwatch_client


class SingleMetric:
    """
    Holds what we need to query a single CloudWatch metric.

    Example in a Terraform SLI:

      numerator = {
        namespace   = "${var.env_name}/sli"
        metric_name = "InterestingUrisSuccess"
        dimensions = [
          {
            Name  = "Hostname"
            Value = var.env_name == "prod" ? "secure.${var.root_domain}" : "idp.${var.env_name}.${var.root_domain}"
          }
        ]
      }
    """

    def __init__(
        self,
        window_days: int,
        namespace: str,
        metric_name: str,
        dimensions: List,
        statistic: Optional[str] = None,
        extended_statistic: Optional[str] = None,
        multiplier: float = 1.0,
    ):
        self.namespace = namespace
        self.metric_name = metric_name
        self.dimensions = dimensions
        self.statistic = statistic
        self.extended_statistic = extended_statistic
        self.multiplier = float(multiplier)

        self.stat_args = {
            "StartTime": datetime.datetime.utcnow()
            - datetime.timedelta(days=window_days),
            "EndTime": datetime.datetime.utcnow(),
            "Period": window_days * 24 * 60 * 60,
        }
        if extended_statistic:
            self.stat_args["ExtendedStatistics"] = [extended_statistic]
        elif statistic:
            self.stat_args["Statistics"] = [statistic]
        else:
            self.statistic = "Sum"
            self.stat_args["Statistics"] = ["Sum"]

    def sum(self) -> float:
        """
        sum returns the sum of a single cloudwatch metric over window_days
        multiplied by the multiplier.
        """
        total = 0.0
        for datapoint in Cloudwatch.client().get_metric_statistics(
            Namespace=self.namespace,
            MetricName=self.metric_name,
            Dimensions=self.dimensions,
            **self.stat_args,
        )["Datapoints"]:
            total += self.extract_stat(datapoint)
        return total * self.multiplier

    def extract_stat(self, datapoint: Dict) -> float:
        """
        extract_stat takes a Cloudwatch datapoint and returns the numeric value
        """
        if self.extended_statistic:
            return datapoint["ExtendedStatistics"][self.extended_statistic]

        return datapoint[self.statistic]


class CompositeMetric:
    """
    Holds multiple SingleMetrics allowing for returning the sum of the metrics.
    """

    def __init__(self, window_days: int, metrics: List[Dict]):
        self.metrics = [SingleMetric(window_days=window_days, **m) for m in metrics]

    def sum(self) -> float:
        total = 0.0

        for m in self.metrics:
            total += m.sum()

        return total


class SLI:
    """
    SLI calculates an SLI definition as a ratio of good requests to
    total requests.

    Please note that we assume `numerator` and `denominator` were defined in
    Terraform, and parsed as JSON. So they lists of Dicts (for composite metrics).

    Example:

    interesting_availability = {
      window_days = 30
      numerator = {
        namespace   = "foo_env/sli"
        metric_name = "InterestingUrisSuccess"
        dimensions = [
          {
            Name  = "Hostname"
            Value = "foo"
          }
        ]
      }
      ...
    }
    """

    def __init__(
        self,
        numerator: List[Dict],
        denominator: List[Dict],
        window_days: int = WINDOW_DAYS,
    ):
        if window_days is None:
            window_days = WINDOW_DAYS
        self.numerator = CompositeMetric(window_days=window_days, metrics=numerator)
        self.denominator = CompositeMetric(window_days=window_days, metrics=denominator)

    def get_ratio(self) -> float:
        """
        get_ratio returns the sum of the numerator divided by the sum of the
        denominator, calculated over the last window_days.

        Can return ZeroDivisonError.
        """
        return self.numerator.sum() / self.denominator.sum()


def publish_slis(slis: Dict[str, SLI], sli_namespace: str, sli_prefix: str):
    """
    create_slis takes a dictionary of SLIs, gets their values and writes the
    associated Cloudwatch metrics.
    """
    for sli_name, sli in slis.items():
        try:
            value = sli.get_ratio()
        except ZeroDivisionError:
            print("x/0 error for %s" % sli_name)
            continue
        except (
            botocore.exceptions.ClientError,
            botocore.exceptions.BotoCoreError,
        ) as e:
            print("CloudWatch API error for %s: %s" % (sli_name, e))
            continue

        print("%s: %f" % (sli_name, value))
        metric_data = [
            {
                "MetricName": sli_prefix + "-" + sli_name,
                "Value": value,
            }
        ]
        Cloudwatch.client().put_metric_data(
            Namespace=sli_namespace,
            MetricData=metric_data,
        )


def parse_sli_json(sli_json: str) -> Dict[str, SLI]:
    """
    Takes JSON (usually generated by Terraform), initializes
    SLI objects from the resulting dictionaries, and returns a dictionary of
    SLI definitions.  If provided the optional window_days value defines the
    evaluation period for the generated SLI objects.  (Default: 30)
    """

    sli_configs = json.loads(sli_json)
    slis = {}
    # Since the SLIs were defined in Terraform and converted to JSON
    # dictionaries, we need to convert them to SLI objects.
    for sli_name, sli_config in sli_configs.items():
        try:
            slis[sli_name] = SLI(**sli_config)
        except (KeyError, TypeError, ValueError) as e:
            print(f"Skipping malformed SLI {sli_name}: {e}")

    return slis


def lambda_handler(event, context):
    # Check required environment variables here and try to help
    if SLI_NAMESPACE is None:
        raise RuntimeError("SLI_NAMESPACE not set in environment")
    if SLI_PREFIX is None:
        raise RuntimeError("SLI_PREFIX not set in environment")
    if SLIS is None:
        raise RuntimeError("SLIS definition JSON not set in environment")

    # Parse SLIs into SLI objects
    slis = parse_sli_json(sli_json=SLIS)

    # Write SLI metrics from the SLI definitions
    publish_slis(slis=slis, sli_namespace=SLI_NAMESPACE, sli_prefix=SLI_PREFIX)


def main():
    lambda_handler(None, None)


if __name__ == "__main__":
    main()
