import os
import re
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
import brevo
from brevo.core.api_error import ApiError

load_dotenv()
app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)

MAX_PER_CALL = 500


def fill_template(text, recipient):
    if not text:
        return text
    text = re.sub(r"\{\{\s*firstname\s*\}\}", recipient.get("firstname") or "there", text, flags=re.IGNORECASE)
    text = re.sub(r"\{\{\s*email\s*\}\}", recipient.get("email") or "", text, flags=re.IGNORECASE)
    return text


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/send-batch", methods=["POST"])
def send_batch():
    api_key = os.environ.get("BREVO_API_KEY")
    if not api_key:
        return jsonify({"error": "BREVO_API_KEY is not set in Render environment variables."}), 500

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "Invalid JSON body."}), 400

    sender_name = payload.get("senderName")
    sender_email = payload.get("senderEmail")
    subject = payload.get("subject")
    html_body = payload.get("htmlBody")
    recipients = payload.get("recipients")
    attachment_payload = payload.get("attachment")

    if not sender_email or not subject or not html_body or not isinstance(recipients, list) or len(recipients) == 0:
        return jsonify({"error": "Missing senderEmail, subject, htmlBody, or recipients."}), 400

    if len(recipients) > MAX_PER_CALL:
        return jsonify({
            "error": f"This tool caps a single send at {MAX_PER_CALL} recipients. "
                     f"You sent {len(recipients)}. Split into another batch."
        }), 400

    message_versions = []
    for r in recipients:
        email = r.get("email")
        firstname = r.get("firstname")
        message_versions.append(
            brevo.SendTransacEmailRequestMessageVersionsItem(
                to=[brevo.SendTransacEmailRequestMessageVersionsItemToItem(email=email, name=firstname or None)],
                subject=fill_template(subject, r),
                html_content=fill_template(html_body, r),
            )
        )

    client = brevo.Brevo(api_key=api_key)

    send_kwargs = dict(
        sender=brevo.SendTransacEmailRequestSender(name=sender_name or sender_email, email=sender_email),
        subject=subject,
        html_content=html_body,
        message_versions=message_versions,
    )
    if attachment_payload:
        send_kwargs["attachment"] = [
            {"name": attachment_payload["name"], "content": attachment_payload["content"]}
        ]

    try:
        result = client.transactional_emails.send_transac_email(**send_kwargs)
    except ApiError as e:
        return jsonify({"error": "Brevo API error", "details": e.body}), e.status_code or 502
    except Exception as e:
        return jsonify({"error": "Request to Brevo failed", "details": str(e)}), 500


    message_ids = None
    try:
        message_ids = getattr(result, "message_ids", None) or getattr(result, "message_id", None)
    except Exception:
        pass

    return jsonify({
        "success": True,
        "sent": len(recipients),
        "messageIds": message_ids,
    }), 200


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
