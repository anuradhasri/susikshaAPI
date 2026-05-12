from email.message import EmailMessage

def build_password_reset_email(
    *,
    to_email: str,
    from_email: str,
    reset_url: str,
    expires_in_minutes: int,
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = "Reset your Sushiksha password"
    message["From"] = from_email
    message["To"] = to_email
    message.set_content(
        "We received a request to reset your Sushiksha password.\n\n"
        f"Use this link within {expires_in_minutes} minutes:\n"
        f"{reset_url}\n\n"
        "If you did not request this, you can ignore this email."
    )
    message.add_alternative(
        f"""
        <!doctype html>
        <html lang="en">
          <head>
            <meta charset="UTF-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1.0" />
          </head>
          <body style="margin: 0; padding: 0; background-color: #f0f4ff; font-family: 'Segoe UI', Georgia, serif;">
            <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #f0f4ff; padding: 40px 16px;">
              <tr>
                <td align="center">
                  <table width="560" cellpadding="0" cellspacing="0" style="max-width: 560px; width: 100%;">

                    <!-- Header bar -->
                    <tr>
                      <td style="background: linear-gradient(135deg, #1e3a8a 0%, #2563eb 60%, #3b82f6 100%);
                                 border-radius: 16px 16px 0 0;
                                 padding: 32px 40px 28px;
                                 text-align: center;">
                        <div style="display: inline-block;
                                    background: rgba(255,255,255,0.15);
                                    border: 1px solid rgba(255,255,255,0.3);
                                    border-radius: 50px;
                                    padding: 6px 20px;
                                    margin-bottom: 16px;">
                          <span style="color: #bfdbfe; font-size: 11px; font-weight: 700;
                                       letter-spacing: 2px; text-transform: uppercase;">
                            Sushiksha
                          </span>
                        </div>
                        <div style="width: 56px; height: 56px;
                                    background: rgba(255,255,255,0.2);
                                    border-radius: 16px;
                                    margin: 0 auto;
                                    display: flex; align-items: center; justify-content: center;
                                    font-size: 28px; line-height: 56px; text-align: center;">
                          🔐
                        </div>
                        <h1 style="margin: 16px 0 0; color: #ffffff;
                                   font-size: 26px; font-weight: 700; letter-spacing: -0.5px;">
                          Password Reset
                        </h1>
                        <p style="margin: 8px 0 0; color: #bfdbfe; font-size: 14px;">
                          We got your request — let's get you back in.
                        </p>
                      </td>
                    </tr>

                    <!-- Card body -->
                    <tr>
                      <td style="background: #ffffff;
                                 padding: 40px 40px 32px;
                                 border-left: 1px solid #e2e8f0;
                                 border-right: 1px solid #e2e8f0;">

                        <p style="margin: 0 0 20px; color: #334155; font-size: 15px; line-height: 1.6;">
                          Hi there,
                        </p>
                        <p style="margin: 0 0 28px; color: #334155; font-size: 15px; line-height: 1.6;">
                          We received a request to reset the password for your Sushiksha account.
                          Click the button below to choose a new one.
                        </p>

                        <!-- CTA button -->
                        <table cellpadding="0" cellspacing="0" width="100%">
                          <tr>
                            <td align="center" style="padding-bottom: 28px;">
                              <a href="{reset_url}"
                                 style="display: inline-block;
                                        background: linear-gradient(135deg, #1e3a8a, #2563eb);
                                        color: #ffffff;
                                        text-decoration: none;
                                        font-size: 15px;
                                        font-weight: 700;
                                        letter-spacing: 0.3px;
                                        padding: 14px 36px;
                                        border-radius: 10px;
                                        box-shadow: 0 4px 14px rgba(37, 99, 235, 0.4);">
                                Set New Password &rarr;
                              </a>
                            </td>
                          </tr>
                        </table>

                        <!-- Expiry notice -->
                        <table cellpadding="0" cellspacing="0" width="100%">
                          <tr>
                            <td style="background: #eff6ff;
                                       border-left: 4px solid #3b82f6;
                                       border-radius: 0 8px 8px 0;
                                       padding: 14px 16px;
                                       margin-bottom: 24px;">
                              <p style="margin: 0; color: #1d4ed8; font-size: 13px; font-weight: 600;">
                                ⏱ This link expires in {expires_in_minutes} minutes.
                              </p>
                            </td>
                          </tr>
                        </table>

                        <!-- Divider -->
                        <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 28px 0;" />

                        <!-- Fallback URL -->
                        <p style="margin: 0 0 6px; color: #64748b; font-size: 12px;">
                          Button not working? Paste this URL into your browser:
                        </p>
                        <p style="margin: 0; word-break: break-all;">
                          <a href="{reset_url}"
                             style="color: #2563eb; font-size: 12px; text-decoration: underline;">
                            {reset_url}
                          </a>
                        </p>
                      </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                      <td style="background: #f8fafc;
                                 border: 1px solid #e2e8f0;
                                 border-top: none;
                                 border-radius: 0 0 16px 16px;
                                 padding: 20px 40px;
                                 text-align: center;">
                        <p style="margin: 0 0 6px; color: #94a3b8; font-size: 12px; line-height: 1.6;">
                          Didn't request a password reset? No action needed — your account is safe.
                        </p>
                        <p style="margin: 0; color: #cbd5e1; font-size: 11px;">
                          &copy; Sushiksha &bull; Sent to {to_email}
                        </p>
                      </td>
                    </tr>

                  </table>
                </td>
              </tr>
            </table>
          </body>
        </html>
        """,
        subtype="html",
    )
    return message