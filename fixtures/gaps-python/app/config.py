import os


class Settings:
    database_url = os.environ.get("DATABASE_URL", "sqlite:///./gaps.db")
    smtp_host = os.environ.get("SMTP_HOST", "smtp.mail-relay.example")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    mail_from = os.environ.get("MAIL_FROM", "welcome@gaps.example")


settings = Settings()
