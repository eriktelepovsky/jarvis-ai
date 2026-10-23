# Custom sources

A custom source shows your own app around the orb, e.g. unpaid invoices from your invoicing app or pending payments from your wallet. You do not need an API or a plugin. Add one address to the app that answers with JSON in the format below, then add the source in **Integrations → Custom source**.

myOrb reads the address in the source's interval. It sends the token you entered as `Authorization: Bearer <token>` and shows the answer as a circle with details: filters, groups, search and links. Your app decides what is unpaid, overdue or pending; myOrb only shows it.

## The circle

Every source has its own circle, named after the source. Sources with the same **circle** name are shown as one circle, e.g. invoices and a wallet as "Finance":

- **The number** adds up what needs you in each source: its `attention_count`, or its `value` when there is none.
- **The details** list every source as a group, with its own groups (e.g. clients) under it. Each source's numbers filter only its own rows.
- **Problems:** when one source fails, the circle shows the others with a warning.

## The format

```json
{
  "version": 1,
  "title": "Invoices",
  "label": "Overdue",
  "value": 7,
  "attention_count": 2,
  "attention": true,
  "summary": "unpaid · 2 overdue",
  "stats": [
    {"value": 7, "label": "Unpaid", "filter": "unpaid"},
    {"value": 2, "label": "Overdue", "filter": "overdue"},
    {"value": "4 820 €", "label": "To collect"}
  ],
  "list_title": "Unpaid invoices",
  "group_label": "clients",
  "rows": [
    {
      "title": "2026-041 · Acme s.r.o.",
      "subtitle": "1 250,00 € · issued 1 Oct",
      "badge": "▲ Due 15 Oct",
      "tone": "fail",
      "url": "https://invoices.example.com/invoices/41/",
      "tags": ["unpaid", "overdue"],
      "group": "Acme s.r.o."
    }
  ],
  "link": {"label": "Open invoices", "url": "https://invoices.example.com/invoices/"},
  "brief": ["7 unpaid invoices, 2 overdue: Acme s.r.o. (due 15 Oct)."]
}
```

| Field | Required | Meaning |
|---|---|---|
| `version` | yes | Always `1`. |
| `title` | yes | The source's name in the details, e.g. "Invoices". |
| `value` | yes | The main number (or short text). In the circle when there is no `attention_count`. |
| `attention_count` | | How many items need you, e.g. overdue invoices. The number in the circle when given. |
| `label` | | What the number counts, e.g. "Overdue": it leads the summary in the details. Under the number in the orb is always the circle's name (e.g. Finance). |
| `attention` | | `true` highlights the circle in amber and makes it larger. |
| `summary` | | Next to the big number in the details. |
| `stats` | | Up to 8 numbers in the details. With a `filter`, clicking one shows only the rows that have that tag in `tags`. |
| `list_title` | | The heading of the list. |
| `group_label` | | The rows' groups in the plural, e.g. "clients". Shown as a filter when there is more than one. |
| `rows` | | Up to 500 items. `tone` is `fail`, `warn`, `ok`, `info` or `neutral` and colors the `badge`. `url` must be http(s). |
| `link` | | A button in the details' footer that opens the app. |
| `brief` | | Sentences for the AI brief. Nothing else from the source goes to it, so you decide whether amounts are mentioned. |

Unknown fields are ignored. An answer that is not in the format is refused with a message that says what is wrong, both when the source is added and in the circle.

## Django example

Use a long random token, e.g. from `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Put it in the app's settings (`MYORB_TOKEN`, from an environment variable) and in myOrb.

```python
# myorb_views.py
import hmac
from datetime import date

from django.conf import settings
from django.http import HttpResponseForbidden, JsonResponse
from django.views.decorators.http import require_GET

from .models import Invoice


def _authorized(request) -> bool:
    expected = f"Bearer {settings.MYORB_TOKEN}"
    given = request.headers.get("Authorization", "")
    return bool(settings.MYORB_TOKEN) and hmac.compare_digest(given, expected)


@require_GET
def myorb_invoices(request):
    if not _authorized(request):
        return HttpResponseForbidden()
    today = date.today()
    unpaid = (
        Invoice.objects.filter(paid_at__isnull=True).select_related("client").order_by("due_date")
    )
    overdue = [i for i in unpaid if i.due_date < today]
    rows = []
    for invoice in unpaid:
        late = invoice.due_date < today
        rows.append(
            {
                "title": f"{invoice.number} · {invoice.client.name}",
                "subtitle": f"{invoice.total:.2f} € · issued {invoice.issued_on:%-d %b}",
                "badge": f"{'▲ ' if late else ''}Due {invoice.due_date:%-d %b}",
                "tone": "fail" if late else "neutral",
                "url": request.build_absolute_uri(invoice.get_absolute_url()),
                "tags": ["unpaid", "overdue"] if late else ["unpaid"],
                "group": invoice.client.name,
            }
        )
    return JsonResponse(
        {
            "version": 1,
            "title": "Invoices",
            "label": "Overdue",
            "value": len(rows),
            "attention_count": len(overdue),
            "attention": bool(overdue),
            "summary": f"unpaid · {len(overdue)} overdue",
            "stats": [
                {"value": len(rows), "label": "Unpaid", "filter": "unpaid"},
                {"value": len(overdue), "label": "Overdue", "filter": "overdue"},
            ],
            "list_title": "Unpaid invoices",
            "group_label": "clients",
            "rows": rows,
            "link": {"label": "Open invoices", "url": request.build_absolute_uri("/invoices/")},
            "brief": [f"{len(rows)} unpaid invoices, {len(overdue)} overdue."],
        }
    )
```

```python
# urls.py
from .myorb_views import myorb_invoices

urlpatterns += [path("myorb/invoices/", myorb_invoices)]
```

A wallet's pending transactions work the same way: `attention_count` is the number of pending transactions, and each row is one of them, e.g. `{"title": "Lidl · −23,40 €", "badge": "21 Oct", "tone": "warn", "tags": ["pending"]}`.
