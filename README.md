# Tech ニュース通知アプリ

## 環境構築手順

```bash
$ cdk deploy
$ aws ssm put-parameter \
  --name "/tech-news-notification/slack/webhook-url" \
  --type SecureString \
  --value "{ Slack Webhook URL }" \
  --overwrite
```
