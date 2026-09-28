import smtplib
from email.message import EmailMessage

from app.config import settings


def send_welcome(to_address: str, name: str) -> None:
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to_address
    message["Subject"] = "Welcome"
    message.set_content(f"Hello {name}, thanks for signing up.")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
        smtp.starttls()
        smtp.send_message(message)
