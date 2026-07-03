"""Lead-magnet landing page rendering (Phase 3).

Renders the lead-magnet copy into a self-contained HTML page (pain-point headline,
benefit checklist, Name + Email form) served at /lead-magnet/<slug>. Colors/type
follow the prototype's design tokens. The form posts to /leads (Phase 4).
"""

import html
import re

# Light-theme design tokens from the prototype (handoff/README.md).
_ACCENT = "#5B47F5"
_BG = "#F3F3F5"
_PANEL = "#FFFFFF"
_INK = "#191920"
_INK2 = "#55555E"
_BORDER = "#E8E8EB"


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "lead-magnet"


def build_slug(base: str, run_id: str) -> str:
    """Slug + a per-run suffix so each run's landing page URL is unique
    (e.g. 'cold-chain-probiotics-2412'), matching the schema's UNIQUE(slug)."""
    suffix = run_id.replace("RUN-", "").lower() or "x"
    return f"{slugify(base)}-{suffix}"


def render_lead_magnet_html(copy: dict, slug: str, run_id: str) -> str:
    headline = html.escape(copy.get("headline") or "")
    subhead = html.escape(copy.get("subhead") or "")
    guide_title = html.escape(copy.get("guide_title") or "Free Guide")
    bullets = copy.get("bullets") or []
    bullets_html = "\n".join(
        f'        <li>{html.escape(str(b))}</li>' for b in bullets
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{guide_title}</title>
<style>
  body {{ margin:0; background:{_BG}; color:{_INK};
         font-family:'Hanken Grotesk',system-ui,-apple-system,sans-serif; }}
  .wrap {{ max-width:640px; margin:48px auto; padding:0 20px; }}
  .card {{ background:{_PANEL}; border:1px solid {_BORDER}; border-radius:13px;
          padding:32px; box-shadow:0 1px 2px rgba(20,20,30,.05),0 1px 3px rgba(20,20,30,.05); }}
  h1 {{ font-size:28px; line-height:1.2; margin:0 0 8px; font-weight:800; }}
  .sub {{ color:{_INK2}; margin:0 0 20px; }}
  ul {{ list-style:none; padding:0; margin:0 0 24px; }}
  li {{ padding:8px 0 8px 28px; position:relative; }}
  li::before {{ content:'✓'; position:absolute; left:0; color:{_ACCENT}; font-weight:800; }}
  label {{ display:block; font-size:13px; color:{_INK2}; margin:12px 0 4px; }}
  input {{ width:100%; box-sizing:border-box; padding:11px 12px; border:1px solid {_BORDER};
          border-radius:9px; font-size:15px; }}
  button {{ margin-top:18px; width:100%; padding:13px; background:{_ACCENT}; color:#fff;
           border:0; border-radius:9px; font-size:15px; font-weight:700; cursor:pointer; }}
  .tag {{ display:inline-block; font-size:12px; color:{_ACCENT}; font-weight:700;
         letter-spacing:.04em; text-transform:uppercase; margin-bottom:12px; }}
</style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <span class="tag">Free guide</span>
      <h1>{headline}</h1>
      <p class="sub">{subhead}</p>
      <ul>
{bullets_html}
      </ul>
      <form action="/leads" method="post">
        <input type="hidden" name="lead_source" value="{html.escape(run_id)}">
        <input type="hidden" name="slug" value="{html.escape(slug)}">
        <label for="name">Name</label>
        <input id="name" name="name" type="text" required>
        <label for="email">Email</label>
        <input id="email" name="email" type="email" required>
        <button type="submit">Send me the guide</button>
      </form>
    </div>
  </div>
</body>
</html>
"""


def render_lead_success_html(name: str) -> str:
    """Tiny success page shown after a browser form submits to /leads."""
    safe_name = html.escape(name.split()[0] if name.strip() else "there")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Thank you</title>
<style>
  body {{ margin:0; background:{_BG}; color:{_INK};
         font-family:'Hanken Grotesk',system-ui,-apple-system,sans-serif; }}
  .wrap {{ max-width:560px; margin:80px auto; padding:0 20px; text-align:center; }}
  .card {{ background:{_PANEL}; border:1px solid {_BORDER}; border-radius:13px; padding:40px; }}
  h1 {{ font-size:26px; margin:0 0 8px; font-weight:800; }}
  p {{ color:{_INK2}; }}
  .check {{ font-size:40px; color:{_ACCENT}; }}
</style></head>
<body><div class="wrap"><div class="card">
  <div class="check">✓</div>
  <h1>Thanks, {safe_name}!</h1>
  <p>Your guide is on its way — check your inbox shortly.</p>
</div></div></body></html>
"""
