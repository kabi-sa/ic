"""
IC Events Approval Hub — e-mail composition + delivery.

Branding follows the official KABi guidelines:
  Mid Blue #216AB1 (primary) · Turquoise #1EAFD9 · Topaz #0EB3AE
  Meteorite #3D3185 (dark sections) · Soft Green #3BB691
Headings use Poppins (Arial fallback), body copy Calibri/Arial.

Delivery: messages are always stored in the outbox first. They are actually sent
over SMTP as soon as an administrator enables and configures SMTP in Settings.
"""

import base64
import html
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart

import db

# ---------------------------------------------------------------- KABi brand
BLUE = "#216AB1"     # KABi Mid Blue    — primary
TURQ = "#1EAFD9"     # KABi Turquoise   — secondary
TOPAZ = "#0EB3AE"    # KABi Topaz       — accent
METEOR = "#3D3185"   # KABi Meteorite   — dark sections
GREEN = "#3BB691"    # KABi Soft Green
IRIS = "#675EAA"
INK = "#000000"
MUTED = "#5a6577"
NL = chr(10)
LINE = "#e3e9f2"
SOFT = "#f4f8fc"
RED = "#c0392b"      # semantic only (rejection) — not a brand colour
AMBER = "#b8860b"

HEAD_FONT = "Poppins,Arial,Helvetica,sans-serif"
BODY_FONT = "Calibri,Arial,Helvetica,sans-serif"

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "assets", "kabi-logo-white.png")
LOGO_CID = "kabilogo"
_logo_cache = {}


def logo_bytes():
    if "raw" not in _logo_cache:
        try:
            with open(LOGO_PATH, "rb") as fh:
                _logo_cache["raw"] = fh.read()
        except OSError:
            _logo_cache["raw"] = None
    return _logo_cache["raw"]


def logo_data_uri():
    raw = logo_bytes()
    if not raw:
        return ""
    if "uri" not in _logo_cache:
        _logo_cache["uri"] = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    return _logo_cache["uri"]


def inline_logo(html_body):
    """Swap the CID reference for a data URI so the in-app preview renders."""
    return (html_body or "").replace("cid:" + LOGO_CID, logo_data_uri())


def esc(v):
    return html.escape(str(v if v is not None else ""))


def money(v, currency="SAR"):
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        v = 0.0
    return "{:,.2f} {}".format(v, currency)


def fmt_date(d):
    if not d:
        return "—"
    try:
        import datetime
        return datetime.datetime.strptime(str(d)[:10], "%Y-%m-%d").strftime("%d %b %Y")
    except Exception:
        return str(d)


# ------------------------------------------------------------------ layout
# Styled to match the KABi IC Announcement Generator: a teal gradient band with the
# centred logo and the title beneath it, a white rounded card, section blocks with a
# thick coloured edge, a dotted divider, and the Internal Communication sign-off.
BAND_FROM = "#12615f"     # deep aqua
BAND_TO = "#0EB3AE"       # topaz


def _block(heading, inner, accent=BLUE):
    """A light rounded panel with a thick coloured edge — the announcement look."""
    return ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
            'style="margin:0 0 14px 0;background:#f5f8fb;border-radius:10px;">'
            '<tr>'
            '<td width="5" style="background:%s;border-radius:10px 0 0 10px;font-size:0;line-height:0;">&nbsp;</td>'
            '<td style="padding:14px 18px;">'
            '%s%s'
            '</td></tr></table>'
            % (accent,
               ('<div style="font-family:%s;font-size:14.5px;font-weight:700;color:%s;margin:0 0 8px 0;">%s</div>'
                % (HEAD_FONT, accent, esc(heading))) if heading else "",
               inner))


def _divider():
    return ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
            'style="margin:22px 0;"><tr>'
            '<td style="border-bottom:1px solid %s;font-size:0;line-height:0;">&nbsp;</td>'
            '<td width="26" align="center" style="line-height:0;">'
            '<span style="display:inline-block;width:9px;height:9px;border-radius:50%%;'
            'background:%s;"></span></td>'
            '<td style="border-bottom:1px solid %s;font-size:0;line-height:0;">&nbsp;</td>'
            '</tr></table>' % (LINE, TOPAZ, LINE))


def _signoff():
    return ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="margin:26px 0 4px 0;">'
            '<tr><td align="left">'
            '<div style="width:64px;height:3px;background:%s;border-radius:3px;margin:0 0 14px 0;'
            'font-size:0;line-height:0;">&nbsp;</div>'
            '<div style="color:%s;font-size:13.5px;font-family:%s;">With kind regards,</div>'
            '<div style="color:%s;font-size:14.5px;font-weight:700;font-family:%s;margin-top:3px;">'
            'Internal Communication Team</div>'
            '</td></tr></table>' % (TOPAZ, MUTED, BODY_FONT, TOPAZ, HEAD_FONT))


def _shell(title, preheader, body, cta=None, accent=TOPAZ):
    button = ""
    if cta:
        button = """
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:6px 0 4px 0;">
        <tr><td align="center">
          <a href="{url}" style="display:inline-block;background:{accent};color:#ffffff;
             text-decoration:none;font-weight:700;font-size:15px;padding:14px 42px;
             border-radius:10px;font-family:{hf};">{label}</a>
          <div style="color:{muted};font-size:11.5px;margin-top:12px;font-family:{bf};">
            or paste this link into your browser:<br>
            <span style="color:{blue};word-break:break-all;">{url}</span></div>
        </td></tr></table>""".format(url=esc(cta[1]), label=esc(cta[0]), accent=accent,
                                     hf=HEAD_FONT, bf=BODY_FONT, muted=MUTED, blue=BLUE)

    logo = ('<img src="cid:%s" width="168" alt="KABi" '
            'style="display:block;border:0;height:auto;max-width:168px;margin:0 auto;">' % LOGO_CID) \
        if logo_bytes() else \
        ('<div style="color:#fff;font-family:%s;font-weight:700;font-size:24px;'
         'letter-spacing:4px;">KABi</div>' % HEAD_FONT)

    return """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title></head>
<body style="margin:0;padding:0;background:{soft};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{pre}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{soft};padding:30px 12px;">
<tr><td align="center">
  <table role="presentation" width="640" cellpadding="0" cellspacing="0"
         style="width:640px;max-width:100%;background:#ffffff;border-radius:16px;
                overflow:hidden;font-family:{bf};border:1px solid {line};">

    <!-- gradient band: centred logo + title, as in the IC announcement template -->
    <tr><td align="center" background="{band_to}"
            style="background:{band_to};background-image:linear-gradient(120deg,{band_from},{band_to});
                   padding:30px 30px 26px 30px;">
      {logo}
      <div style="font-family:{hf};font-size:23px;font-weight:700;color:#ffffff;
                  margin:14px 0 0 0;line-height:1.3;">{title}</div>
      <div style="color:#c9efee;font-size:11.5px;font-family:{bf};margin-top:6px;
                  letter-spacing:1.4px;text-transform:uppercase;">Internal Communication</div>
    </td></tr>

    <tr><td style="padding:28px 30px 22px 30px;">
      {body}
      {button}
      {signoff}
    </td></tr>
    <tr><td style="background:{soft};border-top:1px solid {line};padding:18px 30px;">
      <div style="color:{muted};font-size:11.5px;line-height:1.6;font-family:{bf};">
        Automated message from the KABi IC Events Approval Hub. Please do not reply to this e-mail.<br>
        All decisions and their reasons are stored permanently in the event's approval history.
      </div>
    </td></tr>
  </table>
</td></tr></table></body></html>""".format(
        title=esc(title), pre=esc(preheader), body=body, button=button, logo=logo,
        signoff=_signoff(), band_from=BAND_FROM, band_to=BAND_TO,
        blue=BLUE, turq=TURQ, topaz=TOPAZ, green=GREEN, soft=SOFT, line=LINE,
        muted=MUTED, hf=HEAD_FONT, bf=BODY_FONT)


# ---------------------------------------------------------- plain text twin
# Every template produces an HTML version (for real sending / preview) and a
# plain-text version built from the SAME data, used for `mailto:` links so the
# message opens pre-filled in Outlook.
_TAG = __import__("re").compile(r"<[^>]+>")


def plain(v):
    """Strip the small amount of inline markup we put inside row values."""
    t = _TAG.sub("", str(v if v is not None else ""))
    return html.unescape(t).replace("\xa0", " ").strip()


def _t_rows(pairs):
    width = max((len(plain(k)) for k, _ in pairs), default=0)
    return "\n".join("%-*s : %s" % (width, plain(k), plain(v)) for k, v in pairs)


def _t_head(text):
    return "\n%s\n%s" % (text.upper(), "-" * len(text))


def _t_vendors(vendors, currency, show_status=False):
    out = []
    for v in vendors:
        line = "  - %s (%s) — %s" % (v["vendor_name"], v["category"] or "uncategorised",
                                     money(v["total_amount"], currency))
        if show_status:
            line += "  [%s]" % (v["approval_status"] or "pending").upper()
        out.append(line)
        if show_status and (v["approval_status"] or "") == "rejected" and v["rejection_reason"]:
            out.append("      Rejection reason: %s" % v["rejection_reason"])
    return "\n".join(out) or "  (none)"


def _t_shell(title, body_lines, cta=None):
    parts = ["KABi · IC EVENTS APPROVAL HUB", "=" * 30, "", title, "=" * len(title), ""]
    parts += [l for l in body_lines if l is not None]
    if cta:
        parts += ["", "%s:" % cta[0], cta[1]]
    parts += ["", "-" * 60,
              "Automated message from the KABi IC Events Approval Hub.",
              "All decisions and their reasons are stored in the event's approval history."]
    return "\n".join(parts)


def _rows(pairs):
    out = ['<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
           'style="border-collapse:collapse;margin:0;">']
    for i, (k, v) in enumerate(pairs):
        last = i == len(pairs) - 1
        border = "" if last else "border-bottom:1px solid #e6ecf3;"
        out.append(
            '<tr>'
            '<td style="padding:7px 0;%s color:%s;font-size:12.5px;'
            'width:185px;vertical-align:top;font-family:%s;">%s</td>'
            '<td style="padding:7px 0;%s color:%s;font-size:13.5px;'
            'font-weight:600;vertical-align:top;font-family:%s;">%s</td></tr>'
            % (border, MUTED, BODY_FONT, esc(k), border, INK, BODY_FONT,
               v if str(v).startswith("<") else esc(v))
        )
    out.append("</table>")
    return "".join(out)


def _keys(row):
    """The field names of a row, whether it is a dict or a database row."""
    try:
        return row.keys()
    except AttributeError:
        return ()


def _vendor_table(vendors, currency, show_status=False):
    head = ('<tr style="background:%s;">'
            '<th align="left" style="padding:10px 12px;font-size:11.5px;color:#ffffff;font-family:%s;">VENDOR</th>'
            '<th align="left" style="padding:10px 12px;font-size:11.5px;color:#ffffff;font-family:%s;">CATEGORY</th>'
            '<th align="right" style="padding:10px 12px;font-size:11.5px;color:#ffffff;font-family:%s;">PRICE / UNIT</th>'
            '<th align="right" style="padding:10px 12px;font-size:11.5px;color:#ffffff;font-family:%s;">QTY</th>'
            '<th align="right" style="padding:10px 12px;font-size:11.5px;color:#ffffff;font-family:%s;">TOTAL</th>'
            % (BLUE, BODY_FONT, BODY_FONT, BODY_FONT, BODY_FONT, BODY_FONT))
    if show_status:
        head += ('<th align="center" style="padding:10px 12px;font-size:11.5px;color:#ffffff;'
                 'font-family:%s;">DECISION</th>' % BODY_FONT)
    head += "</tr>"

    body = []
    for i, v in enumerate(vendors):
        bg = "#ffffff" if i % 2 == 0 else SOFT
        # A quotation is "N of something at X each". Showing only the sum hid the two
        # figures an approver actually compares between vendors.
        qty = v["quantity"] if "quantity" in _keys(v) and v["quantity"] else 1
        unit = v["unit_price"] if "unit_price" in _keys(v) and v["unit_price"] is not None \
            else v["quotation_amount"]
        row = ('<tr style="background:%s;">'
               '<td style="padding:10px 12px;font-size:13px;color:%s;border-bottom:1px solid %s;font-family:%s;">%s</td>'
               '<td style="padding:10px 12px;font-size:12.5px;color:%s;border-bottom:1px solid %s;font-family:%s;">%s</td>'
               '<td align="right" style="padding:10px 12px;font-size:12.5px;color:%s;'
               'border-bottom:1px solid %s;font-family:%s;">%s</td>'
               '<td align="right" style="padding:10px 12px;font-size:12.5px;color:%s;'
               'border-bottom:1px solid %s;font-family:%s;">%s</td>'
               '<td align="right" style="padding:10px 12px;font-size:13px;color:%s;font-weight:700;'
               'border-bottom:1px solid %s;font-family:%s;">%s</td>'
               % (bg, INK, LINE, BODY_FONT, esc(v["vendor_name"]),
                  MUTED, LINE, BODY_FONT, esc(v["category"] or "—"),
                  MUTED, LINE, BODY_FONT, esc(money(unit, currency)),
                  MUTED, LINE, BODY_FONT, esc("%g" % float(qty)),
                  INK, LINE, BODY_FONT, esc(money(v["total_amount"], currency))))
        if show_status:
            st = (v["approval_status"] or "pending").lower()
            color = {"approved": GREEN, "rejected": RED, "not_selected": MUTED}.get(st, AMBER)
            row += ('<td align="center" style="padding:10px 12px;border-bottom:1px solid %s;">'
                    '<span style="display:inline-block;background:%s;color:#fff;font-size:11px;'
                    'font-weight:700;padding:4px 12px;border-radius:20px;font-family:%s;">%s</span></td>'
                    % (LINE, color, BODY_FONT,
                       esc("Not selected" if st == "not_selected" else st.title())))
        row += "</tr>"
        body.append(row)
        if show_status and (v["approval_status"] or "") == "rejected" and v["rejection_reason"]:
            span = 4 if show_status else 3
            body.append(
                '<tr><td colspan="%d" style="padding:0 12px 10px 12px;border-bottom:1px solid %s;background:%s;">'
                '<div style="background:#fdf1ef;border-left:3px solid %s;padding:9px 12px;'
                'border-radius:0 6px 6px 0;color:#8c2f22;font-size:12.5px;font-family:%s;">'
                '<b>Rejection reason:</b> %s</div></td></tr>'
                % (span, LINE, bg, RED, BODY_FONT, esc(v["rejection_reason"])))

    return ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
            'style="border-collapse:collapse;border:1px solid %s;border-radius:8px;'
            'overflow:hidden;margin:0 0 20px 0;">%s%s</table>' % (LINE, head, "".join(body)))


def _callout(text, color, bg):
    return ('<div style="background:%s;border-left:4px solid %s;padding:13px 16px;'
            'border-radius:0 8px 8px 0;color:%s;font-size:13.5px;line-height:1.6;'
            'margin:0 0 20px 0;font-family:%s;">%s</div>' % (bg, color, INK, BODY_FONT, text))


def _h(text):
    return ('<div style="font-family:%s;font-size:14px;font-weight:700;color:%s;margin:0 0 10px 0;">%s</div>'
            % (HEAD_FONT, BLUE, esc(text)))


def _p(text):
    return ('<p style="color:%s;font-size:13.5px;line-height:1.7;margin:0 0 14px 0;font-family:%s;">%s</p>'
            % (MUTED, BODY_FONT, text))


def _p_tight(text):
    return ('<p style="color:%s;font-size:13px;line-height:1.65;margin:0 0 8px 0;font-family:%s;">%s</p>'
            % (MUTED, BODY_FONT, text))


# ------------------------------------------------------------------ e-mails
def _option_block(opt, currency, multi, _unused=None):
    """One package: its own vendor table and its own total.

    With a single option there is nothing to choose between, so the option header is
    dropped and only the vendor table plus the budget line are shown.
    """
    breakdown = ('<div style="padding:%s;color:%s;font-size:12px;font-family:%s;">'
                 '%d vendor(s) %s + delivery %s = <b style="color:%s;">%s</b></div>'
                 % ("2px 14px 10px 14px" if multi else "0 0 18px 0", MUTED, BODY_FONT,
                    len(opt["vendors"]), esc(money(opt["vendor_cost"], currency)),
                    esc(money(opt["delivery_cost"] if "delivery_cost" in _keys(opt) else 0,
                              currency)),
                    BLUE, esc(money(opt["total"], currency))))
    if not multi:
        return _vendor_table(opt["vendors"], currency) + breakdown

    head = ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
            'style="background:%s;border-radius:8px 8px 0 0;"><tr>'
            '<td style="padding:9px 14px;color:#fff;font-family:%s;font-weight:700;font-size:13.5px;">%s</td>'
            '<td align="right" style="padding:9px 14px;color:#fff;font-family:%s;font-weight:700;'
            'font-size:14px;">%s</td></tr></table>'
            % (METEOR, HEAD_FONT, esc(opt["name"]), BODY_FONT, esc(money(opt["total"], currency))))
    desc = ('<div style="padding:9px 14px 0 14px;color:%s;font-size:12.5px;font-family:%s;">%s</div>'
            % (MUTED, BODY_FONT, esc(opt["description"]))) if opt.get("description") else ""
    return ('<div style="border:1px solid %s;border-radius:8px;overflow:hidden;margin:0 0 18px 0;">'
            '%s%s<div style="padding:10px 12px 0 12px;">%s</div>%s</div>'
            % (LINE, head, desc, _vendor_table(opt["vendors"], currency), breakdown))


def _ordinal(n):
    n = int(n or 1)
    return "%d%s" % (n, {1: "st", 2: "nd", 3: "rd"}.get(n if n < 20 else n % 10, "th"))


def _signed_block(trail, heading="Approval chain"):
    """Every approver who signed, in order — the audit trail on a closing message."""
    if not trail:
        return ""
    out = []
    for i, t in enumerate(trail):
        edge = "" if i == len(trail) - 1 else "border-bottom:1px solid %s;" % LINE
        out.append(
            '<tr>'
            '<td style="padding:7px 10px;%sfont-family:%s;font-size:12px;color:%s;'
            'white-space:nowrap;">%s approver</td>'
            '<td style="padding:7px 10px;%sfont-family:%s;font-size:13px;color:%s;'
            'font-weight:600;">%s%s</td>'
            '<td align="right" style="padding:7px 10px;%sfont-family:%s;font-size:12px;color:%s;'
            'white-space:nowrap;">%s</td>'
            '</tr>'
            % (edge, BODY_FONT, MUTED, esc(_ordinal(t.get("level"))),
               edge, BODY_FONT, INK, esc(t.get("name") or "—"),
               (' <span style="color:%s;font-weight:400;">· %s</span>' % (MUTED, esc(t["job_title"]))
                if t.get("job_title") else ""),
               edge, BODY_FONT, MUTED,
               esc(fmt_date(t["decided_at"])) if t.get("decided_at") else "—"))
    return _block(heading,
                  '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0">%s</table>'
                  % "".join(out), METEOR)


def _t_signed(trail, heading="Approval chain"):
    if not trail:
        return ""
    out = [_t_head(heading)]
    for t in trail:
        out.append("  %-14s %-28s %s"
                   % (_ordinal(t.get("level")) + " approver", (t.get("name") or "-")[:28],
                      fmt_date(t["decided_at"]) if t.get("decided_at") else "-"))
    return NL.join(out)


def _trail_block(trail, level, is_final):
    """The approval chain so far, and where the request is standing right now.

    Each level that has signed off is shown with the name of the person who did it,
    so the approver reading this can see exactly whose judgement they are building on.
    """
    lines = []
    for t in trail:
        lines.append(
            '<tr>'
            '<td style="padding:7px 10px;border-bottom:1px solid %s;font-family:%s;font-size:12px;'
            'color:%s;white-space:nowrap;">%s approver</td>'
            '<td style="padding:7px 10px;border-bottom:1px solid %s;font-family:%s;font-size:13px;'
            'color:%s;font-weight:600;">%s%s</td>'
            '<td align="right" style="padding:7px 10px;border-bottom:1px solid %s;font-family:%s;'
            'font-size:12px;color:%s;font-weight:700;white-space:nowrap;">Approved%s%s</td>'
            '</tr>'
            % (LINE, BODY_FONT, MUTED, esc(_ordinal(t.get("level"))),
               LINE, BODY_FONT, INK, esc(t.get("name") or "—"),
               (' <span style="color:%s;font-weight:400;">· %s</span>' % (MUTED, esc(t["job_title"]))
                if t.get("job_title") else ""),
               LINE, BODY_FONT, GREEN,
               (' <span style="color:%s;font-weight:400;">%s</span>'
                % (MUTED, esc(fmt_date(t["decided_at"]))) if t.get("decided_at") else ""),
               # Which package they picked. Without it the reader knows an approval
               # happened but not what was approved, which is the thing they are being
               # asked to agree with or overturn.
               ('<div style="color:%s;font-weight:600;font-size:11.5px;margin-top:2px;">'
                'chose %s</div>' % (BLUE, esc(t["option_name"]))
                if t.get("option_name") else "")))

    lines.append(
        '<tr>'
        '<td style="padding:7px 10px;font-family:%s;font-size:12px;color:%s;white-space:nowrap;">'
        '%s approver</td>'
        '<td style="padding:7px 10px;font-family:%s;font-size:13px;color:%s;font-weight:700;">You</td>'
        '<td align="right" style="padding:7px 10px;font-family:%s;font-size:12px;color:%s;'
        'font-weight:700;white-space:nowrap;">%s</td>'
        '</tr>'
        % (BODY_FONT, MUTED, esc(_ordinal(level)), BODY_FONT, INK, BODY_FONT, METEOR,
           "Final decision awaited" if is_final else "Awaiting your decision"))

    return _block("Approval progress",
                  '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0">%s</table>'
                  % "".join(lines), METEOR)


def _t_trail(trail, level, is_final):
    out = [_t_head("Approval progress")]
    for t in trail:
        out.append("  %-14s %-28s Approved%s%s"
                   % (_ordinal(t.get("level")) + " approver", (t.get("name") or "-")[:28],
                      (" " + fmt_date(t["decided_at"])) if t.get("decided_at") else "",
                      ("  [chose %s]" % t["option_name"]) if t.get("option_name") else ""))
    out.append("  %-14s %-28s %s"
               % (_ordinal(level) + " approver", "YOU",
                  "FINAL DECISION AWAITED" if is_final else "AWAITING YOUR DECISION"))
    return NL.join(out)


def build_approval_request(ev, options, creator, approver, base_url, currency, review_url=None,
                           all_approvers=None, level=None, note=None, trail=None,
                           is_final=False, chosen_name=None):
    """E-mail 1 — the request for a decision.

    Sent when an event is first submitted, and again at each step of the approval
    chain. `trail` carries the approvers who have already signed off, so a second or
    third approver opens the message already knowing whose approval preceded theirs;
    `is_final` says their approval closes the chain rather than passing it on.
    """
    trail = list(trail or [])
    closing = is_final and bool(trail)          # the last word on a request already in motion
    if closing:
        subject = "Action Required: Final Approval – %s" % ev["event_name"]
    elif trail:
        subject = "Action Required: Your Approval – %s" % ev["event_name"]
    else:
        subject = "Action Required: Event Approval – %s" % ev["event_name"]
    # Every option is shown to every approver. Narrowing the list to whatever the first
    # approver picked made the decision for everyone after them: a second approver could
    # only ratify, never disagree, and the alternative they might have preferred was not
    # even in the message. What an earlier approver chose is said plainly instead.
    multi = len(options) > 1

    # With one package the event total is that package. With several it is whichever one
    # is currently selected -- so naming options[0] here attached a real number to an
    # arbitrary name. Say which package the figure belongs to, or give the range and let
    # the option blocks below carry the detail.
    if not multi:
        budget_label, budget_value = "Estimated Budget", money(ev["total_budget"], currency)
    elif chosen_name:
        budget_label = "Estimated Budget (%s)" % chosen_name
        budget_value = money(ev["total_budget"], currency)
    else:
        budget_label = "Estimated Budget"
        totals = sorted(float(o["total"] or 0) for o in options)
        budget_value = "%s – %s across %d options" % (
            money(totals[0], currency), money(totals[-1], currency), len(options))
    pairs = [
        ("Event ID", ev["event_number"]),
        ("Event Name", ev["event_name"]),
        ("Event Type", ev["event_type"] or "—"),
        ("Event Date", "%s%s" % (fmt_date(ev["event_date"]),
                                 (" · %s – %s" % (ev["start_time"] or "", ev["end_time"] or ""))
                                 if ev["start_time"] else "")),
        ("Location", ev["location"] or "—"),
        ("Expected Attendees", ev["expected_attendees"] or "—"),
        ("Requested by", "%s (%s)" % (creator["name"], creator["email"])),
        ("Description", ev["description"] or "—"),
        (budget_label, budget_value),
    ]
    html_pairs = pairs[:-1] + [(budget_label,
                                '<span style="color:%s;font-size:16px;">%s</span>'
                                % (BLUE, esc(budget_value)))]
    role_line = (" as the <b>%s approver</b>" % _ordinal(level)) if level else ""
    signed = ", ".join("<b>%s</b> (%s approver)" % (esc(t.get("name") or "—"), esc(_ordinal(t.get("level"))))
                       for t in trail)
    if closing:
        intro = _p("This event has been approved by %s and now awaits your <b>final decision</b>. "
                   "Your approval closes the approval chain and confirms the event." % signed)
    elif trail:
        intro = _p("This event has been approved by %s and is now with you%s. Please review the "
                   "details below and record your decision." % (signed, role_line))
    else:
        intro = _p("A new Internal Communication event has been submitted for your approval by "
                   "<b>%s</b>%s. Please review the details below and record your decision."
                   % (esc(creator["name"]), role_line))
    body = [_p("Dear <b>%s</b>," % esc(approver["name"])), intro, _divider()]
    if trail:
        body.append(_trail_block(trail, level, closing))
    if is_final:
        body.append(_callout(
            "<b>You are the final approver.</b> Once you approve, the event is confirmed and no further "
            "approval is requested.", METEOR, "#eeecf7"))
    if note:
        body.append(_callout(esc(note), GREEN, "#ecf8f4"))
    body.append(_block("Event details", _rows(html_pairs), BLUE))
    picked = [t for t in trail if t.get("option_name")]
    if picked:
        body.append(_callout(
            "%s. You can approve the same package or a different one — <b>your choice is the "
            "one that stands</b>, and all %d options are shown below."
            % (", ".join("<b>%s</b> chose <b>%s</b>" % (esc(t["name"]), esc(t["option_name"]))
                         for t in picked), len(options)), TURQ, "#e9f5fc"))
    elif chosen_name and multi:
        body.append(_callout(
            "<b>%s</b> is the package selected so far. You can approve it or choose another — "
            "all %d options are shown below." % (esc(chosen_name), len(options)), TURQ, "#e9f5fc"))
    others = [a for a in (all_approvers or []) if a["id"] != approver["id"]]
    if others:
        body.append(_callout(
            "This request also went to <b>%s</b> at the same level — whoever responds first passes it on."
            % esc(", ".join(a["name"] for a in others)), TURQ, "#e9f5fc"))
    if multi:
        body.append(_callout(
            "This request contains <b>%d alternative options</b>. Each one is a complete package with its "
            "own vendors and its own total — please approve <b>one</b> of them." % len(options),
            METEOR, "#eeecf7"))
    vendor_inner = "".join(_option_block(opt, currency, multi) for opt in options)
    body.append(_block("Options and vendors" if multi else "Vendors (%d)" % len(options[0]["vendors"]),
                       vendor_inner, TOPAZ))
    body.append(_block("Your final decision" if closing else "What we need from you",
                       _p_tight("Select the vendors you approve, then confirm with a single "
                                "<b>Approve</b>. To turn the request down, use <b>Reject</b> — a written "
                                "reason is mandatory.")
                       + _p_tight("<b>No password needed</b> — the button below opens the approval page; "
                                  "just confirm your name and this e-mail address. The link is personal to "
                                  "you, works for this event only, and expires in 45 days."),
                       GREEN))
    url = review_url or ("%s/#/events/%d/approve" % (base_url.rstrip("/"), ev["id"]))
    cta_colour = TOPAZ

    signed_t = ", ".join("%s (%s approver)" % (t.get("name") or "-", _ordinal(t.get("level")))
                         for t in trail)
    if closing:
        intro_t = ("This event has been approved by %s and now awaits your FINAL DECISION. "
                   "Your approval closes the approval chain and confirms the event." % signed_t)
    elif trail:
        intro_t = ("This event has been approved by %s and is now with you%s."
                   % (signed_t, (" as the %s approver" % _ordinal(level)) if level else ""))
    else:
        intro_t = ("A new Internal Communication event has been submitted for your approval by %s%s."
                   % (creator["name"], (" as the %s approver" % _ordinal(level)) if level else ""))
    lines = ["Dear %s," % approver["name"], "", intro_t, ""]
    if trail:
        lines += [_t_trail(trail, level, closing), ""]
    if is_final:
        lines += ["YOU ARE THE FINAL APPROVER — once you approve, the event is confirmed and no "
                  "further approval is requested.", ""]
    lines += [(note or None), "",
              "Select the vendors you approve, then confirm with a single Approve. To turn the "
              "request down, use Reject — a written reason is mandatory.", "", _t_rows(pairs)]
    if chosen_name:
        lines += ["", "%s is the package selected so far. You may approve it or choose a "
                      "different one - your choice is the one that stands, and every option "
                      "is listed below." % chosen_name]
    if others:
        lines += ["", "Also at this level: %s — whoever responds first passes it on."
                  % ", ".join(a["name"] for a in others)]
    if multi:
        lines += ["", "This request contains %d ALTERNATIVE OPTIONS — each is a complete package "
                      "with its own total. Please approve ONE of them." % len(options)]
    for opt in options:
        lines += [_t_head("%s — %s" % (opt["name"], money(opt["total"], currency))) if multi
                  else _t_head("Vendors (%d)" % len(opt["vendors"]))]
        if opt.get("description"):
            lines.append(opt["description"])
        lines.append(_t_vendors(opt["vendors"], currency))
        lines.append("  %d vendor(s) %s + delivery %s = %s"
                     % (len(opt["vendors"]), money(opt["vendor_cost"], currency),
                        money(opt["delivery_cost"] if "delivery_cost" in _keys(opt) else 0,
                              currency),
                        money(opt["total"], currency)))
    lines += ["", "Select the vendors you approve, then confirm with a single Approve.",
              "A written reason is mandatory for every rejection.", "",
              "No password needed — the link below opens the approval page; just confirm your name "
              "and this e-mail address."]
    heading = "Final approval required" if closing else "Event approval request"
    text = _t_shell(heading, lines, ("REVIEW EVENT", url))

    return subject, _shell(heading, subject, "".join(body),
                           ("Give Final Approval" if closing else "Review Event", url),
                           cta_colour), text


def _chosen_row(option_name):
    """Only meaningful when the request offered more than one option."""
    return [("Approved Option", '<span style="color:%s;">%s</span>' % (METEOR, esc(option_name)))] \
        if option_name else []


def build_fully_approved(ev, vendors, approver, base_url, currency, option_name=None,
                         creator=None, trail=None):
    """E-mail 2 — event and every vendor approved."""
    subject = "Event Approved – %s" % ev["event_name"]
    approved = [v for v in vendors if v["approval_status"] == db.VS_APPROVED]
    pairs = [
        ("Event ID", ev["event_number"]),
        ("Event Name", ev["event_name"]),
        ("Event Date", fmt_date(ev["event_date"])),
        ("Location", ev["location"] or "—"),
    ] + ([("Approved Option", option_name)] if option_name else []) + [
        ("Total Approved Budget", money(ev["approved_budget"], currency)),
        ("Requested by", creator["name"] if creator else "—"),
        ("Final Approver", approver["name"]),
        ("Decision Date", fmt_date(ev["decided_at"]) if ev["decided_at"] else "—"),
    ]
    body = [
        _callout('<b style="color:%s;">Fully approved.</b> This event has completed the approval chain '
                 'and every vendor on the selected package was approved.' % GREEN, GREEN, "#ecf8f4"),
        _rows(pairs[:4] + ([("Approved Option",
                              '<span style="color:%s;">%s</span>' % (METEOR, esc(option_name)))] if option_name else [])
              + [("Total Approved Budget",
                  '<span style="color:%s;font-size:16px;">%s</span>'
                  % (GREEN, esc(money(ev["approved_budget"], currency)))),
                 ("Requested by", creator["name"] if creator else "—"),
                 ("Final Approver", approver["name"]),
                 ("Decision Date", fmt_date(ev["decided_at"]) if ev["decided_at"] else "—")]),
        _signed_block(trail),
        _h("Approved vendors (%d)" % len(approved)),
        _vendor_table(approved, currency, show_status=True),
        _p("Once the event has taken place, open it in the hub and mark it as <b>executed</b> with the actual "
           "execution date — the approver is notified automatically and the record is archived in the history."),
    ]
    url = "%s/#/events/%d" % (base_url.rstrip("/"), ev["id"])
    text = _t_shell("Event approved", [
        "FULLY APPROVED — this event has completed the approval chain and every vendor on the "
        "selected package was approved.", "", _t_rows(pairs),
        _t_signed(trail),
        _t_head("Approved vendors (%d)" % len(approved)), _t_vendors(approved, currency, True), "",
        "Once the event has taken place, mark it as executed in the hub with the actual date.",
    ], ("VIEW EVENT", url))
    return subject, _shell("Event approved", subject, "".join(body), ("View Event", url), GREEN), text


def build_partially_approved(ev, vendors, approver, base_url, currency, option_name=None,
                            creator=None, trail=None):
    """E-mail 3 — event approved, one or more vendors rejected."""
    subject = "Event Approval Update – %s" % ev["event_name"]
    approved = [v for v in vendors if v["approval_status"] == db.VS_APPROVED]
    rejected = [v for v in vendors if v["approval_status"] in (db.VS_REJECTED, db.VS_NOT_SELECTED)]
    body = [
        _callout('<b style="color:%s;">Approved, with some vendors left out.</b> The approver included the '
                 'vendors listed below; the rest were not selected.' % BLUE, TURQ, "#e9f5fc"),
        _rows([
            ("Event ID", ev["event_number"]),
            ("Event Name", ev["event_name"]),
            ("Event Date", fmt_date(ev["event_date"]))] + _chosen_row(option_name) + [
            ("Event Decision", '<span style="color:%s;">Approved</span>' % GREEN),
            ("Approved Budget",
             '<span style="color:%s;font-size:16px;">%s</span>' % (GREEN, esc(money(ev["approved_budget"], currency)))),
            ("Rejected Amount", '<span style="color:%s;">%s</span>' % (RED, esc(money(ev["rejected_budget"], currency)))),
            ("Requested by", creator["name"] if creator else "—"),
            ("Final Approver", approver["name"]),
            ("Decision Date", fmt_date(ev["decided_at"]) if ev["decided_at"] else "—"),
        ]),
        _signed_block(trail),
    ]
    if approved:
        body += [_h("Approved vendors (%d)" % len(approved)), _vendor_table(approved, currency, show_status=True)]
    if rejected:
        body += [_h("Not selected (%d)" % len(rejected)),
                 _vendor_table(rejected, currency, show_status=True)]
    url = "%s/#/events/%d" % (base_url.rstrip("/"), ev["id"])
    t_pairs = [("Event ID", ev["event_number"]), ("Event Name", ev["event_name"]),
               ("Event Date", fmt_date(ev["event_date"]))] \
        + ([("Approved Option", option_name)] if option_name else []) \
        + [("Event Decision", "Approved"),
           ("Approved Budget", money(ev["approved_budget"], currency)),
           ("Rejected Amount", money(ev["rejected_budget"], currency)),
           ("Requested by", creator["name"] if creator else "—"),
           ("Final Approver", approver["name"]),
           ("Decision Date", fmt_date(ev["decided_at"]) if ev["decided_at"] else "—")]
    lines = ["APPROVED, WITH SOME VENDORS LEFT OUT — the rest were not selected.",
             "", _t_rows(t_pairs), _t_signed(trail)]
    if approved:
        lines += [_t_head("Approved vendors (%d)" % len(approved)), _t_vendors(approved, currency, True)]
    if rejected:
        lines += [_t_head("Not selected (%d)" % len(rejected)), _t_vendors(rejected, currency, True)]
    text = _t_shell("Event approval update", lines, ("VIEW EVENT", url))
    return subject, _shell("Event approval update", subject, "".join(body), ("View Event", url), TURQ), text


def build_rejected(ev, approver, reason, base_url, currency, creator=None, trail=None):
    """E-mail 4 — the event itself was rejected."""
    subject = "Event Rejected – %s" % ev["event_name"]
    body = [
        _callout('<b style="color:%s;">Rejected.</b> This event was not approved.' % RED, RED, "#fdf1ef"),
        _rows([
            ("Event ID", ev["event_number"]),
            ("Event Name", ev["event_name"]),
            ("Event Date", fmt_date(ev["event_date"])),
            ("Total Estimated Budget", money(ev["total_budget"], currency)),
            ("Rejection Reason", '<span style="color:%s;">%s</span>' % (RED, esc(reason or "—"))),
            ("Requested by", creator["name"] if creator else "—"),
            ("Rejected by", approver["name"]),
            ("Decision Date", fmt_date(ev["decided_at"]) if ev["decided_at"] else "—"),
        ]),
        _signed_block(trail, "Approved earlier in the chain"),
        _p("The event can be adjusted and submitted again for approval. The full history of this "
           "request is preserved in the hub."),
    ]
    url = "%s/#/events/%d" % (base_url.rstrip("/"), ev["id"])
    text = _t_shell("Event rejected", [
        "REJECTED — this event was not approved.", "",
        _t_rows([("Event ID", ev["event_number"]), ("Event Name", ev["event_name"]),
                 ("Event Date", fmt_date(ev["event_date"])),
                 ("Total Estimated Budget", money(ev["total_budget"], currency)),
                 ("Rejection Reason", reason or "—"),
                 ("Requested by", creator["name"] if creator else "—"),
                 ("Rejected by", approver["name"]),
                 ("Decision Date", fmt_date(ev["decided_at"]) if ev["decided_at"] else "—")]),
        _t_signed(trail, "Approved earlier in the chain"),
        "", "The event can be adjusted and submitted again for approval.",
    ], ("VIEW EVENT", url))
    return subject, _shell("Event rejected", subject, "".join(body), ("View Event", url), RED), text


def build_executed(ev, vendors, actor, base_url, currency):
    """E-mail 5 — execution confirmation, closing the loop for the approver."""
    subject = "Event Executed – %s" % ev["event_name"]
    approved = [v for v in vendors if v["approval_status"] == db.VS_APPROVED]
    body = [
        _callout('<b style="color:%s;">Executed.</b> This event has taken place and is now closed.' % TOPAZ,
                 TOPAZ, "#e8f8f8"),
        _rows([
            ("Event ID", ev["event_number"]),
            ("Event Name", ev["event_name"]),
            ("Planned Date", fmt_date(ev["event_date"])),
            ("Execution Date", '<span style="color:%s;font-size:15px;">%s</span>'
             % (TOPAZ, esc(fmt_date(ev["execution_date"])))),
            ("Location", ev["location"] or "—"),
            ("Approved Budget", money(ev["approved_budget"], currency)),
            ("Confirmed by", actor["name"]),
            ("Execution Notes", ev["execution_notes"] or "—"),
        ]),
        _h("Vendors delivered (%d)" % len(approved)),
        _vendor_table(approved, currency, show_status=True),
    ]
    url = "%s/#/events/%d" % (base_url.rstrip("/"), ev["id"])
    text = _t_shell("Event executed", [
        "EXECUTED — this event has taken place and is now closed.", "",
        _t_rows([("Event ID", ev["event_number"]), ("Event Name", ev["event_name"]),
                 ("Planned Date", fmt_date(ev["event_date"])),
                 ("Execution Date", fmt_date(ev["execution_date"])),
                 ("Location", ev["location"] or "—"),
                 ("Approved Budget", money(ev["approved_budget"], currency)),
                 ("Confirmed by", actor["name"]),
                 ("Execution Notes", ev["execution_notes"] or "—")]),
        _t_head("Vendors delivered (%d)" % len(approved)), _t_vendors(approved, currency, True),
    ], ("VIEW EVENT HISTORY", url))
    return subject, _shell("Event executed", subject, "".join(body), ("View Event History", url), TOPAZ), text


def build_login_code(user, code, minutes):
    """The one-time code that lets someone sign in.

    Deliberately plain and short: the reader wants six digits, not a newsletter. It
    carries no link, so a stolen copy cannot be turned into a click, and it says what
    to do if the request was not theirs.
    """
    subject = "Your KABi IC sign-in code: %s" % code
    body = [
        _p("Hello <b>%s</b>," % esc(user["name"])),
        _p("Use this code to sign in to the IC Events Approval Hub. "
           "It is valid for <b>%d minutes</b> and can be used once." % minutes),
        '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0">'
        '<tr><td align="center" style="padding:22px 0;">'
        '<div style="display:inline-block;background:%s;border-radius:12px;padding:18px 34px;'
        'font-family:%s;font-size:34px;font-weight:800;letter-spacing:10px;color:#fff;">%s</div>'
        '</td></tr></table>' % (METEOR, HEAD_FONT, esc(code)),
        _divider(),
        _callout("If you did not ask to sign in, ignore this message and tell the Internal "
                 "Communication team. Nobody can use this code but you, and it expires by "
                 "itself.", TURQ, "#e9f5fc"),
    ]
    text = _t_shell("Your sign-in code", [
        "Hello %s," % user["name"], "",
        "Use this code to sign in to the IC Events Approval Hub:", "",
        "    %s" % code, "",
        "It is valid for %d minutes and can be used once." % minutes, "",
        "If you did not ask to sign in, ignore this message and tell the Internal "
        "Communication team.",
    ], None)
    return subject, _shell("Your sign-in code", subject, "".join(body), None, METEOR), text


def build_setup_link(user, url, days):
    """The link that lets someone choose their own password.

    It carries no password, because nobody sends one any more: whoever opens this picks
    something only they know. Saying so plainly matters -- it tells the reader that a
    message asking them for their password, or offering them one, did not come from here.
    """
    subject = "Set your password - IC Events Approval Hub"
    body = [
        _p("Hello <b>%s</b>," % esc(user["name"])),
        _p("An account has been created for you on the <b>IC Events Approval Hub</b>. "
           "Use the button below to choose your own password. Nobody in Internal "
           "Communication sets it, sees it, or can look it up later."),
        _rows([("Your sign-in address", user["email"]),
               ("This link", "works once, and expires in %d days" % days)]),
        _divider(),
        _callout("If you were not expecting this, do not open the link -- tell the "
                 "Internal Communication team. It expires by itself either way.",
                 TURQ, "#e9f5fc"),
    ]
    text = _t_shell("Set your password", [
        "Hello %s," % user["name"], "",
        "An account has been created for you on the IC Events Approval Hub.",
        "Choose your own password here:", "",
        "    %s" % url, "",
        "Your sign-in address is %s." % user["email"],
        "The link works once and expires in %d days." % days, "",
        "Nobody in Internal Communication sets your password or can look it up.",
    ], ("Set your password", url))
    return subject, _shell("Set your password", subject, "".join(body),
                           ("Choose my password", url), TOPAZ), text


def build_test(to_name, base_url):
    subject = "Test message – IC Events Approval Hub"
    body = [
        _callout('<b style="color:%s;">SMTP is working.</b> This test message was delivered by the '
                 'IC Events Approval Hub.' % GREEN, GREEN, "#ecf8f4"),
        _p("Hello %s,<br>If you can read this, outgoing e-mail is configured correctly and approval "
           "requests and decision notifications will reach their recipients." % esc(to_name or "there")),
    ]
    text = _t_shell("Test message", [
        "SMTP is working. This test message was delivered by the IC Events Approval Hub.", "",
        "Hello %s," % (to_name or "there"),
        "If you can read this, outgoing e-mail is configured correctly.",
    ], ("OPEN THE HUB", base_url))
    return subject, _shell("Test message", subject, "".join(body), ("Open the hub", base_url), BLUE), text


# ------------------------------------------------------------------ outbox
def queue(conn, to_email, to_name, subject, body_html, mail_type, event_id=None, body_text=None,
          cc=None):
    """Store the message, then deliver it if SMTP is enabled."""
    cur = conn.execute(
        "INSERT INTO emails(event_id,to_email,to_name,cc,subject,body_html,body_text,type,status,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (event_id, to_email, to_name, cc or None, subject, body_html, body_text, mail_type,
         "queued", db.now_iso()),
    )
    mail_id = cur.lastrowid
    if db.get_setting(conn, "smtp_enabled", "0") == "1":
        send_now(conn, mail_id)
    return mail_id


def _build_message(sender, sender_name, to_email, subject, body_html, body_text=None, cc=None):
    outer = MIMEMultipart("related")
    outer["Subject"] = subject
    outer["From"] = "%s <%s>" % (sender_name, sender)
    outer["To"] = to_email
    if cc:
        outer["Cc"] = cc
    alt = MIMEMultipart("alternative")
    outer.attach(alt)
    if body_text:
        alt.attach(MIMEText(body_text, "plain", "utf-8"))
    alt.attach(MIMEText(body_html, "html", "utf-8"))
    raw = logo_bytes()
    if raw and ("cid:" + LOGO_CID) in body_html:
        img = MIMEImage(raw, "png")
        img.add_header("Content-ID", "<%s>" % LOGO_CID)
        img.add_header("Content-Disposition", "inline", filename="kabi.png")
        outer.attach(img)
    return outer


def build_eml(subject, to_email, to_name, body_html, body_text=None, from_email=None,
              from_name=None, cc=None):
    """A ready-to-send Outlook draft.

    `X-Unsent: 1` is what makes Outlook open the file as a composable message with a
    Send button rather than as a received e-mail — so the full branded HTML, logo
    included, can be sent from the user's own mailbox without any SMTP setup.
    """
    msg = _build_message(from_email or "", from_name or "", to_email, subject, body_html,
                         body_text, cc)
    del msg["From"]                      # let Outlook fill in the signed-in account
    if from_email:
        msg["From"] = "%s <%s>" % (from_name or from_email, from_email)
    if to_name:
        del msg["To"]
        msg["To"] = "%s <%s>" % (to_name, to_email)
    msg["X-Unsent"] = "1"
    return msg.as_bytes()


def send_now(conn, mail_id):
    row = conn.execute("SELECT * FROM emails WHERE id=?", (mail_id,)).fetchone()
    if not row:
        return False, "Message not found"
    host = db.get_setting(conn, "smtp_host", "")
    if not host:
        conn.execute("UPDATE emails SET status='failed', error=? WHERE id=?",
                     ("SMTP host is not configured", mail_id))
        return False, "SMTP host is not configured"
    try:
        port = int(db.get_setting(conn, "smtp_port", "587") or 587)
        user = db.get_setting(conn, "smtp_user", "")
        pwd = db.get_setting(conn, "smtp_password", "")
        use_tls = db.get_setting(conn, "smtp_tls", "1") == "1"
        sender = db.get_setting(conn, "mail_from", "ic-hub@kabi.ai")
        sender_name = db.get_setting(conn, "mail_from_name", "KABi IC Events Approval Hub")

        cc = row["cc"] if "cc" in row.keys() else None
        msg = _build_message(sender, sender_name, row["to_email"], row["subject"],
                             row["body_html"], row["body_text"], cc)

        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=25)
        else:
            server = smtplib.SMTP(host, port, timeout=25)
            if use_tls:
                server.starttls()
        if user:
            server.login(user, pwd)
        rcpt = [row["to_email"]] + [a.strip() for a in (cc or "").split(",") if a.strip()]
        server.sendmail(sender, rcpt, msg.as_string())
        server.quit()

        conn.execute("UPDATE emails SET status='sent', sent_at=?, error=NULL WHERE id=?",
                     (db.now_iso(), mail_id))
        return True, "sent"
    except Exception as exc:  # noqa: BLE001 - surfaced to the admin UI
        conn.execute("UPDATE emails SET status='failed', error=? WHERE id=?", (str(exc)[:400], mail_id))
        return False, str(exc)
