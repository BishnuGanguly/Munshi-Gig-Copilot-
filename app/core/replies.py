"""Reply wording: short English sentences that reword cleanly into other languages.
Amounts are digits with the rupee sign; every figure is computed by tools.py, never by a model."""
from datetime import date


def fmt_money(n: float) -> str:
    n = round(float(n), 2)
    whole, _, frac = f"{abs(n):.2f}".partition(".")
    head, tail = whole[:-3], whole[-3:]
    if head:
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups) + "," + tail
    text = whole if frac == "00" else f"{whole}.{frac}"
    return ("-" if n < 0 else "") + "₹" + text


def describe_entry(e: dict, today: date) -> str:
    amount = fmt_money(e["amount"])
    t = e["type"]
    if t == "income":
        text = f"income {amount}" + (f" from {e['platform']}" if e.get("platform") else "")
    elif t == "saving":
        text = f"saving {amount}"
    else:
        cat = e.get("category")
        text = f"expense {amount}" + (f" on {cat}" if cat and cat != "other" else "")
    d = date.fromisoformat(e["date"])
    if d != today:
        text += " (yesterday)" if (today - d).days == 1 else f" ({d.isoformat()})"
    return text


def describe_tx(tx: dict, today: date) -> str:
    return describe_entry({"type": tx["type"], "amount": tx["amount"], "category": tx["category"],
                           "platform": tx["platform"], "date": tx["occurred_on"]}, today)


HELP = ("I can log your earnings and expenses. Say for example: Got 1400 from Swiggy, spent 250 on petrol. "
        "You can also ask: How much did I earn this week?")
NOT_UNDERSTOOD = ("Sorry, I could not understand. Try: Got 1400 from Swiggy, or spent 250 on petrol, "
                  "or ask: How much did I earn this week?")
NOTHING_PENDING = "There is nothing waiting for confirmation."
NO_ENTRIES = "You have no saved entries yet."
BAD_AMOUNT = "I could not use that amount. Amounts must be between ₹1 and ₹50,000. Please say it again."
