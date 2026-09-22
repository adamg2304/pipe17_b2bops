"""Render Branch order slips / packing lists to PDF (wkhtmltopdf via pdfkit).

order_slip_html(order)      -> HTML for an ORDER-level slip (parent products + prices)
packing_list_html(shipment) -> HTML for a SHIPMENT-level packing list (child/box SKUs, no prices)
render_pdf(html)            -> PDF bytes

The container/image must have wkhtmltopdf installed (headless build). See Dockerfile.
"""
import pdfkit

import base64 as _b64
import os as _os

def _logo_data_uri():
    """Branch logo as a data URI (bundled asset next to this module)."""
    p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "assets", "branch_logo.png")
    try:
        with open(p, "rb") as f:
            return "data:image/png;base64," + _b64.b64encode(f.read()).decode()
    except OSError:
        return ""

_LOGO = _logo_data_uri()

_WKHTMLTOPDF = _os.environ.get("WKHTMLTOPDF_BIN", "/usr/bin/wkhtmltopdf")
_OPTS = {"page-size": "Letter", "margin-top": "0", "margin-bottom": "0",
         "margin-left": "0", "margin-right": "0", "encoding": "UTF-8",
         "quiet": "", "disable-smart-shrinking": ""}


def _money(v, currency="USD"):
    try:
        sym = "$" if currency == "USD" else ("CA$" if currency == "CAD" else "")
        return f"{sym}{float(v):,.2f}"
    except (TypeError, ValueError):
        return ""


def _esc(s):
    return (str(s or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _addr_block(addr):
    name = " ".join(x for x in [addr.get("firstName"), addr.get("lastName")] if x).strip()
    city_line = " ".join(x for x in [addr.get("city"), addr.get("stateOrProvince"),
                                     addr.get("zipCodeOrPostalCode")] if x)
    lines = [name, addr.get("company"), addr.get("address1"), addr.get("address2"),
             city_line, addr.get("country")]
    return "<br>".join(_esc(x) for x in lines if x) or "&nbsp;"


_BASE_CSS = """
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: Arial, Helvetica, sans-serif; font-size: 12.5px; color: #1a1a1a;
         padding: 44px 52px; }
  .top { border-bottom: 3px solid #1a1a1a; padding-bottom: 14px; margin-bottom: 22px; }
  .brand { font-size: 22px; font-weight: 800; letter-spacing: .5px; }
  .logo { height: 34px; }
  .doctype { float: right; font-size: 20px; font-weight: 700; color: #444; text-transform: uppercase;
             letter-spacing: 2px; }
  .meta { margin: 18px 0 26px; font-size: 12px; color: #333; }
  .meta b { display:inline-block; min-width: 92px; color:#666; font-weight:600; }
  table.addr { width: 100%; margin-bottom: 26px; border-collapse: collapse; }
  table.addr td { width: 50%; vertical-align: top; font-size: 12px; line-height: 1.6; padding-right: 18px; }
  .lbl { font-size: 10px; text-transform: uppercase; letter-spacing: .8px; color:#888;
         font-weight:700; margin-bottom: 4px; }
  table.items { width: 100%; border-collapse: collapse; margin-top: 4px; }
  table.items thead th { border-top: 2px solid #1a1a1a; border-bottom: 2px solid #1a1a1a;
         font-size: 10.5px; text-transform: uppercase; letter-spacing:.5px; padding: 8px; text-align:left; }
  table.items thead th.num, table.items td.num { text-align: right; }
  table.items td { padding: 9px 8px; border-bottom: 1px solid #e6e6e6; vertical-align: top; }
  .nm { font-weight: 600; }
  .sku { font-size: 10.5px; color:#777; margin-top: 2px; }
  .totals { margin-top: 14px; width: 100%; }
  .totals td { padding: 3px 8px; font-size: 12.5px; }
  .totals .lab { text-align: right; color:#555; }
  .totals .val { text-align: right; width: 130px; font-weight: 600; }
  .totals .grand td { border-top: 2px solid #1a1a1a; font-size: 14px; font-weight: 800; padding-top: 8px; }
  .notice { background:#fff8f0; border-left: 3px solid #e07000; padding: 10px 14px; font-size: 11px;
            line-height:1.5; margin: 4px 0 18px; }
  .foot { margin-top: 40px; text-align:center; color:#bbb; font-size: 16px; letter-spacing: 8px; }
"""


def order_slip_html(order):
    addr = order.get("shippingAddress") or {}
    cur = order.get("currency", "USD")
    order_no = _esc(order.get("extOrderId"))
    date = _esc((order.get("extOrderCreatedAt") or order.get("createdAt") or "")[:10])

    rows = ""
    for li in order.get("lineItems") or []:
        qty = li.get("quantity") or 0
        price = li.get("itemPrice") or 0
        rows += (f'<tr><td><div class="nm">{_esc(li.get("name"))}</div>'
                 f'<div class="sku">{_esc(li.get("sku"))}</div></td>'
                 f'<td class="num">{qty}</td>'
                 f'<td class="num">{_money(price, cur)}</td>'
                 f'<td class="num">{_money((qty or 0) * (price or 0), cur)}</td></tr>')

    subtotal = order.get("subTotalPrice")
    total = order.get("totalPrice")
    tax = order.get("orderTax")
    totals = f'<tr><td class="lab">Subtotal</td><td class="val">{_money(subtotal, cur)}</td></tr>'
    if tax:
        totals += f'<tr><td class="lab">Tax</td><td class="val">{_money(tax, cur)}</td></tr>'
    totals += f'<tr class="grand"><td class="lab">Total ({_esc(cur)})</td><td class="val">{_money(total, cur)}</td></tr>'

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>{_BASE_CSS}</style></head><body>
  <div class="top"><span class="doctype">Order Slip</span>{("<img class=\"logo\" src=\"" + _LOGO + "\"/>") if _LOGO else "<span class=\"brand\">BRANCH</span>"}</div>
  <div class="meta"><div><b>Order #</b> {order_no}</div><div><b>Order Date</b> {date}</div></div>
  <table class="addr"><tr>
    <td><div class="lbl">Ship To</div>{_addr_block(addr)}</td>
    <td><div class="lbl">Contact</div>{_esc(addr.get("email")) or "&nbsp;"}<br>{_esc(addr.get("phone"))}</td>
  </tr></table>
  <table class="items"><thead><tr>
    <th>Item</th><th class="num">Qty</th><th class="num">Unit</th><th class="num">Amount</th>
  </tr></thead><tbody>{rows}</tbody></table>
  <table class="totals" align="right"><tr><td></td></tr>{totals}</table>
  <div class="foot">&#10022; &#10022; &#10022;</div>
</body></html>"""


def packing_list_html(shipment):
    """Shipment-level packing list: child/box SKUs, quantities, NO prices."""
    addr = shipment.get("shippingAddress") or {}
    ship_no = _esc(shipment.get("extShipmentId"))
    date = _esc((shipment.get("createdAt") or "")[:10])
    rows = ""
    for li in shipment.get("lineItems") or []:
        rows += (f'<tr><td><div class="nm">{_esc(li.get("name"))}</div>'
                 f'<div class="sku">{_esc(li.get("sku"))}</div></td>'
                 f'<td class="num">{li.get("quantity") or 0}</td></tr>')
    notice = ('<div class="notice"><b>Packing list</b> &mdash; pack the box-level items below. '
              'Quantities are per this shipment.</div>')
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>{_BASE_CSS}</style></head><body>
  <div class="top"><span class="doctype">Packing List</span>{("<img class=\"logo\" src=\"" + _LOGO + "\"/>") if _LOGO else "<span class=\"brand\">BRANCH</span>"}</div>
  <div class="meta"><div><b>Shipment #</b> {ship_no}</div><div><b>Date</b> {date}</div></div>
  <table class="addr"><tr><td><div class="lbl">Ship To</div>{_addr_block(addr)}</td><td></td></tr></table>
  {notice}
  <table class="items"><thead><tr><th>Item</th><th class="num">Qty</th></tr></thead>
    <tbody>{rows}</tbody></table>
  <div class="foot">&#10022; &#10022; &#10022;</div>
</body></html>"""


def render_pdf(html):
    cfg = pdfkit.configuration(wkhtmltopdf=_WKHTMLTOPDF)
    return pdfkit.from_string(html, False, options=_OPTS, configuration=cfg)
