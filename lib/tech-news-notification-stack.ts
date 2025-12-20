import * as cdk from "aws-cdk-lib/core";
import { Construct } from "constructs";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as ssm from "aws-cdk-lib/aws-ssm";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";

export class TechNewsNotificationStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const slackWebhook = new ssm.StringParameter(this, "SlackWebhookUrl", {
      parameterName: "/tech-news-notification/slack/webhook-url",
      stringValue: "REPLACE_ME",
    });

    const dedupTable = new dynamodb.TableV2(this, "SlackNotifyDedup", {
      partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
      timeToLiveAttribute: "ttl",
      removalPolicy: cdk.RemovalPolicy.DESTROY, // 本番ではRETAIN(データがあればテーブルを削除しない)が推奨
    });

    const notifyLambda = new lambda.Function(this, "Notifier", {
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: "src.tech_news_notification.handler",
      code: lambda.Code.fromAsset("lambda"),
      timeout: cdk.Duration.seconds(30),
      environment: {
        DEDUP_TABLE_NAME: dedupTable.tableName,
        SLACK_WEBHOOL_PARAM: slackWebhook.parameterName,
      },
    });

    new events.Rule(this, "DailyTechNewsNotificationRule", {
      schedule: events.Schedule.cron({
        minute: "0",
        hour: "0", // UTCで00：00 -> JSTで09：00
      }),
      targets: [new targets.LambdaFunction(notifyLambda, { retryAttempts: 5 })],
    });

    dedupTable.grantWriteData(notifyLambda);
    slackWebhook.grantRead(notifyLambda);
  }
}
