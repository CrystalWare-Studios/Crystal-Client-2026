
import textwrap

DEFAULT_MAX_TOTAL_LENGTH = 144

FRAME_STYLES = {
    "none": {
        "name": "None",
        "description": "No frame, plain text",
        "top_left": "",
        "top_right": "",
        "bottom_left": "",
        "bottom_right": "",
        "horizontal": "",
        "vertical": "",
        "padding": False
    },
    "dots": {
        "name": "Dots",
        "description": "Simple dotted border",
        "top_left": ".",
        "top_right": ".",
        "bottom_left": ".",
        "bottom_right": ".",
        "horizontal": ".",
        "vertical": ".",
        "padding": True
    },
    "dashes": {
        "name": "Dashes",
        "description": "Clean dash border",
        "top_left": "+",
        "top_right": "+",
        "bottom_left": "+",
        "bottom_right": "+",
        "horizontal": "-",
        "vertical": "|",
        "padding": True
    },
    "equals": {
        "name": "Equals",
        "description": "Double line style",
        "top_left": "+",
        "top_right": "+",
        "bottom_left": "+",
        "bottom_right": "+",
        "horizontal": "=",
        "vertical": "|",
        "padding": True
    },
    "stars": {
        "name": "Stars",
        "description": "Decorative star border",
        "top_left": "*",
        "top_right": "*",
        "bottom_left": "*",
        "bottom_right": "*",
        "horizontal": "*",
        "vertical": "*",
        "padding": True
    },
    "hashtags": {
        "name": "Hashtags",
        "description": "Bold hashtag border",
        "top_left": "#",
        "top_right": "#",
        "bottom_left": "#",
        "bottom_right": "#",
        "horizontal": "#",
        "vertical": "#",
        "padding": True
    },
    "tildes": {
        "name": "Tildes",
        "description": "Wavy tilde border",
        "top_left": "~",
        "top_right": "~",
        "bottom_left": "~",
        "bottom_right": "~",
        "horizontal": "~",
        "vertical": "~",
        "padding": True
    },
    "minimal_top": {
        "name": "Minimal Top",
        "description": "Simple line above text",
        "top_left": "",
        "top_right": "",
        "bottom_left": "",
        "bottom_right": "",
        "horizontal": "-",
        "vertical": "",
        "top_only": True,
        "padding": False
    },
    "minimal_both": {
        "name": "Minimal Lines",
        "description": "Lines above and below",
        "top_left": "",
        "top_right": "",
        "bottom_left": "",
        "bottom_right": "",
        "horizontal": "-",
        "vertical": "",
        "top_only": False,
        "padding": False
    },
    "arrows": {
        "name": "Arrows",
        "description": "Arrow-style accents",
        "top_left": ">",
        "top_right": "<",
        "bottom_left": ">",
        "bottom_right": "<",
        "horizontal": "-",
        "vertical": "|",
        "padding": True
    },
    "brackets": {
        "name": "Brackets",
        "description": "Clean bracket style",
        "top_left": "[",
        "top_right": "]",
        "bottom_left": "[",
        "bottom_right": "]",
        "horizontal": "",
        "vertical": "",
        "bracket_mode": True,
        "padding": False
    },
    "parens": {
        "name": "Parentheses",
        "description": "Soft parentheses style",
        "top_left": "(",
        "top_right": ")",
        "bottom_left": "(",
        "bottom_right": ")",
        "horizontal": "",
        "vertical": "",
        "bracket_mode": True,
        "padding": False
    },
    "angle": {
        "name": "Angle Brackets",
        "description": "Sharp angle style",
        "top_left": "<",
        "top_right": ">",
        "bottom_left": "<",
        "bottom_right": ">",
        "horizontal": "",
        "vertical": "",
        "bracket_mode": True,
        "padding": False
    },
    "pipes": {
        "name": "Pipes",
        "description": "Vertical pipe style",
        "top_left": "|",
        "top_right": "|",
        "bottom_left": "|",
        "bottom_right": "|",
        "horizontal": "",
        "vertical": "",
        "bracket_mode": True,
        "padding": False
    },
    "emoji": {
        "name": "Emoji",
        "description": "Your chosen emoji on each end of every line",
        "top_left": "",
        "top_right": "",
        "bottom_left": "",
        "bottom_right": "",
        "horizontal": "",
        "vertical": "",
        "emoji_mode": True,
        "padding": False
    }
}


DEFAULT_FRAME_EMOJI = "✨"


def get_frame_styles():
    return [{"id": k, "name": v["name"], "description": v["description"]} for k, v in FRAME_STYLES.items()]


def get_longest_line_length(text):
    lines = text.split('\n')
    return max(len(line) for line in lines) if lines else 0


def truncate_line(line, max_width):
    if max_width <= 0:
        return ""
    if len(line) <= max_width:
        return line
    if max_width <= 3:
        return line[:max_width]
    return line[:max_width - 1] + "..."


def _fit_to_budget(build_fn, lines, max_width, max_total_length):
    line_list = list(lines) if lines else [""]
    width = max(min(max_width, 40), 1)

    for w in range(width, 0, -1):
        result = build_fn(w, line_list)
        if len(result) <= max_total_length:
            return result

    while len(line_list) > 1:
        line_list = line_list[:-1]
        for w in range(width, 0, -1):
            result = build_fn(w, line_list)
            if len(result) <= max_total_length:
                return result

    result = build_fn(1, line_list[:1])
    return result[:max_total_length] if len(result) > max_total_length else result


def apply_frame(text, style_id, max_total_length=DEFAULT_MAX_TOTAL_LENGTH, width=None, emoji=DEFAULT_FRAME_EMOJI):
    if not text or not text.strip():
        return text

    style = FRAME_STYLES.get(style_id, FRAME_STYLES["none"])

    if style_id == "none":
        return text[:max_total_length]

    lines = text.split('\n')
    preferred_width = width if width is not None else get_longest_line_length(text)
    preferred_width = min(preferred_width, 40)

    if style.get("emoji_mode"):
        return apply_emoji_frame(lines, emoji, preferred_width, max_total_length)

    if style.get("bracket_mode"):
        return apply_bracket_frame(lines, style, preferred_width, max_total_length)

    if style.get("top_only") is not None:
        return apply_minimal_frame(lines, style, preferred_width, max_total_length)

    return apply_box_frame(lines, style, preferred_width, max_total_length)


def apply_emoji_frame(lines, emoji, width, max_total_length=DEFAULT_MAX_TOTAL_LENGTH):
    emoji = (emoji or DEFAULT_FRAME_EMOJI).strip() or DEFAULT_FRAME_EMOJI

    def build(w, line_list):
        return '\n'.join(f"{emoji} {truncate_line(line, w)} {emoji}" for line in line_list)

    return _fit_to_budget(build, lines, width, max_total_length)


def apply_box_frame(lines, style, width, max_total_length=DEFAULT_MAX_TOTAL_LENGTH):
    tl, tr = style["top_left"], style["top_right"]
    bl, br = style["bottom_left"], style["bottom_right"]
    h, v = style["horizontal"], style["vertical"]
    padding = style["padding"]

    def build(w, line_list):
        inner_width = w + 2
        top_line = tl + (h * inner_width) + tr
        bottom_line = bl + (h * inner_width) + br
        body = []
        for line in line_list:
            truncated = truncate_line(line, w)
            padded = truncated.ljust(w)
            if padding:
                body.append(f"{v} {padded} {v}")
            else:
                body.append(f"{v}{padded}{v}")
        return '\n'.join([top_line] + body + [bottom_line])

    return _fit_to_budget(build, lines, width, max_total_length)


def apply_minimal_frame(lines, style, width, max_total_length=DEFAULT_MAX_TOTAL_LENGTH):
    h = style["horizontal"]
    top_only = style.get("top_only", False)

    def build(w, line_list):
        line_str = h * (w + 4)
        parts = [line_str] + [f"  {truncate_line(line, w)}  " for line in line_list]
        if not top_only:
            parts.append(line_str)
        return '\n'.join(parts)

    return _fit_to_budget(build, lines, width, max_total_length)


def apply_bracket_frame(lines, style, width=40, max_total_length=DEFAULT_MAX_TOTAL_LENGTH):
    tl, tr = style["top_left"], style["top_right"]

    def build(w, line_list):
        return '\n'.join(f"{tl}{truncate_line(line, w)}{tr}" for line in line_list)

    return _fit_to_budget(build, lines, width, max_total_length)


def get_frame_preview(style_id, max_total_length=DEFAULT_MAX_TOTAL_LENGTH, emoji=DEFAULT_FRAME_EMOJI):
    sample_text = "Hello World\n12:00 PM"
    return apply_frame(sample_text, style_id, max_total_length=max_total_length, emoji=emoji)


def _frame_total_length(style, width, lines, emoji=DEFAULT_FRAME_EMOJI):
    if lines <= 0:
        return 0
    if style.get("emoji_mode"):
        per_line = 2 * len(emoji or DEFAULT_FRAME_EMOJI) + 2 + width
        return lines * per_line + (lines - 1)
    if style.get("bracket_mode"):
        tl, tr = style["top_left"], style["top_right"]
        per_line = len(tl) + width + len(tr)
        return lines * per_line + (lines - 1)

    if style.get("top_only") is not None:
        h = style["horizontal"]
        border_len = len(h) * (width + 4)
        num_borders = 1 if style.get("top_only") else 2
        body_len = width + 4
        total_parts = num_borders + lines
        return num_borders * border_len + lines * body_len + (total_parts - 1)

    tl, tr = style["top_left"], style["top_right"]
    bl, br = style["bottom_left"], style["bottom_right"]
    h, v = style["horizontal"], style["vertical"]
    padding = style["padding"]
    inner_width = width + 2
    top_len = len(tl) + len(h) * inner_width + len(tr)
    bottom_len = len(bl) + len(h) * inner_width + len(br)
    content_len = 2 * len(v) + width + (2 if padding else 0)
    total_parts = 2 + lines
    return top_len + bottom_len + lines * content_len + (total_parts - 1)


def plan_frame_capacity(style_id, max_total_length=DEFAULT_MAX_TOTAL_LENGTH, min_width=14, max_width=40, emoji=DEFAULT_FRAME_EMOJI):
    style = FRAME_STYLES.get(style_id, FRAME_STYLES["none"])
    if style_id == "none" or not style or style_id not in FRAME_STYLES:
        return max_width, 6

    best_width, best_lines, best_capacity = min_width, 1, 0
    for width in range(max_width, min_width - 1, -1):
        lines = 0
        while _frame_total_length(style, width, lines + 1, emoji) <= max_total_length:
            lines += 1
        if lines >= 1:
            capacity = width * lines
            if capacity > best_capacity:
                best_capacity = capacity
                best_width, best_lines = width, lines

    return best_width, max(best_lines, 1)


def wrap_for_frame(text, width):
    if width <= 0:
        return text
    lines = []
    for paragraph in str(text or "").split('\n'):
        if not paragraph.strip():
            lines.append("")
            continue
        wrapped = textwrap.wrap(paragraph, width=width, break_long_words=True, break_on_hyphens=False)
        lines.extend(wrapped if wrapped else [""])
    return '\n'.join(lines)


def chunk_lines(text, lines_per_chunk):
    lines = str(text or "").split('\n')
    lines_per_chunk = max(lines_per_chunk, 1)
    chunks = ['\n'.join(lines[i:i + lines_per_chunk]) for i in range(0, len(lines), lines_per_chunk)]
    return chunks or [""]


def paginate_text(text, max_total_length=DEFAULT_MAX_TOTAL_LENGTH):
    text = str(text or "")
    if not text:
        return [""]
    if len(text) <= max_total_length:
        return [text]

    pages = []
    current = ""

    def flush():
        nonlocal current
        if current:
            pages.append(current)
            current = ""

    for line in text.split('\n'):
        while len(line) > max_total_length:


            flush()
            cut = line.rfind(' ', 0, max_total_length)
            if cut <= 0:
                cut = max_total_length
            pages.append(line[:cut].rstrip())
            line = line[cut:].lstrip()

        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= max_total_length:
            current = candidate
        else:
            flush()
            current = line

    flush()
    return pages or [""]
