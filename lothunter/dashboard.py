"""Server-rendered pages for the loopback dashboard."""
from dataclasses import fields
from html import escape
from urllib.parse import urlencode

from . import registry
from .valuation import Fees, PATHS, SCENARIOS, Recovery, Scenario, evaluate, number, units

PATH_LABELS = {"whole": "Whole-unit resale", "refurbish": "Refurbish and resell",
               "parts": "Parts recovered per donor"}
RECOVERY_LABELS = {
    "quantity": "Allocated units",
    "price": "Resale price per unit or donor bundle (£)",
    "unsold_pct": "Unsold at regular price (%)",
    "unsold_value": "Clearance value per unsold unit (£)",
    "processing": "Testing, repair and materials per unit (£)",
    "labour": "Labour per unit (£)",
    "selling_fee_pct": "Selling fees (% of revenue)",
    "packing_postage": "Packing and outbound postage per shipment (£)",
    "returns_pct": "Returns and refund reserve (% of revenue)",
}
SCENARIO_LABELS = {
    "failed": "Failed or unrecoverable units",
    "failed_unit_cost": "Testing and disposal per failed unit (£)",
    "overhead": "Lot overhead and storage (£)",
    "contingency": "Risk contingency (£)",
    "margin_pct": "Required profit (% of revenue)",
}
MONEY_RESULTS = {
    "gross": "Estimated revenue", "operating_costs": "Operating costs and reserves",
    "required_profit": "Required profit", "maximum_bid": "Maximum hammer bid",
    "landed": "Landed acquisition cost", "profit": "Estimated profit",
}
RESULT_DETAILS = {
    "hammer": "Hammer or asking price", "buyer_fee": "Buyer fee",
    "hammer_vat": "VAT on goods", "fee_vat": "VAT on buyer fees",
    "inbound": "Inbound logistics", "inbound_vat": "VAT on inbound logistics",
    "processing": "Testing, repairs and materials", "labour": "Labour",
    "selling_fee": "Selling fees", "packing_postage": "Outbound postage and packaging",
    "returns": "Returns and refunds", "failed": "Failed-unit processing",
    "overhead": "Overhead and storage", "contingency": "Risk contingency",
}


def h(value):
    return escape(str(value), quote=True)


def gbp(value):
    return "—" if value is None else f"£{value:,.2f}"


def page(title, body, error=""):
    alert = f'<p class="alert" role="alert">{h(error)}</p>' if error else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{h(title)} · LotHunter UK</title><link rel="stylesheet" href="/style.css">
</head><body><header><nav aria-label="Main">
<a class="brand" href="/">LotHunter UK</a><a href="/">Listings</a>
<a href="/new">Add listing</a><a href="/import">Import CSV</a></nav></header>
<main><h1>{h(title)}</h1>{alert}{body}</main></body></html>"""


def form_start(action, token, extra=""):
    return (f'<form action="{h(action)}" method="post" {extra}>'
            f'<input type="hidden" name="_csrf" value="{h(token)}">')


def overview(listings):
    columns = ("Listing", "Source", "Quantity", "Listing price", "Evidence confidence", "Review")
    header = "".join(f'<th scope="col">{title}</th>' for title in columns)
    rows = []
    for entry in listings:
        record = entry["record"]
        confidence, _ = registry.assess_confidence(record)
        price = gbp(number(record["price"], "Price")) if record["price"] else "Unknown"
        cells = (f'<a href="/listing/{entry["id"]}">{h(record["title"])}</a>',
                 h(record["source"]), h(record["quantity"] or "Unknown"), price,
                 h(confidence.capitalize()), h(record["review_status"].capitalize()))
        rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    if not rows:
        return page("Listings", '<p>No listings yet. <a href="/new">Add a listing</a> or '
                    '<a href="/import">import a CSV</a>.</p>')
    return page("Listings", f'<div class="scroll"><table><thead><tr>{header}</tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table></div>')


def listing_form(record, token, item=None, error=""):
    route = f"/listing/{item}/edit" if item else "/new"
    title = "Edit listing" if item else "Add listing"
    back = f'<p><a href="/listing/{item}">Back to listing</a></p>' if item else ""
    options = {"confidence": registry.CONFIDENCE_STATES, "review_status": registry.REVIEW_STATES}
    controls = []
    for key, label in registry.LABELS.items():
        value = record.get(key, "")
        if key in options:
            control = f'<select name="{key}">' + "".join(
                f'<option{" selected" if value == choice else ""}>{choice}</option>'
                for choice in options[key]
            ) + "</select>"
        elif key in ("description", "condition", "evidence"):
            control = f'<textarea name="{key}" rows="3" maxlength="12000">{h(value)}</textarea>'
        else:
            required = " required" if key in registry.REQUIRED_HEADERS else ""
            readonly = " readonly" if item and key in {"source", "listing_id"} else ""
            control = f'<input name="{key}" value="{h(value)}"{required}{readonly}>'
        controls.append(f"<label>{h(label)}{control}</label>")
    return page(title, back + '<p>Leave unknown details blank. Enter zero for confirmed zero fees or VAT.</p>'
                + form_start(route, token) + f'<div class="grid">{"".join(controls)}</div>'
                + '<p><button>Save listing</button></p></form>', error)


def description_list(values):
    return '<dl>' + "".join(f"<dt>{h(label)}</dt><dd>{h(value)}</dd>" for label, value in values) + "</dl>"


def listing_detail(entry, duplicate=False, demo=False):
    item, record = entry["id"], entry["record"]
    confidence, missing = registry.assess_confidence(record)
    source_link = (f'<p><a href="{h(record["url"])}" rel="noreferrer">Open original listing</a></p>'
                   if record["url"] else "")
    notice = ('<p role="status">This identity already exists. New evidence was attached to '
              'the existing listing.</p>' if duplicate else "")
    risk = f'<p class="notice">Evidence confidence <strong>{h(confidence)}</strong>.'
    if missing:
        risk += f' Missing or unresolved details include {h(", ".join(missing))}.'
    risk += '</p>'
    actions = (f'<p class="actions"><a href="/listing/{item}/edit">Edit listing</a>'
               f'<a href="/listing/{item}/value">Open calculator</a>')
    if demo:
        actions += f'<a href="/listing/{item}/value?demo=1">Load illustrative scenarios</a>'
    actions += '</p>'
    metadata = [("First seen (UTC)", entry["first_seen"]), ("Last seen (UTC)", entry["last_seen"])]
    metadata.extend((registry.LABELS[key], value) for key, value in record.items()
                    if key != "title" and value)
    observations = []
    for observation in entry["observations"]:
        summary = f'{observation["observed_at"]} · {observation["method"]}'
        submitted = [(registry.LABELS[key], value)
                     for key, value in observation["record"].items() if value]
        observations.append(f'<details><summary>{h(summary)}</summary>'
                            f'{description_list(submitted)}</details>')
    return page(record["title"], notice + actions + risk + source_link + description_list(metadata)
                + '<h2>Submitted observations</h2>' + "".join(observations))


def import_form(token, error="", result=None):
    notice = ""
    if result:
        notice = (f'<p role="status">Added {result[0]} listing(s) and matched '
                  f'{result[1]} duplicate(s). <a href="/">View listings</a>.</p>')
    return page("Import listings", notice
                + '<p><a href="/template.csv">Download CSV template</a> · '
                  '<a href="/examples.csv">Download illustrative lots</a></p>'
                  '<p>Upload UTF-8 CSV. Existing listings keep their edited details and gain new evidence where supplied.</p>'
                + form_start("/import", token, 'enctype="multipart/form-data"')
                + '<label>CSV file<input name="file" type="file" accept=".csv,text/csv" required></label>'
                  '<p><button>Import listings</button></p></form>', error)


def calculator_defaults(record):
    values = {"quantity": record["quantity"], "hammer": record["price"]}
    fee_defaults = Fees()
    values.update({key: record[key] or str(getattr(fee_defaults, key)) for key in registry.FEE_FIELDS})
    for name in SCENARIOS:
        values.update({f"{name}.{key}": "0" for key in SCENARIO_LABELS})
        values[f"{name}.failed"] = record["quantity"]
        values.update({f"{name}.{path}.{field.name}": "0" for path in PATHS for field in fields(Recovery)})
    return values


def calculate(values):
    quantity = units(values.get("quantity", ""), "Lot quantity", minimum=1)
    hammer_text = values.get("hammer", "").strip()
    hammer = number(hammer_text, "Hammer or asking price") if hammer_text else None
    fees = Fees(**{key: number(values.get(key, ""), registry.LABELS[key]) for key in registry.FEE_FIELDS})
    results = {}
    for name in SCENARIOS:
        try:
            outcomes = {}
            for path in PATHS:
                prefix = f"{name}.{path}."
                parsed = {key: (units if key == "quantity" else number)(
                    values.get(prefix + key, ""), f"{PATH_LABELS[path]} / {label}"
                ) for key, label in RECOVERY_LABELS.items()}
                outcomes[path] = Recovery(**parsed)
            settings = {key: (units if key == "failed" else number)(
                values.get(f"{name}.{key}", ""), label
            ) for key, label in SCENARIO_LABELS.items()}
            results[name] = evaluate(quantity, fees, Scenario(**outcomes, **settings), hammer)
        except ValueError as error:
            raise ValueError(f"{name.capitalize()} scenario. {error}") from None
    return results


def result_view(results):
    headers = "".join(f'<th scope="col">{name.capitalize()}</th>' for name in results)
    rows = []
    for key, label in (*MONEY_RESULTS.items(), ("roi_pct", "ROI on total outlay"), ("status", "Margin status")):
        cells = []
        for result in results.values():
            value = result[key]
            if key == "roi_pct":
                display = "—" if value is None else f"{value:,.2f}%"
            elif key == "status":
                display = value
            elif key == "maximum_bid" and value is None:
                display = "No viable bid"
            else:
                display = gbp(value)
            cells.append(f"<td>{h(display)}</td>")
        rows.append(f'<tr><th scope="row">{h(label)}</th>{"".join(cells)}</tr>')
    details = []
    for name, result in results.items():
        lines = {**(result["invoice"] or {}), **result["costs"]}
        details.append(f'<details><summary>{name.capitalize()} cost breakdown</summary>'
                       + description_list((RESULT_DETAILS[key], gbp(value)) for key, value in lines.items())
                       + '</details>')
    return ('<section id="results"><h2>Scenario results</h2><div class="scroll"><table><thead>'
            f'<tr><th scope="col">Estimate</th>{headers}</tr></thead><tbody>{"".join(rows)}'
            f'</tbody></table></div>{"".join(details)}</section>')


def calculator(entry, values, token, results=None, error="", illustrative=False):
    item, record = entry["id"], entry["record"]
    controls = []

    def control(name, label, optional=False):
        required = "" if optional else " required"
        return (f'<label>{h(label)}<input name="{h(name)}" inputmode="decimal"'
                f' value="{h(values.get(name, ""))}"{required}></label>')

    for scenario in SCENARIOS:
        outcomes = []
        for path in PATHS:
            inputs = "".join(control(f"{scenario}.{path}.{key}", label) for key, label in RECOVERY_LABELS.items())
            outcomes.append(f'<fieldset><legend>{h(PATH_LABELS[path])}</legend><div class="grid">{inputs}</div></fieldset>')
        settings = "".join(control(f"{scenario}.{key}", label) for key, label in SCENARIO_LABELS.items())
        controls.append(f'<details open><summary>{scenario.capitalize()} scenario</summary>'
                        f'{"".join(outcomes)}<div class="grid">{settings}</div></details>')
    fees = "".join(control(key, registry.LABELS[key]) for key in registry.FEE_FIELDS)
    confidence, missing = registry.assess_confidence(record)
    risk = f'<p class="notice">Listing evidence confidence <strong>{h(confidence)}</strong>. '
    if missing:
        risk += f'Unresolved listing details include {h(", ".join(missing))}. '
    risk += 'Verify fees, VAT and resale comparisons before using a bid ceiling.</p>'
    note = ('<p class="alert">Illustrative scenario assumptions. Prices, yields and fees are examples '
            'and have no market verification.</p>' if illustrative else "")
    action = f'/listing/{item}/value'
    if illustrative:
        action += "?" + urlencode({"demo": 1})
    body = (f'<p><a href="/listing/{item}">{h(record["title"])}</a></p>{risk}{note}'
            + (result_view(results) if results else "")
            + '<p>Allocate every acquired unit once per scenario. A parts allocation represents the '
              'combined value of components from one donor. A blank purchase price leaves profit and ROI unknown.</p>'
            + form_start(action, token)
            + '<fieldset><legend>Acquisition inputs</legend>'
              '<p>Blank listing fee fields initialise to zero and bid defaults to £1. Replace them with verified terms. '
              'VAT uses separate cash charges for goods, fees and inbound costs.</p>'
            + '<div class="grid">' + control("quantity", "Lot quantity") + control("hammer", "Hammer or asking price (£)", True)
            + fees + '</div></fieldset>' + "".join(controls)
            + '<p><button>Calculate scenarios</button></p></form>')
    return page("Scenario calculator", body, error)
