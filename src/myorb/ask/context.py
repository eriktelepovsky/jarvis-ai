"""What Claude knows when the user asks something: the dashboard as text.

Every widget's numbers and rows (what the user sees in the details), and for plugins that keep
their rows to themselves (ask_rows = False) or have no widget (e.g. the weather), the facts
they give the brief. Built once per conversation, so follow-up questions reuse the cached
prompt and see the same data.
"""

import asyncio
import logging

from myorb.plugins import BriefSection, Plugin, Widget, WidgetContext, WidgetData

logger = logging.getLogger(__name__)

# Rows per widget: enough for "which invoices…", not so many that one source crowds out others.
MAX_ROWS = 150


async def _widget(context: WidgetContext, widget: Widget) -> WidgetData | None:
    try:
        return await widget.load(context)
    except Exception:
        logger.exception("Ask: widget %s failed", widget.id)
        return None


async def _brief(context: WidgetContext, plugin: Plugin) -> BriefSection | None:
    try:
        return await plugin.brief(context)
    except Exception:
        logger.exception("Ask: brief facts from %s failed", plugin.id)
        return None


def widget_text(widget: Widget, data: WidgetData) -> str:
    lines = [f"## {widget.title}"]
    number = " ".join(str(p) for p in (data.value, data.label or widget.label) if p is not None)
    lines.append(f"Number: {number}" + (f" ({data.summary})" if data.summary else ""))
    if data.message:
        lines.append(f"Note: {data.message}")
    if data.stats:
        lines.append("Stats: " + "; ".join(f"{s.label}: {s.value}" for s in data.stats))
    if data.rows:
        lines.append(data.list_title or "Items:")
    for row in data.rows[:MAX_ROWS]:
        where = " / ".join(p for p in (row.group, row.subgroup) if p)
        parts = [
            row.title,
            row.subtitle,
            row.badge,
            f"[{where}]" if where else "",
            f"(ref: {row.ref})" if row.ref else "",
        ]
        lines.append("- " + " · ".join(p for p in parts if p))
    if len(data.rows) > MAX_ROWS:
        lines.append(f"(and {len(data.rows) - MAX_ROWS} more)")
    return "\n".join(lines)


async def dashboard(context: WidgetContext, plugins: list[Plugin]) -> tuple[str, list[Plugin]]:
    """The dashboard as Markdown-like text, sources in the order of the welcome screen; and
    the plugins whose widgets are set up (their Ask actions are offered)."""
    set_up: list[Plugin] = []

    async def plugin_text(plugin: Plugin) -> list[str]:
        widgets = plugin.widgets() if plugin.ask_rows else []
        loaded = await asyncio.gather(*(_widget(context, w) for w in widgets))
        texts = [
            widget_text(widget, data)
            for widget, data in zip(widgets, loaded, strict=True)
            if data is not None and data.connected
        ]
        if texts:
            set_up.append(plugin)
        if not widgets:
            section = await _brief(context, plugin)
            if section and section.lines:
                texts.append(f"## {section.title}\n" + "\n".join(f"- {x}" for x in section.lines))
        return texts

    parts = await asyncio.gather(*(plugin_text(p) for p in plugins))
    now = context.now
    header = f"Now: {now:%A %d %B %Y, %H:%M} ({now.tzinfo})."
    text = "\n\n".join([header, *(text for texts in parts for text in texts)])
    return text, [p for p in plugins if p in set_up]
