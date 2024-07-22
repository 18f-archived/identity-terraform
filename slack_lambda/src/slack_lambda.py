#!/usr/bin/python3.9
import boto3
import urllib3
import json
import os
import re
import datetime
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)


class SlackNotificationFormatter:
    def __init__(
        self, event, default_slack_username, default_slack_icon, slack_channel
    ):
        self.event = event
        self.default_slack_username = default_slack_username
        self.default_slack_icon = default_slack_icon
        self.slack_channel = slack_channel

    def format_aws_health_event(self, data={}, slack_username="", slack_icon=""):
        details = data["detail"]
        blocks = [
            self.section(
                "\n".join(
                    [
                        "*AWS Health Event*",
                        f'*Service: {details["service"]}*',
                        f'Event Type: {details["eventTypeCode"]}',
                        f'Status: {details["statusCode"]}',
                    ]
                )
            )
        ]

        blocks.append(self.section(details["eventArn"]))
        blocks.append(
            self.section(
                "\n".join(
                    ["```", details["eventDescription"][0]["latestDescription"], "```"]
                )
            )
        )

        if len(data["resources"]) > 0:
            affected_resources = "Affected Resources:\n"

            for resource in data["resources"]:
                affected_resources = f"{resource}\n"

            blocks.append(self.section(affected_resources))

        blocks.append(
            self.section(
                "\n".join(
                    [
                        f"Account: {details['account']}",
                        f"Region: {details['eventRegion']}",
                    ]
                )
            )
        )

        try:
            iso_time = datetime.fromisoformat(data["time"])
            formatted_time = iso_time.strftime("%Y-%m-%d %H:%M:%S %Z")
        except:
            formatted_time = data["time"]

        time_information = f"Notification Time: {formatted_time}"

        if "startTime" in details:
            time_information += f"\nStart Time: {details['startTime']}"

        if "endTime" in details:
            time_information += f"\nEnd Time: {details['endTime']}"

        if "lastUpdatedTime" in details:
            time_information += f"\nLast Updated: {details['lastUpdatedTime']}"

        blocks.append(self.section(time_information))

        msgtext = "\n".join(
            [
                "*AWS Health Event*",
                f'*{details["service"]}*',
                f'Event Type: {details["eventTypeCode"]}',
                f'Status: {details["statusCode"]}',
                details["eventDescription"][0]["latestDescription"],
            ]
        )

        return self.compose_payload(
            text=msgtext,
            blocks=blocks,
            slack_username=slack_username,
            slack_icon=slack_icon,
        )

    def format_codebuild_message(self, data={}, slack_username="", slack_icon=""):
        msgtext = f'auto-terraform: {data["detail"]["pipeline"]} pipeline {data["detail"]["state"]} with execution ID {data["detail"]["execution-id"]}'

        return self.compose_payload(
            text=msgtext, slack_username=slack_username, slack_icon=slack_icon
        )

    def format_cloudwatch_alarm_message(
        self, data={}, slack_username="", slack_icon=""
    ):
        slackAlarmEmoji = os.environ["slack_alarm_emoji"]
        slackOkEmoji = os.environ["slack_ok_emoji"]

        if data["NewStateValue"] == "ALARM":
            alertState = f"{slackAlarmEmoji} *ALARM:* "
        else:
            alertState = f"{slackOkEmoji} *OK:* "

        blocks = [self.section(f'{alertState} *{data["AlarmName"]}*')]

        try:
            iso_time = datetime.fromisoformat(
                data["StateChangeTime"].replace("+0000", "+00:00")
            )
            formatted_time = iso_time.strftime("%Y-%m-%d %H:%M:%S %Z")
        except:
            formatted_time = data["StateChangeTime"]

        match = re.search("Runbook: (https://\\S+)", data["AlarmDescription"])
        if match:
            runbook_url = match.group(1)

            blocks[0]["accessory"] = self.runbook_button(runbook_url)

            description_no_runbook = re.sub(
                "Runbook: (https://\\S+)\n", "", data["AlarmDescription"]
            )
            blocks[0]["text"]["text"] += f"\n{description_no_runbook}"
        else:
            blocks.append(self.section(data["AlarmDescription"]))

        blocks.append(
            self.section(
                "\n".join(
                    [
                        data["NewStateReason"],
                        f"*Time*: {formatted_time}",
                        f'*Region*: {data["Region"]}',
                    ]
                )
            )
        )

        msgtext = "\n".join(
            [
                f'{alertState} *{data["AlarmName"]}*',
                data["AlarmDescription"],
                data["NewStateReason"],
                f"*Time*: {formatted_time}",
                f'*Region*: {data["Region"]}',
            ]
        )

        return self.compose_payload(
            text=msgtext,
            blocks=blocks,
            slack_username=slack_username,
            slack_icon=slack_icon,
        )

    def format_generic_slack_message(
        self, eventmsg="", slack_username="", slack_icon=""
    ):

        return self.compose_payload(
            text=eventmsg, slack_username=slack_username, slack_icon=slack_icon
        )

    def format_aws_incident_manager_message(
        self, data={}, slack_username="", slack_icon=""
    ):
        if data["IncidentManagerEvent"] == "ShiftChange":
            msgtext = f"**ON-CALL CHANGE:** {data['Details']['ContactName']} is {data['Details']['Status']} for the {data['Details']['RotationName']} rotation"
        elif data["IncidentManagerEvent"] == "IncidentOpened":
            msgtext = f"**INCIDENT OPENED:** {data['Details']['title']}"
        elif data["IncidentManagerEvent"] == "IncidentClosed":
            msgtext = f"**INCIDENT CLOSED:** {data['Details']['title']}"
        elif data["IncidentManagerEvent"] == "ResponderPaged":
            msgtext = f"**RESPONDER PAGED:** {data['Details']['contactArn'].split('/')[-1]}"
        elif data["IncidentManagerEvent"] == "ResponderAcknowledged":
            msgtext = f"**REPONDER ACKNOWLEDGED:** {data['Details']['contactArn'].split('/')[-1]}"

        return self.compose_payload(
            text=msgtext, slack_username=slack_username, slack_icon=slack_icon
        )

    def compose_payload(self, text="", blocks=None, slack_username="", slack_icon=""):
        msg = {
            "channel": self.slack_channel,
            "username": (
                slack_username if slack_username else self.default_slack_username
            ),
            "text": text,
            "icon_emoji": slack_icon if slack_icon else self.default_slack_icon,
        }

        if blocks:
            msg["blocks"] = blocks

        return msg

    def section(self, txt):
        return {"type": "section", "text": {"type": "mrkdwn", "text": txt}}

    def runbook_button(self, runbook_url):
        return {
            "type": "button",
            "text": {
                "type": "plain_text",
                "text": "View Runbook :books:",
                "emoji": True,
            },
            "value": "runbook-id",
            "url": runbook_url,
            "action_id": "button-action",
        }


def get_slack_message_payload(event):
    formatter = SlackNotificationFormatter(
        event=event,
        default_slack_username=os.environ["slack_username"],
        default_slack_icon=os.environ["slack_icon"],
        slack_channel=os.environ["slack_channel"],
    )

    eventmsg = event["Records"][0]["Sns"]["Message"]

    try:
        data = json.loads(eventmsg)

        if (
            "detail-type" in data
            and data["detail-type"] == "CodePipeline Pipeline Execution State Change"
        ):
            return formatter.format_codebuild_message(
                data,
                slack_username="AWS CodePipeline",
            )
        elif "AlarmName" in data and "AlarmDescription" in data:
            return formatter.format_cloudwatch_alarm_message(
                data,
                slack_username="AWS Cloudwatch Alarm",
                slack_icon=":aws:",
            )
        elif "source" in data and data["detail-type"] == "AWS Health Event":
            logger.info("health")
            return formatter.format_aws_health_event(
                data,
                slack_username="AWS Health Event",
                slack_icon=":aws:",
            )
        elif "IncidentManagerEvent" in data:
            return formatter.format_aws_incident_manager_message(
                data,
                slack_username="AWS Incident Manager",
            )
        else:
            logger.info("generic")
            return formatter.format_generic_slack_message(
                eventmsg,
            )

    except Exception as e:
        return formatter.format_generic_slack_message(
            eventmsg,
        )


def send_slack_notification(payload):
    ssm = boto3.client("ssm")
    slackUrlParam = os.environ["slack_webhook_url_parameter"]
    url = ssm.get_parameter(Name=slackUrlParam, WithDecryption=True)["Parameter"][
        "Value"
    ]

    http = urllib3.PoolManager()

    return http.request("POST", url, body=json.dumps(payload).encode("utf-8"))


def lambda_handler(event, context):
    """
    Lambda function to parse notification events and forward to Slack

    :param event: lambda expected event object
    :param context: lambda expected context object
    :returns: none
    """
    payload = get_slack_message_payload(event)

    response = send_slack_notification(payload)

    if json.loads(response)["code"] != 200:
        return logger.error(
            {
                "status_code": response.status,
                "response": response.data,
            }
        )
    else:
        return logger.info(
            {
                "message": event["Records"][0]["Sns"]["Message"],
                "slack_payload": payload,
                "status_code": response.status,
                "response": response.data,
            }
        )
