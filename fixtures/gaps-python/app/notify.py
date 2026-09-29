import logging

import emails

from app.config import settings

logger = logging.getLogger("gaps.notify")


def send_account_email(email_to: str, password: str) -> None:
    message = emails.message.Message(
        subject="Your new account",
        html=f"<p>Your password is {password}</p>",
        mail_from=("Gaps", settings.mail_from),
    )
    smtp_options = {"host": settings.smtp_host, "port": settings.smtp_port}
    response = message.send(to=email_to, smtp=smtp_options)
    logger.info("send email result: %s", response)
