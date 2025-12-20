import * as cdk from "aws-cdk-lib/core";
import { Construct } from "constructs";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as ssm from "aws-cdk-lib/aws-ssm";

export class TechNewsNotificationStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const slackWebhookParams = new ssm.StringParameter(
      this,
      "SlackWebhookUrl",
      {
        parameterName: "/tech-news-notification/slack/webhook-url",
        stringValue: "REPLACE_ME",
      }
    );
  }
}
