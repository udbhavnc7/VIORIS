"""Email Connector — Gmail API wrapper with OAuth2."""

import base64
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Optional


@dataclass
class EmailMessage:
    id: str = ""
    thread_id: str = ""
    from_addr: str = ""
    to_addr: str = ""
    subject: str = ""
    body: str = ""
    snippet: str = ""
    is_read: bool = False
    has_attachments: bool = False
    date: str = ""
    labels: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "from": self.from_addr,
            "to": self.to_addr,
            "subject": self.subject,
            "body": self.body,
            "snippet": self.snippet,
            "is_read": self.is_read,
            "has_attachments": self.has_attachments,
            "date": self.date,
            "labels": self.labels,
        }


@dataclass
class DraftEmail:
    to: str
    subject: str
    body: str
    cc: str = ""
    bcc: str = ""
    reply_to_id: str = ""

    def to_mime(self) -> MIMEText:
        msg = MIMEText(self.body)
        msg["to"] = self.to
        msg["subject"] = self.subject
        if self.cc:
            msg["cc"] = self.cc
        return msg


class GmailConnector:
    """Gmail API wrapper — reads/sends via OAuth2 token."""

    SCOPES = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.modify",
    ]

    def __init__(self, credentials_path: str = "credentials.json", token_path: str = "token.json"):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self._service = None
        self._connected = False

    def connect(self) -> tuple[bool, str]:
        if not os.path.exists(self.credentials_path):
            return False, "credentials.json not found"

        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build

            creds = None
            if os.path.exists(self.token_path):
                creds = Credentials.from_authorized_user_file(self.token_path, self.SCOPES)

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, self.SCOPES)
                    creds = flow.run_local_server(port=0)

                Path(self.token_path).write_text(creds.to_json())

            self._service = build("gmail", "v1", credentials=creds)
            self._connected = True
            return True, "Connected"
        except Exception as e:
            return False, str(e)

    def is_connected(self) -> bool:
        return self._connected

    def list_messages(self, query: str = "", max_results: int = 10) -> list[EmailMessage]:
        if not self._connected:
            return []

        try:
            results = self._service.users().messages().list(
                userId="me", q=query, maxResults=max_results
            ).execute()
            messages = results.get("messages", [])

            emails = []
            for msg_ref in messages:
                msg = self._service.users().messages().get(
                    userId="me", id=msg_ref["id"], format="full"
                ).execute()
                emails.append(self._parse_message(msg))
            return emails
        except Exception:
            return []

    def get_message(self, message_id: str) -> Optional[EmailMessage]:
        if not self._connected:
            return None

        try:
            msg = self._service.users().messages().get(
                userId="me", id=message_id, format="full"
            ).execute()
            return self._parse_message(msg)
        except Exception:
            return None

    def send_message(self, draft: DraftEmail) -> tuple[bool, str]:
        if not self._connected:
            return False, "Not connected"

        try:
            mime_msg = draft.to_mime()
            raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode()
            body = {"raw": raw}

            if draft.reply_to_id:
                body["threadId"] = draft.reply_to_id

            result = self._service.users().messages().send(
                userId="me", body=body
            ).execute()
            return True, result.get("id", "")
        except Exception as e:
            return False, str(e)

    def mark_read(self, message_id: str) -> bool:
        if not self._connected:
            return False

        try:
            self._service.users().messages().modify(
                userId="me", id=message_id,
                body={"removeLabelIds": ["UNREAD"]}
            ).execute()
            return True
        except Exception:
            return False

    def get_unread_count(self) -> int:
        if not self._connected:
            return 0

        try:
            results = self._service.users().messages().list(
                userId="me", q="is:unread", maxResults=1
            ).execute()
            return results.get("resultSizeEstimate", 0)
        except Exception:
            return 0

    def _parse_message(self, msg: dict) -> EmailMessage:
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        labels = msg.get("labelIds", [])

        body = ""
        payload = msg.get("payload", {})
        if payload.get("mimeType") == "text/plain":
            body = base64.urlsafe_b64decode(payload.get("body", {}).get("data", "")).decode(errors="ignore")
        elif "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain":
                    body = base64.urlsafe_b64decode(part.get("body", {}).get("data", "")).decode(errors="ignore")
                    break

        return EmailMessage(
            id=msg.get("id", ""),
            thread_id=msg.get("threadId", ""),
            from_addr=headers.get("from", ""),
            to_addr=headers.get("to", ""),
            subject=headers.get("subject", ""),
            body=body,
            snippet=msg.get("snippet", ""),
            is_read="UNREAD" not in labels,
            has_attachments=any(
                p.get("filename") for p in payload.get("parts", [])
            ),
            date=headers.get("date", ""),
            labels=labels,
        )
