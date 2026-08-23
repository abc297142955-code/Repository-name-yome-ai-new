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
6. Keep display as **WooCommerce My Account only** for member-area behavior.

Membership behavior
-------------------

- If WooCommerce Memberships is installed, the widget only appears for active
  members.
- Otherwise, it appears for logged-in WordPress/WooCommerce customers.
- You can also place `[yome_assistant]` on a member-only page.
- The second product price is shown to customers as the YOME member price.
- If a customer asks to order an amount such as `RD$7800`, the assistant starts
  an order draft and collects name, delivery zone/address, and payment method.
