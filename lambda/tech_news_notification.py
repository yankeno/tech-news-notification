import hashlib
import json
import logging
import os
import posixpath
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse, urlunparse

import boto3
import feedparser
import requests

logger = logging.getLogger()
logger.setLevel("INFO")
ssm = boto3.client("ssm", region_name="ap-northeast-1")
dynamo = boto3.client("dynamodb", region_name="ap-northeast-1")
rss_urls: dict[str, str] = {
    "qiita": "https://qiita.com/popular-items/feed",
    "hatena": "http://b.hatena.ne.jp/hotentry/it.rss",
    "zenn": "https://zenn.dev/feed",
    "codezine": "https://codezine.jp/rss/new/20/index.xml",
    "developersio": "https://dev.classmethod.jp/feed",
}
today = date.today()
today_jp = f"{today.year}年{today.month}月{today.day}日"
header: dict[str, Any] = {
    "type": "header",
    "text": {
        "type": "plain_text",
        "text": f"{today_jp}の新着記事",
        "emoji": True,
    },
}
DIVIDER: dict[str, Any] = {"type": "divider"}
SLACK_PARAM = os.environ["SLACK_WEBHOOK_PARAM"]
DEDUP_TABLE = os.environ["DEDUP_TABLE_NAME"]
DEDUP_TTL = 3
MAX_PROCESS_ENTRIES_COUNT = 50
MAX_ENTRIES_COUNT = 10
FALLBACK_MESSAGE = {"text": f"{today_jp}の新着記事はありません"}
ERROR_MESSAGE = {"text": f"{today_jp}の記事取得でエラーが発生しました"}


def handler(event, context):
    logger.info("Starting handler")

    try:
        feeds = _get_feeds()
        message = _build_message(feeds)
    except Exception as e:
        logger.error(f"Error occurred: {e}", stack_info=True)
        message = ERROR_MESSAGE

    _notify_slack_webhook(message)
    logger.info("Finished handler")

    return {"message": "success"}


def _get_feeds() -> list[dict]:
    feeds = []
    for url in rss_urls.values():
        feed = _parse_feed(url)
        feeds.append(feed)

    return feeds


def _parse_feed(url: str) -> dict:
    feed = feedparser.parse(url)
    logger.info(f"Fetched feed from {url} with {len(feed.entries)} entries")

    feed_title: str = feed.feed.get("title")
    if not feed_title:
        logger.warning(f"Feed title not found for URL: {url}")

    entries = []
    for entry in feed.entries:
        title: str = entry.get("title")
        link: str = entry.get("link")
        published: str = entry.get("published") or entry.get("updated")
        entries.append({"title": title, "link": link, "published": published})

    return {"feed_title": feed_title, "entries": entries}


def _build_message(feeds: list[dict]) -> dict:
    blocks = []

    for feed in feeds:
        text = f"*{feed.get('feed_title')}*\n"
        entries = []  # メッセージに含める記事一覧
        processing_target_entries = feed.get("entries", [])[
            :MAX_PROCESS_ENTRIES_COUNT
        ]  # 処理対象にする記事一覧

        for entry in processing_target_entries:
            url = entry.get("link", "")
            if not _is_valid_url(url):
                continue

            normalized_url = _normalize_url(url)
            dedup_key = _make_dedup_key(normalized_url)
            upsert_item = _build_upsert_item(normalized_url, dedup_key)

            # 重複している場合はスキップ
            if _is_already_registered(upsert_item):
                continue

            entries.append(f"• <{normalized_url}|{entry.get('title')}>")

        # 最大値を超えている場合は最大値まで削除
        if len(entries) > MAX_ENTRIES_COUNT:
            del entries[MAX_ENTRIES_COUNT:]

        # entryが1つもなければセクションごと不要
        if len(entries) < 1:
            continue

        text += "".join(f"{entry}\n" for entry in entries)
        blocks.append(DIVIDER)
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})

    # 記事が何もない場合はフォールバックメッセージだけを送信
    if len(blocks) > 0:
        blocks.insert(0, header)
        return {"blocks": blocks}
    else:
        return FALLBACK_MESSAGE


def _is_valid_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all(
            [
                result.scheme in ("http", "https"),
                result.netloc,
            ]
        )
    except Exception:
        logger.warning(f"Invalid URL: {url}")
        return False


def _normalize_url(url: str) -> str:
    parsed_url = urlparse(url)

    scheme = parsed_url.scheme or "https"
    host = parsed_url.hostname.lower() if parsed_url.hostname else ""
    path = posixpath.normpath(parsed_url.path)

    if path != "/" and path.endswith("/"):
        path = path[:-1]

    return urlunparse(
        (
            scheme,
            host,
            path,
            "",  # params
            "",  # query
            "",  # fragment
        )
    )


def _make_dedup_key(url: str) -> str:
    return hashlib.sha256(url.encode("UTF-8")).hexdigest()


def _build_upsert_item(normalized_url: str, dedup_key: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    ttl = int((datetime.now(timezone.utc) + timedelta(days=DEDUP_TTL)).timestamp())
    return {
        "pk": {"S": f"URL#{dedup_key}"},
        "url": {"S": normalized_url},
        "ttl": {"N": str(ttl)},
        "created_at": {"S": str(now)},
    }


def _is_already_registered(item: dict) -> bool:
    try:
        dynamo.put_item(
            TableName=DEDUP_TABLE,
            Item=item,
            ConditionExpression="attribute_not_exists(#pk)",
            ExpressionAttributeNames={
                "#pk": "pk",
            },
        )
        return False
    except dynamo.exceptions.ConditionalCheckFailedException:
        logger.info(f"Duplicate URL found: {item['url']['S']}")
        return True
    except Exception as e:
        logger.error(f"Error during DynamoDB operation: {e}", stack_info=True)
        raise


def _get_slack_webhook_url() -> str:
    try:
        response = ssm.get_parameter(Name=SLACK_PARAM, WithDecryption=True)
        return response["Parameter"]["Value"]
    except Exception as e:
        logger.error(f"Error fetching {SLACK_PARAM} from SSM: {e}", stack_info=True)
        raise


def _notify_slack_webhook(message: dict) -> None:
    url = _get_slack_webhook_url()

    try:
        logger.info(f"Sending notification to Slack webhook: {json.dumps(message)}")
        r = requests.post(url, json=message, timeout=5)
        r.raise_for_status()
        logger.info(f"Notification sent to Slack successfully: {r.text}")
    except requests.RequestException as e:
        logger.error(f"Error sending notification to Slack: {e}", stack_info=True)
        raise
