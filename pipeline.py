"""The 'brain': character matching, prompt assembly, batch helpers, contact
sheet compositor."""
import re
from io import BytesIO

from PIL import Image

import config  # noqa: F401


# --------------------------------------------------------------------------- #
#  Character matching
# --------------------------------------------------------------------------- #
def match_characters(prompt, characters):
    """Return characters whose name appears as a whole word in the prompt.

    Also recognises explicit @Name mentions (which is what the cast chips
    insert) and these are treated as an unambiguous tag.
    """
    low = (prompt or "").lower()
    matched = []
    seen = set()
    for c in characters:
        name = (c.get("name") or "").strip()
        if not name or c["id"] in seen:
            continue
        # @-tag form
        if re.search(r"@" + re.escape(name.lower()) + r"\b", low):
            matched.append(c); seen.add(c["id"]); continue
        # bare-word form
        if re.search(r"(?<!\w)" + re.escape(name.lower()) + r"(?!\w)", low):
            matched.append(c); seen.add(c["id"])
    return matched


def strip_tags(prompt):
    """Remove leading @ characters before names so the prompt reads naturally
    when passed to the image model."""
    return re.sub(r"@(\w)", r"\1", prompt or "")


# --------------------------------------------------------------------------- #
#  Batch parsing
# --------------------------------------------------------------------------- #
def split_lines_batch(text, mode="line"):
    """Split a multi-line prompt-batch text into individual prompts.

    mode='line'        — each non-empty line is one prompt
    mode='blank'       — prompts are separated by one or more blank lines
                         (allows multi-line prompts)
    """
    text = (text or "").strip()
    if not text:
        return []
    if mode == "blank":
        parts = re.split(r"\n\s*\n+", text)
    else:
        parts = text.splitlines()
    return [p.strip() for p in parts if p.strip()]


def parse_character_batch(text):
    """Parse a block of text into [{name, description}, ...].

    Each entry is separated by a blank line. The FIRST line of an entry is the
    name (may optionally be wrapped in @ or [] or end with a colon); the rest
    of the entry is the description.
    """
    out = []
    for entry in split_lines_batch(text, mode="blank"):
        lines = [l.strip() for l in entry.splitlines() if l.strip()]
        if not lines:
            continue
        first = lines[0]
        # strip optional decorations like "@Name", "Name:", "[Name]"
        first = re.sub(r"^[\[@]?", "", first)
        first = re.sub(r"[\]:]$", "", first)
        first = re.sub(r":$", "", first).strip()
        # If first line has "Name - description" or "Name: description" form,
        # split it.
        m = re.match(r"^([^\-:—]+?)\s*[\-:—]\s*(.+)$", lines[0])
        if m and len(lines) == 1:
            name = m.group(1).strip().lstrip("@[").rstrip("]:")
            desc = m.group(2).strip()
        else:
            name = first
            desc = " ".join(lines[1:]).strip()
        if name:
            out.append({"name": name, "description": desc})
    return out


# --------------------------------------------------------------------------- #
#  Prompt assembly
# --------------------------------------------------------------------------- #
_MASTER_HINT = (
    "You are rendering ONE frame in a continuous visual series. Every frame must "
    "live in the SAME universe, art style, color palette and lighting language."
)


# When the reference style is a flat 2D / stick-figure / explainer cartoon (like
# the Zenn / "The Bliss Point" look), gpt-image-2 tends to drift toward soft
# shading, gradients and 3D depth. This directive forces it back to the crisp,
# 100%-flat, bold-outline cartoon look. Applied ONLY when the style looks flat,
# so photoreal / 3D styles are unaffected.
_FLAT_STYLE_HINTS = (
    "flat 2d", "flat colour", "flat color", "2d cartoon", "cartoon",
    "stick figure", "stick-figure", "stick man", "vector", "line art",
    "line-art", "marker", "hand-drawn", "hand drawn", "doodle", "explainer",
    "comic", "cel-shad", "cel shad",
)
_FLAT_DIRECTIVE = (
    "FLAT-CARTOON RENDERING — MANDATORY: bold, even BLACK outlines of uniform "
    "weight on every shape; fill each shape with ONE solid flat colour. "
    "ABSOLUTELY NO gradients, NO soft/cel shading, NO ambient occlusion, NO 3D "
    "depth, NO photographic texture, NO drop shadows, NO highlights. Backgrounds "
    "are clean white or a single flat colour block. Characters are simple stick "
    "figures: round bald heads, tiny simple oval/dot eyes, thin single-line "
    "limbs, minimal facial detail. Props are simple shapes with the same bold "
    "outline. Crisp, clean, 2D — like a modern hand-inked explainer cartoon."
)


def _is_flat_style(*texts):
    blob = " ".join(t for t in texts if t).lower()
    return any(h in blob for h in _FLAT_STYLE_HINTS)


def build_full_prompt(master_prompt, shot_prompt, matched, has_previous,
                      style_locked, style_notes=""):
    parts = [_MASTER_HINT]
    _flat = _is_flat_style(style_notes, master_prompt)

    if (master_prompt or "").strip():
        parts.append("WORLD / STYLE BIBLE:\n" + master_prompt.strip())

    # Explicit text style description from the reference video analysis.
    # Written twice: once as a system-level mandate, once prepended to the shot.
    if (style_notes or "").strip():
        parts.append(
            "MANDATORY ART STYLE — reproduce this EXACTLY. "
            "This style overrides any default tendencies of the model. "
            "Every pixel must match: same rendering technique, same palette, "
            "same line weight, same lighting, same proportions:\n"
            + style_notes.strip()
        )

    if _flat:
        parts.append(_FLAT_DIRECTIVE)

    if matched:
        names = ", ".join(c["name"] for c in matched)
        parts.append(
            "CHARACTER REFERENCES: the attached labelled character sheets define "
            f"the canonical look of {names}. Keep each named character's face, "
            "hair, build, outfit and colors identical to their sheet wherever "
            "they appear in this frame."
        )

    if has_previous:
        parts.append(
            "CONTINUITY: one attached image is the PREVIOUS frame in this series. "
            "Carry over its art style, palette, grain, line quality and world "
            "details so this frame reads like the same production — but compose "
            "the NEW scene described below rather than copying it."
        )

    if style_locked:
        parts.append(
            "STYLE ANCHORS: the FIRST attached reference images are frames taken "
            "straight from the source video (in a labelled grid they are marked "
            "\"STYLE REF\"). They define the EXACT rendering technique, line work, "
            "colour palette, shading, lighting, texture and proportions to "
            "reproduce. Copy that look faithfully — do NOT drift toward a generic "
            "or more detailed/realistic style. Any character-sheet image defines "
            "character identity ONLY; the previous-frame image is for continuity "
            "ONLY. If the reference look and your defaults disagree, the source "
            "video frames win."
        )

    # Prepend the style notes directly onto the shot prompt as well so it
    # appears twice (instruction block + prompt prefix) — this strongly anchors
    # the model to the right look even when references are ignored.
    raw_shot = strip_tags(shot_prompt).strip()
    if (style_notes or "").strip() and not raw_shot.lower().startswith(
            style_notes.strip()[:20].lower()):
        raw_shot = style_notes.strip().rstrip(".") + ". " + raw_shot

    parts.append("NEW FRAME TO RENDER:\n" + raw_shot)
    return "\n\n".join(parts)


def build_sheet_prompt(master_prompt, name, description, style_notes=""):
    parts = [f'Character model / reference sheet for "{name}".']
    if (description or "").strip():
        parts.append(f"Character description: {description.strip()}.")
    parts.append(
        "Layout: one clean reference sheet containing a full-body turnaround "
        "(front, 3/4, side, back), a large headshot, and a row of facial "
        "expressions. Keep proportions and design identical across every view. "
        "Neutral flat background, soft even lighting."
    )
    parts.append(
        f'Print the name "{name}" clearly as a label at the top of the sheet so '
        "it can be referenced by name later."
    )
    if (style_notes or "").strip():
        parts.append(
            "Art style for this character (must match the reference video look):\n"
            + style_notes.strip()
        )
    if _is_flat_style(style_notes, master_prompt):
        parts.append(_FLAT_DIRECTIVE)
    if (master_prompt or "").strip():
        parts.append("World / style bible:\n" + master_prompt.strip())
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
#  Contact sheet fallback
# --------------------------------------------------------------------------- #
def contact_sheet(images, labels=None, max_cols=2, cell=896, bg=(17, 17, 19)):
    """Composite reference images into one grid. When ``labels`` is given, each
    cell gets a readable caption (e.g. "STYLE REF", "CHAR: Maya", "PREV FRAME")
    so the image model can tell the style anchor from a character sheet from the
    previous frame — critical for faithfully copying the reference art style
    instead of blending all the refs together."""
    from PIL import ImageDraw, ImageFont
    imgs = []
    for b in images:
        try:
            imgs.append(Image.open(BytesIO(b)).convert("RGB"))
        except Exception:
            continue
    if not imgs:
        raise ValueError("no valid reference images to composite")

    labels = list(labels or [])
    n = len(imgs)
    cols = 1 if n == 1 else min(max_cols, n)
    rows = (n + cols - 1) // cols
    canvas = Image.new("RGB", (cols * cell, rows * cell), bg)
    draw = ImageDraw.Draw(canvas)
    font = None
    for _fname in ("arialbd.ttf", "Arial_Bold.ttf", "DejaVuSans-Bold.ttf", "arial.ttf"):
        try:
            font = ImageFont.truetype(_fname, max(22, cell // 28))
            break
        except Exception:
            continue
    if font is None:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None

    for idx, im in enumerate(imgs):
        im = im.copy()
        im.thumbnail((cell, cell))
        r, c = divmod(idx, cols)
        x = c * cell + (cell - im.width) // 2
        y = r * cell + (cell - im.height) // 2
        canvas.paste(im, (x, y))
        lbl = labels[idx] if idx < len(labels) else ""
        if lbl and font is not None:
            bx, by = c * cell, r * cell
            try:
                tw = int(draw.textlength(lbl, font=font))
            except Exception:
                tw = len(lbl) * (cell // 28)
            pad = cell // 80
            draw.rectangle([bx, by, bx + tw + pad * 3, by + (cell // 18)],
                           fill=(0, 0, 0))
            draw.text((bx + pad, by + pad), lbl, fill=(255, 214, 110), font=font)
    out = BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


def downsize_for_vision(image_bytes, max_side=1024, quality=85):
    """Make an image small enough to be efficient for Claude vision calls."""
    try:
        im = Image.open(BytesIO(image_bytes)).convert("RGB")
    except Exception:
        return image_bytes
    w, h = im.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    out = BytesIO()
    im.save(out, format="JPEG", quality=quality)
    return out.getvalue()
