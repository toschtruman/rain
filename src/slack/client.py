import os
from typing import Optional
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


class SlackClient:
    def __init__(self):
        token = os.environ.get("SLACK_BOT_TOKEN")
        if not token:
            raise RuntimeError("Missing env var: SLACK_BOT_TOKEN")
        self.client = WebClient(token=token)
        self.ops_channel = os.environ.get("SLACK_OPS_CHANNEL_ID", "#operations-channel")

    def post_message(self, text: str, channel: Optional[str] = None, blocks: Optional[list] = None) -> dict:
        channel = channel or self.ops_channel
        kwargs = {"channel": channel, "text": text}
        if blocks:
            kwargs["blocks"] = blocks
        try:
            return self.client.chat_postMessage(**kwargs)
        except SlackApiError as e:
            raise RuntimeError(f"Slack error: {e.response['error']}") from e

    def post_digest(self, digest_text: str) -> dict:
        return self.post_message(text=digest_text, channel=self.ops_channel)
