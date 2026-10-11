"""Email notifications through an SMTP relay."""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import Runner, Settings
from .db import Database
from .stats import load_results

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, settings: Settings, db: Database, runners: list[Runner], templates_dir):
        self.settings = settings
        self.db = db
        self.runners = runners
        self.env = Environment(loader=FileSystemLoader(str(templates_dir)),
                               autoescape=select_autoescape(["html"]))

    @property
    def enabled(self) -> bool:
        return self.settings.email_enabled

    # ------------------------------------------------------------------
    def build_new_results(self, keys) -> EmailMessage:
        keys = set(map(tuple, keys))
        results = [r for r in load_results(self.db, self.runners)
                   if (r.athlete_id, r.event_slug, r.run_number) in keys]
        results.sort(key=lambda r: (r.run_date, r.runner), reverse=True)
        people = sorted({r.runner for r in results})
        if len(results) == 1:
            r = results[0]
            subject = f"parkrun: {r.runner} ran {r.time} at {r.event_name}"
            if r.is_pb:
                subject += " (new PB!)"
        else:
            subject = f"parkrun: {len(results)} new results for {', '.join(people)}"
        ctx = {"results": results, "base_url": self.settings.base_url}
        msg = self._message(subject)
        msg.set_content(self.env.get_template("email_results.txt").render(**ctx))
        msg.add_alternative(self.env.get_template("email_results.html").render(**ctx), subtype="html")
        return msg

    def send_new_results(self, keys) -> None:
        if not self.enabled:
            return
        self._send(self.build_new_results(keys))

    def send_failure_alert(self, failures: int, message: str) -> None:
        if not self.enabled:
            return
        msg = self._message(f"parkrun dashboard: {failures} fetches in a row have failed")
        msg.set_content(
            f"The parkrun dashboard couldn't fetch results {failures} times in a row.\n\n"
            f"Last error: {message}\n\n"
            "If parkrun is blocking requests, try FETCHER=browser, or import saved pages from the "
            f"dashboard's 'Import a saved page' option.\n\n{self.settings.base_url}\n")
        self._send(msg)

    def send_test(self) -> None:
        msg = self._message("parkrun dashboard: test email")
        msg.set_content(f"Email from the parkrun dashboard is working.\n\n{self.settings.base_url}\n")
        self._send(msg)

    # ------------------------------------------------------------------
    def _message(self, subject: str) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.settings.email_from
        msg["To"] = ", ".join(self.settings.email_to)
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain="parkrun-dashboard.local")
        return msg

    def _send(self, msg: EmailMessage) -> None:
        s = self.settings
        if s.smtp_ssl:
            server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=30,
                                      context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30)
        try:
            server.ehlo()
            if s.smtp_starttls and not s.smtp_ssl:
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if s.smtp_user:
                server.login(s.smtp_user, s.smtp_password)
            server.send_message(msg)
            log.info("Sent email: %s", msg["Subject"])
        finally:
            try:
                server.quit()
            except Exception:
                pass
