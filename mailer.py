"""Gmail sender (SMTP + app password). The ONLY recipient is DEMO_EMAIL. No CC, no BCC.
Prospect email addresses are research data and can never be a delivery destination:
the recipient is not a parameter, it is read from .env inside this module."""
import os, smtplib
from email.message import EmailMessage
import settings as S

class MailerNotConfigured(Exception):
    pass

class MailerError(Exception):
    pass

def send_status():
    """Plain facts for the UI: can we send, and to whom (never shows the password)."""
    demo = S.env("DEMO_EMAIL")
    off = bool(os.environ.get("SPRINTO_DISABLE_SEND"))        # test-only switch: sending is impossible while set
    return {"demo_email_set": bool(demo), "password_set": bool(S.env("GMAIL_APP_PASSWORD")),
            "ready": bool(demo and S.env("GMAIL_APP_PASSWORD")) and not off, "to": demo}

def check_login():
    """Proves the Gmail login works. Sends nothing."""
    if os.environ.get("SPRINTO_DISABLE_SEND"):
        raise MailerNotConfigured("Sending is switched off for this test run.")
    pw = (S.env("GMAIL_APP_PASSWORD") or "").replace(" ", "")
    login = S.env("GMAIL_ADDRESS") or S.env("GMAIL_USER") or S.env("DEMO_EMAIL")
    if not pw or not login:
        raise MailerNotConfigured("GMAIL_APP_PASSWORD (or the Gmail address) is not set.")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(login, pw)
    except smtplib.SMTPAuthenticationError:
        raise MailerError("Gmail refused the login. A school Google Workspace account may block app passwords.")
    except (smtplib.SMTPException, OSError) as e:
        raise MailerError(f"Could not reach Gmail: {type(e).__name__}")
    return "Gmail login works. Nothing was sent."

def send_demo(subject, body):
    if os.environ.get("SPRINTO_DISABLE_SEND"):
        raise MailerNotConfigured("Sending is switched off for this test run (SPRINTO_DISABLE_SEND), so nothing was sent.")
    demo = S.env("DEMO_EMAIL")
    if not demo:
        raise MailerNotConfigured("DEMO_EMAIL is not set in .env.")
    pw = (S.env("GMAIL_APP_PASSWORD") or "").replace(" ", "")
    if not pw:
        raise MailerNotConfigured("GMAIL_APP_PASSWORD is not set in .env, so nothing was sent. "
                                  "The approved email is saved and can be sent once it is added.")
    login = S.env("GMAIL_ADDRESS") or S.env("GMAIL_USER") or demo     # the sending account; defaults to the demo address
    msg = EmailMessage()
    msg["To"] = demo
    msg["From"] = login
    msg["Subject"] = subject
    msg.set_content(body)
    # no Cc / Bcc header is ever set; the envelope has exactly one recipient
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(login, pw)
            s.send_message(msg, from_addr=login, to_addrs=[demo])
    except smtplib.SMTPAuthenticationError:
        raise MailerError("Gmail refused the login. If this is a school Google Workspace account, "
                          "the admin may block app passwords. Tell me and we will switch method.")
    except (smtplib.SMTPException, OSError) as e:
        raise MailerError(f"Gmail send failed: {type(e).__name__}")
    return demo
