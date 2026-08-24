YOME WooCommerce Assistant
==========================

This repository now includes a small WordPress plugin that connects logged-in
WooCommerce members to the existing YOME AI service.

Files
-----

- `app.py`
  - Adds `POST /site-chat`, a website-safe chat endpoint that reuses the
    existing YOME customer reply logic.
- `wordpress-plugin/yome-woocommerce-assistant.php`
  - Single-file WordPress plugin.
  - Shows a cartoon YOME assistant on WooCommerce My Account pages by default.
  - Adds a **YOME助手** tab inside WooCommerce My Account for the member page.
  - Uses an animated YOME robot with hover/bob, blinking eyes, and glowing antenna.
  - Can read the separate **YOME · INVENTARIO** JSON API and send that inventory
    context to the AI.
  - Also exposes a built-in bridge API at
    `/wp-json/yome-assistant/v1/inventory` and can auto-read likely YOME
    inventory tables from the same WordPress database when **Inventory API URL**
    is left blank.
  - If no separate YOME inventory table is found, it falls back to live
    WooCommerce/member-system product inventory. It does not use the POS/caja
    catalog route.
  - The settings page shows an admin-only inventory preview, so admins can test
    live inventory without opening the protected REST API directly.
  - Requests inventory live for each customer question with no-cache headers and
    a cache-busting timestamp, so old product data is not reused.
  - Proxies chat through WordPress AJAX so the widget key is not exposed in the
    browser.

Flask / Railway environment
---------------------------

Recommended variables:

- `YOME_SITE_WIDGET_KEY`
  - Shared secret used by the WordPress plugin when calling `/site-chat`.
- `YOME_SITE_ALLOWED_ORIGIN`
  - Optional CORS origin, for example `https://yome.it.com`.

WordPress install
-----------------

1. Copy `wordpress-plugin/yome-woocommerce-assistant.php` to:
   `wp-content/plugins/yome-woocommerce-assistant/yome-woocommerce-assistant.php`
2. Activate **YOME WooCommerce Assistant** in WordPress plugins.
   Re-save **Settings > Permalinks** if the YOME助手 account tab does not appear.
3. Open **WooCommerce > YOME Assistant**.
4. Set **YOME chat API** to:
   `https://repository-name-yome-ai-new-production.up.railway.app/site-chat`
5. Set **Widget key** to the same value as `YOME_SITE_WIDGET_KEY`.
6. Enable **YOME · INVENTARIO**. Leave **Inventory API URL** blank to let the
   plugin auto-read live inventory tables from the same WordPress database. If
   no separate inventory table is found, it will read WooCommerce/member-system
   product inventory live.
7. Optional: use the built-in API URL
   `https://yome.it.com/wp-json/yome-assistant/v1/inventory` for testing after
   the plugin is active. It accepts `q` and `limit`.
   A direct browser request can return `401 rest_forbidden`; that only means the
   REST endpoint is protected. The member chat and settings preview read the
   inventory server-side.
8. Admin-only diagnostics:
   `https://yome.it.com/wp-json/yome-assistant/v1/inventory-debug`.
9. If YOME · INVENTARIO requires a login, fill **Inventory username** and
   **Inventory password** in WordPress settings. Do not commit credentials to GitHub.
10. Keep display as **WooCommerce My Account only** for member-area behavior.

Membership behavior
-------------------

- If WooCommerce Memberships is installed, the widget only appears for active
  members.
- Otherwise, it appears for logged-in WordPress/WooCommerce customers.
- You can also place `[yome_assistant]` on a member-only page.
- The second product price is shown to customers as the YOME member price.
- If a customer asks to order an amount such as `RD$7800`, the assistant starts
  an order draft and collects name, delivery zone/address, and payment method.
- When a member asks `que mercancia hay`, `que productos tienen`, `que nuevo hay`,
  or a specific product name, WordPress queries **YOME · INVENTARIO** and sends
  product/stock data to the AI.
- If **Inventory API URL** is blank, the plugin scans likely custom inventory
  tables such as names containing `yome`, `invent`, `stock`, `tienda`, or
  `almacen`, maps product fields, and sends the latest rows to the AI.
- If those tables are not found, the plugin reads WooCommerce/member-system
  products directly with `wc_get_products` and product stock fields. It does not
  use the WooCommerce POS/caja catalog route.
- Member chat product questions require live YOME · INVENTARIO data. If the live
  inventory API is not configured or returns no data, `/site-chat` will not fall
  back to the older local `products.csv` catalog.
- The inventory API can return a list directly, or JSON with `items`, `products`,
  `data`, `rows`, or `inventory`. Supported item fields include `name`,
  `product_name`, `sku`, `code`, `stock`, `quantity`, `store`, `price`,
  `member_price`, `image`, and `url`.
