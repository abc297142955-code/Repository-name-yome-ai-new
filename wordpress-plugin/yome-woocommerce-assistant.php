<?php
/**
 * Plugin Name: YOME WooCommerce Assistant
 * Description: Shows a cartoon YOME assistant for logged-in WooCommerce members and proxies questions to the YOME AI service.
 * Version: 1.0.0
 * Author: YOME
 * Text Domain: yome-woocommerce-assistant
 */

if (!defined('ABSPATH')) {
    exit;
}

final class YOME_WooCommerce_Assistant {
    private const OPTION_KEY = 'yome_woocommerce_assistant_options';
    private const NONCE_ACTION = 'yome_assistant_chat';
    private static $assets_loaded = false;

    public static function init(): void {
        add_action('init', [__CLASS__, 'register_account_endpoint']);
        add_action('admin_menu', [__CLASS__, 'admin_menu']);
        add_action('wp_enqueue_scripts', [__CLASS__, 'enqueue_widget']);
        add_action('wp_footer', [__CLASS__, 'render_widget']);
        add_action('wp_ajax_yome_assistant_chat', [__CLASS__, 'ajax_chat']);
        add_filter('woocommerce_account_menu_items', [__CLASS__, 'account_menu_items']);
        add_action('woocommerce_account_yome-assistant_endpoint', [__CLASS__, 'account_endpoint_content']);
        add_shortcode('yome_assistant', [__CLASS__, 'shortcode']);
    }

    public static function activate(): void {
        self::register_account_endpoint();
        flush_rewrite_rules();
    }

    public static function deactivate(): void {
        flush_rewrite_rules();
    }

    public static function register_account_endpoint(): void {
        add_rewrite_endpoint('yome-assistant', EP_ROOT | EP_PAGES);
    }

    public static function account_menu_items(array $items): array {
        $new_items = [];
        foreach ($items as $key => $label) {
            if ($key === 'customer-logout') {
                $new_items['yome-assistant'] = 'YOME助手';
            }
            $new_items[$key] = $label;
        }

        if (!isset($new_items['yome-assistant'])) {
            $new_items['yome-assistant'] = 'YOME助手';
        }

        return $new_items;
    }

    public static function account_endpoint_content(): void {
        if (!self::user_can_chat()) {
            echo '<p>YOME助手 está disponible para miembros con sesión iniciada.</p>';
            return;
        }

        self::enqueue_widget(true);
        echo '<div class="yome-member-assistant-page">';
        echo '<h2>YOME助手</h2>';
        self::widget_markup('inline');
        echo '</div>';
    }

    private static function defaults(): array {
        return [
            'endpoint_url' => 'https://repository-name-yome-ai-new-production.up.railway.app/site-chat',
            'widget_key' => '',
            'inventory_enabled' => 'no',
            'inventory_api_url' => '',
            'inventory_api_key' => '',
            'display_scope' => 'account',
            'enabled' => 'yes',
        ];
    }

    private static function options(): array {
        $saved = get_option(self::OPTION_KEY, []);
        return wp_parse_args(is_array($saved) ? $saved : [], self::defaults());
    }

    public static function admin_menu(): void {
        $parent = class_exists('WooCommerce') ? 'woocommerce' : 'options-general.php';
        add_submenu_page(
            $parent,
            'YOME Assistant',
            'YOME Assistant',
            'manage_options',
            'yome-assistant',
            [__CLASS__, 'settings_page']
        );
    }

    public static function settings_page(): void {
        if (!current_user_can('manage_options')) {
            wp_die(esc_html__('You do not have permission to access this page.', 'yome-woocommerce-assistant'));
        }

        $options = self::options();

        if ($_SERVER['REQUEST_METHOD'] === 'POST' && check_admin_referer('yome_assistant_settings')) {
            $endpoint_url = esc_url_raw(wp_unslash($_POST['endpoint_url'] ?? ''));
            $widget_key = sanitize_text_field(wp_unslash($_POST['widget_key'] ?? ''));
            $inventory_enabled = !empty($_POST['inventory_enabled']) ? 'yes' : 'no';
            $inventory_api_url = esc_url_raw(wp_unslash($_POST['inventory_api_url'] ?? ''));
            $inventory_api_key = sanitize_text_field(wp_unslash($_POST['inventory_api_key'] ?? ''));
            $display_scope = sanitize_key(wp_unslash($_POST['display_scope'] ?? 'account'));
            $enabled = !empty($_POST['enabled']) ? 'yes' : 'no';

            if (!in_array($display_scope, ['account', 'site', 'shortcode'], true)) {
                $display_scope = 'account';
            }

            $options = [
                'endpoint_url' => $endpoint_url,
                'widget_key' => $widget_key,
                'inventory_enabled' => $inventory_enabled,
                'inventory_api_url' => $inventory_api_url,
                'inventory_api_key' => $inventory_api_key,
                'display_scope' => $display_scope,
                'enabled' => $enabled,
            ];
            update_option(self::OPTION_KEY, $options, false);

            echo '<div class="notice notice-success"><p>YOME Assistant settings saved.</p></div>';
        }

        ?>
        <div class="wrap">
            <h1>YOME Assistant</h1>
            <form method="post">
                <?php wp_nonce_field('yome_assistant_settings'); ?>
                <table class="form-table" role="presentation">
                    <tr>
                        <th scope="row"><label for="endpoint_url">YOME chat API</label></th>
                        <td>
                            <input name="endpoint_url" id="endpoint_url" type="url" class="regular-text"
                                   value="<?php echo esc_attr($options['endpoint_url']); ?>" />
                            <p class="description">Example: https://your-railway-app/site-chat</p>
                        </td>
                    </tr>
                    <tr>
                        <th scope="row"><label for="widget_key">Widget key</label></th>
                        <td>
                            <input name="widget_key" id="widget_key" type="password" class="regular-text"
                                   value="<?php echo esc_attr($options['widget_key']); ?>" autocomplete="new-password" />
                            <p class="description">Use the same value as the Flask environment variable YOME_SITE_WIDGET_KEY.</p>
                        </td>
                    </tr>
                    <tr>
                        <th scope="row">YOME · INVENTARIO</th>
                        <td>
                            <label>
                                <input name="inventory_enabled" type="checkbox" value="yes" <?php checked($options['inventory_enabled'], 'yes'); ?> />
                                Use YOME inventory data for AI replies
                            </label>
                        </td>
                    </tr>
                    <tr>
                        <th scope="row"><label for="inventory_api_url">Inventory API URL</label></th>
                        <td>
                            <input name="inventory_api_url" id="inventory_api_url" type="url" class="regular-text"
                                   value="<?php echo esc_attr($options['inventory_api_url']); ?>" />
                            <p class="description">The YOME · INVENTARIO endpoint that returns JSON products/stock. It can receive q and limit query parameters.</p>
                        </td>
                    </tr>
                    <tr>
                        <th scope="row"><label for="inventory_api_key">Inventory API key</label></th>
                        <td>
                            <input name="inventory_api_key" id="inventory_api_key" type="password" class="regular-text"
                                   value="<?php echo esc_attr($options['inventory_api_key']); ?>" autocomplete="new-password" />
                            <p class="description">Optional. Sent as X-YOME-Inventory-Key and Bearer authorization.</p>
                        </td>
                    </tr>
                    <tr>
                        <th scope="row">Display</th>
                        <td>
                            <select name="display_scope">
                                <option value="account" <?php selected($options['display_scope'], 'account'); ?>>WooCommerce My Account only</option>
                                <option value="site" <?php selected($options['display_scope'], 'site'); ?>>All logged-in site pages</option>
                                <option value="shortcode" <?php selected($options['display_scope'], 'shortcode'); ?>>Shortcode only</option>
                            </select>
                            <p class="description">Shortcode: [yome_assistant]</p>
                        </td>
                    </tr>
                    <tr>
                        <th scope="row">Enabled</th>
                        <td>
                            <label>
                                <input name="enabled" type="checkbox" value="yes" <?php checked($options['enabled'], 'yes'); ?> />
                                Show YOME Assistant
                            </label>
                        </td>
                    </tr>
                </table>
                <?php submit_button(); ?>
            </form>
        </div>
        <?php
    }

    private static function user_can_chat(): bool {
        if (!is_user_logged_in()) {
            return false;
        }

        if (function_exists('wc_memberships_is_user_active_member')) {
            return (bool) wc_memberships_is_user_active_member(get_current_user_id());
        }

        return true;
    }

    private static function should_render(): bool {
        $options = self::options();
        if ($options['enabled'] !== 'yes' || !self::user_can_chat()) {
            return false;
        }

        if ($options['display_scope'] === 'site') {
            return true;
        }

        if ($options['display_scope'] === 'account') {
            return function_exists('is_account_page') && is_account_page();
        }

        return false;
    }

    public static function shortcode(): string {
        if (!self::user_can_chat()) {
            return '';
        }

        self::enqueue_widget(true);
        ob_start();
        self::widget_markup('inline');
        return (string) ob_get_clean();
    }

    public static function enqueue_widget(bool $force = false): void {
        if (!$force && !self::should_render()) {
            return;
        }

        if (self::$assets_loaded) {
            return;
        }
        self::$assets_loaded = true;

        wp_register_style('yome-assistant-widget', false, [], '1.0.0');
        wp_enqueue_style('yome-assistant-widget');
        wp_add_inline_style('yome-assistant-widget', self::css());

        wp_register_script('yome-assistant-widget', false, [], '1.0.0', true);
        wp_enqueue_script('yome-assistant-widget');
        wp_add_inline_script('yome-assistant-widget', 'window.YOMEAssistantConfig = ' . wp_json_encode([
            'ajaxUrl' => admin_url('admin-ajax.php'),
            'nonce' => wp_create_nonce(self::NONCE_ACTION),
            'userId' => get_current_user_id(),
        ]) . ';', 'before');
        wp_add_inline_script('yome-assistant-widget', self::js());
    }

    public static function render_widget(): void {
        if (!self::should_render()) {
            return;
        }

        if (function_exists('is_wc_endpoint_url') && is_wc_endpoint_url('yome-assistant')) {
            return;
        }

        self::widget_markup('floating');
    }

    private static function widget_markup(string $mode): void {
        $class = $mode === 'inline' ? 'yome-assistant yome-assistant-inline' : 'yome-assistant yome-assistant-floating';
        ?>
        <div class="<?php echo esc_attr($class); ?>" data-yome-assistant>
            <button class="yome-assistant-launcher" type="button" aria-label="Open YOME Assistant">
                <?php self::robot_markup(); ?>
                <span class="yome-launcher-text">YOME助手</span>
            </button>
            <section class="yome-chat-panel" aria-live="polite">
                <div class="yome-chat-head">
                    <?php self::robot_markup('small'); ?>
                    <div>
                        <strong>YOME Assistant</strong>
                        <span>Productos, precios, pedidos y ayuda</span>
                    </div>
                    <button class="yome-chat-close" type="button" aria-label="Close">×</button>
                </div>
                <div class="yome-chat-messages">
                    <div class="yome-msg yome-msg-bot">Hola 😊 Soy el asistente de YOME. Pregúntame sobre productos, precios, entrega, pagos o pedidos.</div>
                </div>
                <form class="yome-chat-form">
                    <input type="text" name="message" autocomplete="off" placeholder="Escribe tu pregunta sobre YOME..." />
                    <button type="submit">Enviar</button>
                </form>
            </section>
        </div>
        <?php
    }

    private static function robot_markup(string $class = ''): void {
        $classes = trim('yome-robot ' . $class);
        ?>
        <span class="<?php echo esc_attr($classes); ?>" aria-hidden="true">
            <span class="yome-robot-antenna"></span>
            <span class="yome-robot-head">
                <span class="yome-eye left"></span>
                <span class="yome-eye right"></span>
                <span class="yome-robot-mouth"></span>
            </span>
            <span class="yome-robot-body"><span>Y</span></span>
            <span class="yome-robot-arm left"></span>
            <span class="yome-robot-arm right"></span>
        </span>
        <?php
    }

    public static function ajax_chat(): void {
        check_ajax_referer(self::NONCE_ACTION, 'nonce');

        if (!self::user_can_chat()) {
            wp_send_json_error(['message' => 'Please log in to use YOME Assistant.'], 403);
        }

        $options = self::options();
        $endpoint = esc_url_raw($options['endpoint_url']);
        $message = sanitize_textarea_field(wp_unslash($_POST['message'] ?? ''));

        if (!$endpoint || !$message) {
            wp_send_json_error(['message' => 'Missing message or YOME API URL.'], 400);
        }

        $user = wp_get_current_user();
        $headers = ['Content-Type' => 'application/json'];
        if (!empty($options['widget_key'])) {
            $headers['X-YOME-Widget-Key'] = $options['widget_key'];
        }
        $inventory_context = self::yome_inventory_context($message, $options);

        $response = wp_remote_post($endpoint, [
            'timeout' => 20,
            'headers' => $headers,
            'body' => wp_json_encode([
                'message' => $message,
                'member_id' => get_current_user_id(),
                'member_email' => $user ? $user->user_email : '',
                'member_name' => $user ? $user->display_name : '',
                'source' => 'woocommerce',
                'site' => home_url('/'),
                'yome_inventory_context' => $inventory_context,
            ]),
        ]);

        if (is_wp_error($response)) {
            wp_send_json_error(['message' => 'YOME Assistant is unavailable right now.'], 502);
        }

        $code = (int) wp_remote_retrieve_response_code($response);
        $body = json_decode(wp_remote_retrieve_body($response), true);
        if ($code < 200 || $code >= 300 || !is_array($body)) {
            wp_send_json_error(['message' => 'YOME Assistant returned an invalid response.'], 502);
        }

        wp_send_json_success([
            'reply' => wp_kses_post($body['reply'] ?? $body['message'] ?? 'No pude responder ahora.'),
        ]);
    }

    private static function yome_inventory_context(string $message, array $options): array {
        if (($options['inventory_enabled'] ?? 'no') !== 'yes' || empty($options['inventory_api_url'])) {
            return ['enabled' => false, 'queried' => false, 'items' => []];
        }

        $message_norm = self::normalize_text($message);
        $inventory_intent = self::inventory_question_intent($message_norm);
        $search = self::inventory_search_terms($message_norm);

        if (!$inventory_intent && $search === '') {
            return ['enabled' => true, 'queried' => false, 'items' => []];
        }

        $url = add_query_arg([
            'q' => $search !== '' ? $search : $message,
            'limit' => 12,
        ], $options['inventory_api_url']);

        $headers = ['Accept' => 'application/json'];
        if (!empty($options['inventory_api_key'])) {
            $headers['X-YOME-Inventory-Key'] = $options['inventory_api_key'];
            $headers['Authorization'] = 'Bearer ' . $options['inventory_api_key'];
        }

        $response = wp_remote_get($url, [
            'timeout' => 12,
            'headers' => $headers,
        ]);

        if (is_wp_error($response)) {
            return [
                'enabled' => true,
                'queried' => true,
                'error' => $response->get_error_message(),
                'items' => [],
            ];
        }

        $code = (int) wp_remote_retrieve_response_code($response);
        $body = json_decode(wp_remote_retrieve_body($response), true);
        if ($code < 200 || $code >= 300 || !is_array($body)) {
            return [
                'enabled' => true,
                'queried' => true,
                'error' => 'invalid_inventory_response',
                'items' => [],
            ];
        }

        return [
            'enabled' => true,
            'queried' => true,
            'query' => $message,
            'search' => $search,
            'items' => self::extract_inventory_items($body),
        ];
    }

    private static function extract_inventory_items(array $body): array {
        $items = $body;
        foreach (['items', 'products', 'data', 'rows', 'inventory'] as $key) {
            if (isset($body[$key]) && is_array($body[$key])) {
                $items = $body[$key];
                break;
            }
        }

        if (!self::is_list_array($items)) {
            return [];
        }

        $clean = [];
        foreach (array_slice($items, 0, 12) as $item) {
            if (!is_array($item)) {
                continue;
            }
            $clean[] = [
                'name' => self::first_value($item, ['name', 'product_name', 'nombre', 'title', 'producto']),
                'code' => self::first_value($item, ['code', 'sku', 'codigo', 'código', 'barcode']),
                'category' => self::first_value($item, ['category', 'categoria', 'categoría']),
                'stock' => self::first_value($item, ['stock', 'qty', 'quantity', 'cantidad', 'existencia', 'available']),
                'store' => self::first_value($item, ['store', 'branch', 'location', 'warehouse', 'tienda', 'almacen', 'almacén', '门店']),
                'price' => self::first_value($item, ['price', 'regular_price', 'precio', 'precio_regular']),
                'member_price' => self::first_value($item, ['member_price', 'price_member', 'precio_miembro', 'miembro', '会员价']),
                'image' => self::first_value($item, ['image', 'image_url', 'photo', 'foto', 'thumbnail']),
                'url' => self::first_value($item, ['url', 'link', 'permalink']),
                'updated_at' => self::first_value($item, ['updated_at', 'created_at', 'date', 'fecha']),
            ];
        }

        return $clean;
    }

    private static function first_value(array $item, array $keys): string {
        foreach ($keys as $key) {
            if (isset($item[$key]) && $item[$key] !== '') {
                return is_scalar($item[$key]) ? (string) $item[$key] : '';
            }
        }
        return '';
    }

    private static function is_list_array(array $items): bool {
        $index = 0;
        foreach ($items as $key => $_value) {
            if ($key !== $index) {
                return false;
            }
            $index++;
        }
        return true;
    }

    private static function inventory_question_intent(string $message_norm): bool {
        $words = [
            'mercancia', 'mercancias', 'producto', 'productos', 'catalogo', 'inventario',
            'stock', 'existencia', 'disponible', 'nuevo', 'nueva', 'nuevos', 'nuevas',
            'novedades', 'hay', 'tienen', 'venden', '货', '库存', '产品', '商品', '新品', '新货'
        ];
        foreach ($words as $word) {
            if (strpos($message_norm, $word) !== false) {
                return true;
            }
        }
        return false;
    }

    private static function inventory_search_terms(string $message_norm): string {
        $stop_words = [
            'que', 'hay', 'tiene', 'tienen', 'precio', 'precios', 'quiero', 'buscar',
            'busco', 'dame', 'ver', 'mercancia', 'mercancias', 'producto', 'productos',
            'catalogo', 'inventario', 'stock', 'nuevo', 'nueva', 'nuevos', 'nuevas',
            'disponible', 'disponibles', 'por', 'favor', 'yome', 'miembro', '会员价'
        ];
        $tokens = preg_split('/\s+/', $message_norm);
        $kept = [];
        foreach ($tokens as $token) {
            $token = trim((string) $token);
            if ($token === '' || in_array($token, $stop_words, true) || strlen($token) < 3) {
                continue;
            }
            $kept[] = $token;
        }
        return implode(' ', array_slice($kept, 0, 4));
    }

    private static function normalize_text(string $text): string {
        $text = strtolower(remove_accents(wp_strip_all_tags($text)));
        $text = preg_replace('/[^a-z0-9\s\x{4e00}-\x{9fff}]/u', ' ', $text);
        $text = preg_replace('/\s+/', ' ', $text);
        return trim((string) $text);
    }

    private static function css(): string {
        return <<<'CSS'
.yome-assistant{--yome-ink:#172033;--yome-blue:#1477d4;--yome-gold:#f2b544;--yome-line:#d9e1ec;font-family:Arial,sans-serif;color:var(--yome-ink)}
.yome-assistant-floating{position:fixed;right:18px;bottom:18px;z-index:99999}
.yome-assistant-inline{max-width:390px;margin:18px 0}
.yome-member-assistant-page{max-width:760px}
.yome-assistant-launcher{display:flex;align-items:center;gap:10px;border:0;background:#fff;color:var(--yome-ink);padding:9px 13px;border-radius:999px;box-shadow:0 10px 30px rgba(23,32,51,.18);cursor:pointer}
.yome-launcher-text{font-weight:700;font-size:14px;white-space:nowrap}
.yome-robot{width:54px;height:58px;display:inline-block;position:relative;flex:0 0 54px;animation:yome-bob 2.8s ease-in-out infinite;filter:drop-shadow(0 8px 12px rgba(23,32,51,.18))}
.yome-robot.small{width:42px;height:45px;flex-basis:42px}
.yome-robot-antenna{position:absolute;left:50%;top:0;width:2px;height:10px;background:var(--yome-blue);transform:translateX(-50%);border-radius:2px}
.yome-robot-antenna:after{content:"";position:absolute;left:50%;top:-5px;width:8px;height:8px;background:var(--yome-gold);border-radius:50%;transform:translateX(-50%);animation:yome-pulse 1.7s ease-in-out infinite}
.yome-robot-head{position:absolute;left:7px;right:7px;top:9px;height:30px;background:linear-gradient(145deg,#fff,#e8f4ff);border:2px solid var(--yome-blue);border-radius:12px}
.yome-eye{position:absolute;top:10px;width:7px;height:7px;background:var(--yome-ink);border-radius:50%;animation:yome-blink 4s infinite}.yome-eye.left{left:11px}.yome-eye.right{right:11px}
.yome-robot-mouth{position:absolute;left:50%;bottom:7px;width:14px;height:3px;background:var(--yome-gold);border-radius:4px;transform:translateX(-50%)}
.yome-robot-body{position:absolute;left:12px;right:12px;top:38px;height:17px;background:linear-gradient(145deg,var(--yome-gold),#ffe48d);border-radius:8px 8px 12px 12px;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:11px;color:var(--yome-blue)}
.yome-robot-arm{position:absolute;top:41px;width:8px;height:3px;background:var(--yome-blue);border-radius:4px}.yome-robot-arm.left{left:5px;transform:rotate(25deg);animation:yome-wave-left 2.2s ease-in-out infinite}.yome-robot-arm.right{right:5px;transform:rotate(-25deg);animation:yome-wave-right 2.2s ease-in-out infinite}
.yome-robot.small .yome-robot-antenna{height:8px}.yome-robot.small .yome-robot-head{left:6px;right:6px;top:7px;height:24px;border-radius:10px}.yome-robot.small .yome-eye{top:8px;width:5px;height:5px}.yome-robot.small .yome-eye.left{left:8px}.yome-robot.small .yome-eye.right{right:8px}.yome-robot.small .yome-robot-mouth{bottom:5px;width:11px}.yome-robot.small .yome-robot-body{left:10px;right:10px;top:30px;height:13px;font-size:9px}.yome-robot.small .yome-robot-arm{top:33px}
.yome-chat-panel{display:none;width:min(380px,calc(100vw - 28px));height:520px;max-height:calc(100vh - 110px);background:#fff;border:1px solid var(--yome-line);border-radius:8px;box-shadow:0 18px 50px rgba(23,32,51,.22);overflow:hidden}
.yome-assistant-inline .yome-chat-panel{width:100%;height:560px}
.yome-assistant.open .yome-chat-panel,.yome-assistant-inline .yome-chat-panel{display:flex;flex-direction:column}
.yome-assistant.open .yome-assistant-launcher,.yome-assistant-inline .yome-assistant-launcher{display:none}
.yome-chat-head{display:flex;align-items:center;gap:10px;padding:12px 14px;background:#f7fafc;border-bottom:1px solid var(--yome-line)}
.yome-chat-head strong{display:block;font-size:15px}.yome-chat-head span{display:block;font-size:12px;color:#5d6b7c;margin-top:2px}
.yome-chat-close{margin-left:auto;border:0;background:transparent;font-size:24px;line-height:1;color:#5d6b7c;cursor:pointer}
.yome-chat-messages{flex:1;overflow:auto;padding:14px;background:#f3f6fa}
.yome-msg{max-width:88%;padding:10px 12px;border-radius:8px;margin:0 0 10px;font-size:14px;line-height:1.45;white-space:pre-wrap}
.yome-msg-bot{background:#fff;border:1px solid var(--yome-line)}
.yome-msg-user{background:var(--yome-blue);color:#fff;margin-left:auto}
.yome-chat-form{display:flex;gap:8px;padding:10px;border-top:1px solid var(--yome-line);background:#fff}
.yome-chat-form input{flex:1;min-width:0;border:1px solid var(--yome-line);border-radius:8px;padding:10px 11px;font-size:14px}
.yome-chat-form button{border:0;border-radius:8px;background:var(--yome-blue);color:#fff;font-weight:700;padding:0 14px;cursor:pointer}
@keyframes yome-bob{0%,100%{transform:translateY(0) rotate(-1deg)}50%{transform:translateY(-6px) rotate(1deg)}}
@keyframes yome-pulse{0%,100%{box-shadow:0 0 0 rgba(242,181,68,0);transform:translateX(-50%) scale(1)}50%{box-shadow:0 0 14px rgba(242,181,68,.8);transform:translateX(-50%) scale(1.2)}}
@keyframes yome-blink{0%,92%,100%{transform:scaleY(1)}95%{transform:scaleY(.12)}}
@keyframes yome-wave-left{0%,100%{transform:rotate(25deg)}50%{transform:rotate(2deg)}}
@keyframes yome-wave-right{0%,100%{transform:rotate(-25deg)}50%{transform:rotate(-2deg)}}
@media (prefers-reduced-motion:reduce){.yome-robot,.yome-robot-antenna:after,.yome-eye,.yome-robot-arm{animation:none}}
@media (max-width:480px){.yome-assistant-floating{right:10px;bottom:10px}.yome-chat-panel{width:calc(100vw - 20px);height:540px}.yome-launcher-text{display:none}}
CSS;
    }

    private static function js(): string {
        return <<<'JS'
(function(){
  function ready(fn){ if(document.readyState !== 'loading') fn(); else document.addEventListener('DOMContentLoaded', fn); }
  ready(function(){
    document.querySelectorAll('[data-yome-assistant]').forEach(function(root){
      var launcher = root.querySelector('.yome-assistant-launcher');
      var close = root.querySelector('.yome-chat-close');
      var form = root.querySelector('.yome-chat-form');
      var input = form ? form.querySelector('input[name="message"]') : null;
      var messages = root.querySelector('.yome-chat-messages');

      function addMessage(text, type){
        var div = document.createElement('div');
        div.className = 'yome-msg ' + (type === 'user' ? 'yome-msg-user' : 'yome-msg-bot');
        div.textContent = text;
        messages.appendChild(div);
        messages.scrollTop = messages.scrollHeight;
      }

      if(launcher){ launcher.addEventListener('click', function(){ root.classList.add('open'); if(input) input.focus(); }); }
      if(close){ close.addEventListener('click', function(){ root.classList.remove('open'); }); }

      if(form){
        form.addEventListener('submit', function(event){
          event.preventDefault();
          var text = input.value.trim();
          if(!text) return;
          input.value = '';
          addMessage(text, 'user');
          addMessage('YOME está escribiendo...', 'bot');
          var waiting = messages.lastElementChild;
          var body = new URLSearchParams();
          body.set('action', 'yome_assistant_chat');
          body.set('nonce', window.YOMEAssistantConfig.nonce);
          body.set('message', text);
          fetch(window.YOMEAssistantConfig.ajaxUrl, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'},
            body: body.toString()
          }).then(function(res){ return res.json(); }).then(function(data){
            waiting.textContent = data && data.success && data.data && data.data.reply
              ? data.data.reply
              : 'Ahora mismo no pude responder. Intenta otra vez.';
          }).catch(function(){
            waiting.textContent = 'YOME Assistant no está disponible ahora mismo.';
          });
        });
      }
    });
  });
})();
JS;
    }
}

register_activation_hook(__FILE__, ['YOME_WooCommerce_Assistant', 'activate']);
register_deactivation_hook(__FILE__, ['YOME_WooCommerce_Assistant', 'deactivate']);
YOME_WooCommerce_Assistant::init();
