"""LLM prompts, kept faithful to handoff/PROMPTS.md.

`string.Template` ($-placeholders) is used instead of f-strings/`.format()` so the
literal JSON braces in the templates don't need escaping.
"""

from string import Template

# --- 1. Research extraction (Phase 2) --------------------------------------

RESEARCH_SYSTEM = (
    "You are a market-research analyst specializing in health & wellness in\n"
    "underdeveloped and emerging-market regions. You extract specific, evidence-backed\n"
    "market pain points from source material. You never invent statistics; every claim\n"
    "must trace to the provided sources."
)

RESEARCH_USER = Template(
    """Target market: $market

Below are excerpts from recent articles, papers, and reports. Identify ONE specific,
underserved market pain point in this space — something concrete enough to build
content and a product offer around. Avoid generic statements ("access is hard").

Sources:
$sources

Already-covered pain points (DO NOT repeat or closely paraphrase these):
$existing

Return ONLY this JSON:
{
  "pain_point": "<one sharp sentence, specific and concrete>",
  "source_insight": "<1-2 sentences of supporting evidence, citing which source>",
  "source_url": "<the most relevant source URL, or null>",
  "region": "<the specific sub-region this is about>",
  "novelty_self_score": <0-100 estimate of how distinct this is from the covered list>
}"""
)

# Appended on a re-query after the uniqueness guard rejects a candidate, nudging
# the model toward a genuinely different angle.
REGENERATE_SUFFIX = (
    "\n\nYour previous attempt was too similar to existing content. "
    "Take a distinctly different angle, structure, and opening."
)

# Appended when a human reviewer rejects a piece with a reason (addendum §10).
REGENERATE_WITH_REASON = Template(
    "\n\nA reviewer rejected the previous draft for this reason: \"$reason\".\n"
    "Produce a clearly different draft that fixes exactly that, keep it unique versus "
    "all prior content, and obey every original rule (voice, structure, length)."
)


# --- 2. Blog post (Phase 3; human-voice spec per the addendum) --------------

BLOG_SYSTEM = (
    "You are a thoughtful, warm blogger writing for a general audience, on behalf of a\n"
    "health & wellness brand serving emerging markets. You write specific, credible,\n"
    "non-generic long-form content that reads as if a real person wrote it, not a robot\n"
    "or a corporate AI. You never fabricate statistics; when you reference data, you\n"
    "attribute it to the provided source insight."
)

BLOG_USER = Template(
    """Topic / primary keyword (pain point): $pain_point
Supporting evidence: $source_insight
Region: $region
Lead magnet to promote: "$lead_magnet_title"

Write a long-form blog post (800-1200 words; aim past 1000) targeting the pain point
as the primary keyword. It must sound like an empathetic, knowledgeable human wrote it.

VOICE & STYLE (follow strictly):
- Conversational but not sloppy. Use contractions (it's, you're, we'll) where natural.
- Address the reader directly as "you." Make them feel spoken to, not lectured.
- Mix short and medium sentences for natural rhythm. Avoid long academic run-ons.
- One or two brief, relatable analogies are fine. No fabricated life stories.
- Plain, concrete language. Explain any technical term immediately; avoid jargon.
- SEVERELY limit em-dashes: no more than 2 in the entire post. Rewrite clauses with
  commas, periods, or parentheses instead.
- No hollow AI phrases ("navigating the landscape", "it's important to note"). Never
  use the phrase "delve into".
- No emojis unless the topic truly demands it.
- Short paragraphs, usually 2-4 sentences.

STRUCTURE:
1. Headline: clear, benefit-driven, answers a question. No clickbait, no ALL CAPS.
2. Intro (1-2 short paragraphs): hook with a relatable problem, question, or fact;
   say why it matters and what the reader gains.
3. Body (3-5 scannable sections): casual but descriptive subheadings, one idea each,
   at most one short bullet list per section.
4. Conclusion: summarize the takeaway in a fresh way (don't just repeat); end with a
   gentle, open-ended question or one small action. No pushy calls-to-action.

SEO REQUIREMENTS (in addition to the above):
- meta title <= 60 chars; meta description <= 155 chars
- 4-6 H2 section headers, keyword-aware
- 2-3 internal-link SUGGESTIONS (anchor text + topic) - do not invent URLs
- One lead-magnet call-to-action near the end promoting the guide above, kept gentle
  and in the same human voice

Return ONLY this JSON:
{
  "meta_title": "...",
  "meta_description": "...",
  "headers": ["...", "..."],
  "body_markdown": "...full post in markdown...",
  "internal_link_suggestions": [{"anchor": "...", "target_topic": "..."}],
  "cta": "...",
  "word_count": <int>
}"""
)

# Appended on a regenerate when the deterministic voice check fails, telling the
# model exactly what to fix. Fill $problems before appending.
BLOG_VOICE_REGENERATE_SUFFIX = (
    "\n\nYour previous draft broke the voice rules ($problems). Rewrite it from "
    "scratch obeying every rule, especially the em-dash limit and the banned phrases."
)


# --- 3. Social posts (Phase 3) ---------------------------------------------
# PROMPTS.md #3 has no System block; this brief one sets brand voice consistently.

SOCIAL_SYSTEM = (
    "You are a social media writer for a health & wellness brand serving emerging\n"
    "markets. Each platform gets its native voice. You never fabricate statistics."
)

SOCIAL_USER = Template(
    """Pain point: $pain_point
Evidence: $source_insight
Lead magnet: "$lead_magnet_title"

Write three social posts, each in its platform's native voice, each ending in a
call-to-action for the lead magnet:

- linkedin: professional, 150-250 words, a credible hook in line 1, line breaks
- facebook: conversational, 80-150 words, approachable
- instagram: short and punchy, emoji-friendly, plus 4-6 relevant hashtags

Do NOT reuse phrasing across the three. Return ONLY this JSON:
{
  "linkedin": "...",
  "facebook": "...",
  "instagram": "...",
  "instagram_hashtags": ["#...", "..."]
}"""
)


# --- 4. Lead-magnet landing copy (Phase 3) ---------------------------------
# PROMPTS.md #4 has no System block; this brief one sets brand voice consistently.

LEAD_MAGNET_SYSTEM = (
    "You are a conversion copywriter for a health & wellness brand serving emerging\n"
    "markets. You write benefit-driven, credible landing-page copy."
)

LEAD_MAGNET_USER = Template(
    """Pain point: $pain_point
Region: $region

Write landing-page copy for a free guide that solves this pain point. Return ONLY:
{
  "headline": "<benefit-driven, references the pain point>",
  "subhead": "<1 sentence>",
  "bullets": ["<3 concrete things the reader will learn>"],
  "guide_title": "<the downloadable's title, e.g. '5 Ways to ...'>",
  "slug": "<url-safe-kebab-slug>"
}"""
)


# --- 5. Email nurture sequence (Phase 4) -----------------------------------

EMAIL_SYSTEM = (
    "You are an email marketing specialist. You write warm, useful, non-spammy nurture\n"
    "emails. Soft-sell only in the final email. Keep each email scannable."
)

EMAIL_USER = Template(
    """Pain point: $pain_point
Lead magnet delivered: "$lead_magnet_title"
Brand/sender: $sender_name

Draft a 3-email nurture sequence. Use {{name}} as a merge tag for the recipient.

- Email 1 (send immediately): deliver the lead magnet, set expectations.
- Email 2 (day 3): educational value expanding on the pain point. No pitch.
- Email 3 (day 7): a short case study / testimonial and ONE soft call-to-action.

Return ONLY this JSON:
{
  "emails": [
    {"position": 1, "goal": "deliver",    "timing": "immediately", "subject": "...", "body": "..."},
    {"position": 2, "goal": "educate",    "timing": "day_3",       "subject": "...", "body": "..."},
    {"position": 3, "goal": "soft_pitch", "timing": "day_7",       "subject": "...", "body": "..."}
  ]
}"""
)
