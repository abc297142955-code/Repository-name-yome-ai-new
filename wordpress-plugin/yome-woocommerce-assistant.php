<?php
/**
 * Plugin Name: YOME WooCommerce Assistant
 * Description: Shows a cartoon YOME assistant for logged-in WooCommerce members and proxies questions to the YOME AI service.
 * Version: 1.0.4
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
        add_action('rest_api_init', [__CLASS__, 'register_inventory_api']);
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
            'inventory_username' => '',
            'inventory_password' => '',
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
            $inventory_username = sanitize_text_field(wp_unslash($_POST['inventory_username'] ?? ''));
            $inventory_password = sanitize_text_field(wp_unslash($_POST['inventory_password'] ?? ''));
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
                'inventory_username' => $inventory_username,
                'inventory_password' => $inventory_password,
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
                            <p class="description">Leave blank to read live inventory from this WordPress/member system automatically. Do not paste the built-in API here; it is only for testing or external tools.</p>
                            <p class="description">Built-in API after activation: <code><?php echo esc_html(rest_url('yome-assistant/v1/inventory')); ?></code></p>
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
                        <th scope="row"><label for="inventory_username">Inventory username</label></th>
                        <td>
                            <input name="inventory_username" id="inventory_username" type="text" class="regular-text"
                                   value="<?php echo esc_attr($options['inventory_username']); ?>" autocomplete="off" />
                            <p class="description">Optional. Use this when YOME · INVENTARIO requires an account login.</p>
                        </td>
                    </tr>
                    <tr>
                        <th scope="row"><label for="inventory_password">Inventory password</label></th>
                        <td>
                            <input name="inventory_password" id="inventory_password" type="password" class="regular-text"
                                   value="<?php echo esc_attr($options['inventory_password']); ?>" autocomplete="new-password" />
                            <p class="description">Optional. Sent with Inventory username as Basic authorization.</p>
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
            <?php self::settings_inventory_preview($options); ?>
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

    private static function settings_inventory_preview(array $options): void {
        if (($options['inventory_enabled'] ?? 'no') !== 'yes') {
            echo '<h2>Inventory preview</h2>';
            echo '<p>Turn on YOME · INVENTARIO and save to preview live member inventory here.</p>';
            return;
        }

        $context = self::yome_inventory_context('que mercancia hay', $options);
        $items = self::yome_inventory_context_items($context);

        echo '<h2>Inventory preview</h2>';
        echo '<p><strong>Source:</strong> ' . esc_html((string) ($context['source'] ?? 'configured inventory')) . '</p>';
        echo '<p><strong>Items:</strong> ' . esc_html((string) count($items)) . '</p>';
        if (!empty($context['error'])) {
            echo '<p><strong>Error:</strong> ' . esc_html((string) $context['error']) . '</p>';
        }

        if (!$items) {
            echo '<p>No inventory rows found yet. The chat will not use old CSV stock.</p>';
            return;
        }

        echo '<table class="widefat striped" style="max-width:1000px">';
        echo '<thead><tr><th>Name</th><th>Code</th><th>Stock</th><th>Price</th><th>Member price</th><th>Store</th></tr></thead><tbody>';
        foreach (array_slice($items, 0, 8) as $item) {
            echo '<tr>';
            echo '<td>' . esc_html((string) ($item['name'] ?? '')) . '</td>';
            echo '<td>' . esc_html((string) ($item['code'] ?? '')) . '</td>';
            echo '<td>' . esc_html((string) ($item['stock'] ?? '')) . '</td>';
            echo '<td>' . esc_html((string) ($item['price'] ?? '')) . '</td>';
            echo '<td>' . esc_html((string) ($item['member_price'] ?? '')) . '</td>';
            echo '<td>' . esc_html((string) ($item['store'] ?? '')) . '</td>';
            echo '</tr>';
        }
        echo '</tbody></table>';
    }

    private static function yome_inventory_context_items(array $context): array {
        foreach (['items', 'products', 'data', 'rows'] as $key) {
            if (!empty($context[$key]) && is_array($context[$key])) {
                return array_filter($context[$key], 'is_array');
            }
        }
        return [];
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

    public static function register_inventory_api(): void {
        register_rest_route('yome-assistant/v1', '/inventory', [
            'methods' => 'GET',
            'callback' => [__CLASS__, 'rest_inventory'],
            'permission_callback' => [__CLASS__, 'rest_inventory_permission'],
            'args' => [
                'q' => [
                    'required' => false,
                    'sanitize_callback' => 'sanitize_text_field',
                ],
                'limit' => [
                    'required' => false,
                    'sanitize_callback' => 'absint',
                ],
            ],
        ]);

        register_rest_route('yome-assistant/v1', '/inventory-debug', [
            'methods' => 'GET',
            'callback' => [__CLASS__, 'rest_inventory_debug'],
            'permission_callback' => static function () {
                return current_user_can('manage_options');
            },
        ]);
    }

    public static function rest_inventory_permission($request): bool {
        if (current_user_can('manage_woocommerce') || current_user_can('manage_options')) {
            return true;
        }

        $options = self::options();
        $key = trim((string) ($options['inventory_api_key'] ?? ''));
        if ($key === '') {
            return false;
        }

        $sent_key = trim((string) $request->get_header('x-yome-inventory-key'));
        if (hash_equals($key, $sent_key)) {
            return true;
        }

        $auth = trim((string) $request->get_header('authorization'));
        if (stripos($auth, 'Bearer ') === 0 && hash_equals($key, trim(substr($auth, 7)))) {
            return true;
        }

        return false;
    }

    public static function rest_inventory($request) {
        $q = sanitize_text_field((string) $request->get_param('q'));
        $limit = absint($request->get_param('limit'));
        if ($limit < 1 || $limit > 50) {
            $limit = 12;
        }

        return rest_ensure_response([
            'items' => self::local_inventory_items($q, $limit),
            'source' => 'yome_assistant_local_inventory',
            'live' => true,
            'fetched_at' => gmdate('c'),
        ]);
    }

    public static function rest_inventory_debug() {
        $tables = [];
        foreach (self::inventory_candidate_tables() as $table) {
            $columns = self::table_columns($table);
            $tables[] = [
                'table' => $table,
                'columns' => $columns,
                'map' => self::inventory_column_map($columns),
            ];
        }

        return rest_ensure_response([
            'api' => rest_url('yome-assistant/v1/inventory'),
            'debug' => rest_url('yome-assistant/v1/inventory-debug'),
            'woocommerce_member_inventory' => [
                'available' => function_exists('wc_get_products'),
                'sample' => self::woocommerce_inventory_items('', 3),
            ],
            'tables' => $tables,
        ]);
    }

    private static function yome_inventory_context(string $message, array $options): array {
        if (($options['inventory_enabled'] ?? 'no') !== 'yes') {
            return ['enabled' => false, 'queried' => false, 'items' => []];
        }

        $message_norm = self::normalize_text($message);
        $inventory_intent = self::inventory_question_intent($message_norm);
        $search = self::inventory_search_terms($message_norm);

        if (!$inventory_intent && $search === '') {
            return ['enabled' => true, 'queried' => false, 'items' => []];
        }

        $inventory_api_url = trim((string) ($options['inventory_api_url'] ?? ''));
        if ($inventory_api_url === '' || self::is_builtin_inventory_api_url($inventory_api_url)) {
            return self::local_inventory_context($message, $search);
        }

        $url = add_query_arg([
            'q' => $search !== '' ? $search : $message,
            'limit' => 12,
            '_yome_live' => time(),
        ], $inventory_api_url);

        $headers = [
            'Accept' => 'application/json',
            'Cache-Control' => 'no-cache, no-store, must-revalidate',
            'Pragma' => 'no-cache',
            'Expires' => '0',
        ];
        if (!empty($options['inventory_api_key'])) {
            $headers['X-YOME-Inventory-Key'] = $options['inventory_api_key'];
            $headers['Authorization'] = 'Bearer ' . $options['inventory_api_key'];
        }
        if (!empty($options['inventory_username']) || !empty($options['inventory_password'])) {
            $headers['Authorization'] = 'Basic ' . base64_encode(
                ($options['inventory_username'] ?? '') . ':' . ($options['inventory_password'] ?? '')
            );
        }

        $response = wp_remote_get($url, [
            'timeout' => 12,
            'headers' => $headers,
            'redirection' => 2,
            'httpversion' => '1.1',
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
            'live' => true,
            'fetched_at' => gmdate('c'),
            'items' => self::extract_inventory_items($body),
        ];
    }

    private static function local_inventory_context(string $message, string $search): array {
        return [
            'enabled' => true,
            'queried' => true,
            'query' => $message,
            'search' => $search,
            'source' => 'local_wordpress_database',
            'live' => true,
            'fetched_at' => gmdate('c'),
            'items' => self::local_inventory_items($search, 12),
        ];
    }

    private static function is_builtin_inventory_api_url(string $url): bool {
        $path = wp_parse_url($url, PHP_URL_PATH);
        if (is_string($path) && strpos($path, '/wp-json/yome-assistant/v1/inventory') !== false) {
            return true;
        }

        $query = wp_parse_url($url, PHP_URL_QUERY);
        if (is_string($query)) {
            parse_str($query, $params);
            $rest_route = isset($params['rest_route']) ? (string) $params['rest_route'] : '';
            if (strpos($rest_route, '/yome-assistant/v1/inventory') === 0) {
                return true;
            }
        }

        return false;
    }

    private static function local_inventory_items(string $search = '', int $limit = 12): array {
        global $wpdb;

        if (empty($wpdb)) {
            return [];
        }

        $limit = max(1, min(50, absint($limit)));
        $items = [];
        foreach (self::inventory_candidate_tables() as $table) {
            $columns = self::table_columns($table);
            if (!$columns) {
                continue;
            }

            $map = self::inventory_column_map($columns);
            if (empty($map['name']) && empty($map['code'])) {
                continue;
            }
            if (empty($map['stock']) && empty($map['price']) && empty($map['member_price'])) {
                continue;
            }

            $rows = self::query_inventory_table($table, $columns, $map, $search, $limit - count($items));
            foreach ($rows as $row) {
                $items[] = $row;
                if (count($items) >= $limit) {
                    break 2;
                }
            }
        }

        if (count($items) < $limit) {
            $seen = [];
            foreach ($items as $item) {
                $key = strtolower(($item['code'] ?? '') . '|' . ($item['name'] ?? ''));
                $seen[$key] = true;
            }

            foreach (self::woocommerce_inventory_items($search, $limit - count($items)) as $item) {
                $key = strtolower(($item['code'] ?? '') . '|' . ($item['name'] ?? ''));
                if (isset($seen[$key])) {
                    continue;
                }
                $items[] = $item;
                $seen[$key] = true;
                if (count($items) >= $limit) {
                    break;
                }
            }
        }

        return $items;
    }

    private static function woocommerce_inventory_items(string $search = '', int $limit = 12): array {
        if (!function_exists('wc_get_product') || !function_exists('wc_get_products')) {
            return [];
        }

        $limit = max(1, min(50, absint($limit)));
        $products = self::woocommerce_products_for_inventory($search, $limit);
        $items = [];

        foreach ($products as $product) {
            if (!is_object($product) || !method_exists($product, 'get_id')) {
                continue;
            }

            $stock_qty = method_exists($product, 'get_stock_quantity') ? $product->get_stock_quantity() : null;
            $stock_status = method_exists($product, 'get_stock_status') ? (string) $product->get_stock_status() : '';
            $stock_text = $stock_qty !== null ? (string) $stock_qty : self::stock_status_text($stock_status);

            $image = '';
            if (method_exists($product, 'get_image_id')) {
                $image_id = (int) $product->get_image_id();
                if ($image_id > 0) {
                    $image = (string) wp_get_attachment_image_url($image_id, 'medium');
                }
            }

            $items[] = [
                'name' => method_exists($product, 'get_name') ? (string) $product->get_name() : '',
                'code' => method_exists($product, 'get_sku') ? (string) $product->get_sku() : '',
                'category' => self::woocommerce_product_categories((int) $product->get_id()),
                'stock' => $stock_text,
                'store' => 'YOME member system',
                'price' => self::woocommerce_product_price($product),
                'member_price' => self::woocommerce_member_price($product),
                'image' => $image,
                'url' => get_permalink((int) $product->get_id()),
                'updated_at' => self::woocommerce_product_modified($product),
                'source' => 'woocommerce_member_system',
            ];
        }

        return $items;
    }

    private static function woocommerce_products_for_inventory(string $search, int $limit): array {
        global $wpdb;

        $search = trim($search);
        if ($search !== '' && !empty($wpdb)) {
            $like = '%' . $wpdb->esc_like($search) . '%';
            $sql = "
                SELECT DISTINCT p.ID
                FROM {$wpdb->posts} p
                LEFT JOIN {$wpdb->postmeta} sku ON sku.post_id = p.ID AND sku.meta_key = '_sku'
                WHERE p.post_type = 'product'
                  AND p.post_status = 'publish'
                  AND (p.post_title LIKE %s OR sku.meta_value LIKE %s)
                ORDER BY p.post_modified DESC
                LIMIT %d
            ";
            $ids = $wpdb->get_col($wpdb->prepare($sql, $like, $like, $limit));
            if (is_array($ids) && $ids) {
                $products = [];
                foreach ($ids as $id) {
                    $product = wc_get_product((int) $id);
                    if ($product) {
                        $products[] = $product;
                    }
                }
                return $products;
            }
        }

        $args = [
            'status' => 'publish',
            'limit' => $limit,
            'orderby' => 'modified',
            'order' => 'DESC',
            'return' => 'objects',
        ];
        if ($search !== '') {
            $args['search'] = '*' . $search . '*';
        }

        $products = wc_get_products($args);
        return is_array($products) ? $products : [];
    }

    private static function stock_status_text(string $stock_status): string {
        if ($stock_status === 'instock') {
            return 'Disponible';
        }
        if ($stock_status === 'outofstock') {
            return 'Agotado';
        }
        if ($stock_status === 'onbackorder') {
            return 'Por encargo';
        }
        return $stock_status;
    }

    private static function woocommerce_product_price($product): string {
        foreach (['get_regular_price', 'get_price'] as $method) {
            if (method_exists($product, $method)) {
                $value = (string) $product->{$method}();
                if ($value !== '') {
                    return $value;
                }
            }
        }
        return '';
    }

    private static function woocommerce_member_price($product): string {
        if (!is_object($product) || !method_exists($product, 'get_id')) {
            return '';
        }

        $id = (int) $product->get_id();
        $keys = [
            '_member_price', 'member_price', '_membership_price', 'membership_price',
            '_price_wholesale', 'price_wholesale', '_wholesale_price', 'wholesale_price',
            '_precio_miembro', 'precio_miembro', '_precio_mayor', 'precio_mayor',
            '_mayor', 'mayor',
        ];
        foreach ($keys as $key) {
            $value = get_post_meta($id, $key, true);
            if (is_scalar($value) && (string) $value !== '') {
                return (string) $value;
            }
        }

        return '';
    }

    private static function woocommerce_product_categories(int $product_id): string {
        $terms = wp_get_post_terms($product_id, 'product_cat', ['fields' => 'names']);
        if (!is_array($terms) || is_wp_error($terms)) {
            return '';
        }
        return implode(', ', array_map('strval', $terms));
    }

    private static function woocommerce_product_modified($product): string {
        if (is_object($product) && method_exists($product, 'get_date_modified')) {
            $date = $product->get_date_modified();
            if (is_object($date) && method_exists($date, 'date')) {
                return (string) $date->date('c');
            }
        }
        return '';
    }

    private static function inventory_candidate_tables(): array {
        global $wpdb;

        $tables = $wpdb->get_col('SHOW TABLES');
        if (!is_array($tables)) {
            return [];
        }

        $scored = [];
        foreach ($tables as $table) {
            $score = self::inventory_table_score((string) $table);
            if ($score > 0) {
                $scored[] = ['table' => (string) $table, 'score' => $score];
            }
        }

        usort($scored, static function ($a, $b) {
            return $b['score'] <=> $a['score'];
        });

        return array_map(static function ($row) {
            return $row['table'];
        }, $scored);
    }

    private static function inventory_table_score(string $table): int {
        $name = strtolower($table);
        $blocked = [
            'actionscheduler', 'comment', 'links', 'options', 'postmeta', 'posts',
            'term', 'usermeta', 'users', 'woocommerce_sessions',
        ];
        foreach ($blocked as $word) {
            if (strpos($name, $word) !== false) {
                return 0;
            }
        }

        $score = 0;
        foreach (['yome' => 50, 'invent' => 45, 'stock' => 35, 'warehouse' => 25, 'almacen' => 25, 'tienda' => 25, 'store' => 15] as $word => $points) {
            if (strpos($name, $word) !== false) {
                $score += $points;
            }
        }

        return $score;
    }

    private static function table_columns(string $table): array {
        global $wpdb;

        $rows = $wpdb->get_results('DESCRIBE ' . self::sql_ident($table), ARRAY_A);
        if (!is_array($rows)) {
            return [];
        }

        $columns = [];
        foreach ($rows as $row) {
            if (!empty($row['Field'])) {
                $columns[] = (string) $row['Field'];
            }
        }
        return $columns;
    }

    private static function inventory_column_map(array $columns): array {
        $aliases = [
            'name' => ['name', 'productname', 'nombre', 'nombredelproducto', 'producto', 'title', 'itemname', 'descripcion', 'description'],
            'code' => ['code', 'sku', 'codigo', 'codigobarra', 'barcode', 'barcodeid', 'productcode', 'referencia', 'ref'],
            'category' => ['category', 'categoria', 'tipo', 'class'],
            'stock' => ['stock', 'qty', 'quantity', 'cantidad', 'existencia', 'available', 'disponible', 'inventario', 'onhand', 'saldo'],
            'store' => ['store', 'branch', 'location', 'warehouse', 'tienda', 'almacen', 'sucursal', 'ubicacion', 'bodega'],
            'price' => ['price', 'regularprice', 'precio', 'precioregular', 'venta', 'priceretail', 'retail'],
            'member_price' => ['memberprice', 'pricewholesale', 'preciomiembro', 'preciomayor', 'mayor', 'wholesale', 'membershipprice', 'miembro'],
            'image' => ['image', 'imageurl', 'photo', 'foto', 'thumbnail', 'imagen', 'imageurls'],
            'url' => ['url', 'link', 'permalink'],
            'updated_at' => ['updatedat', 'createdat', 'date', 'fecha', 'modified', 'updated'],
        ];

        $normalized = [];
        foreach ($columns as $column) {
            $normalized[$column] = self::normalize_column_key($column);
        }

        $map = [];
        foreach ($aliases as $field => $field_aliases) {
            $map[$field] = '';
            foreach ($normalized as $column => $key) {
                if (in_array($key, $field_aliases, true)) {
                    $map[$field] = $column;
                    break;
                }
            }
            if ($map[$field] !== '') {
                continue;
            }
            foreach ($normalized as $column => $key) {
                foreach ($field_aliases as $alias) {
                    if (strpos($key, $alias) !== false) {
                        $map[$field] = $column;
                        break 2;
                    }
                }
            }
        }

        return $map;
    }

    private static function query_inventory_table(string $table, array $columns, array $map, string $search, int $limit): array {
        global $wpdb;

        if ($limit < 1) {
            return [];
        }

        $select = [];
        foreach (['name', 'code', 'category', 'stock', 'store', 'price', 'member_price', 'image', 'url', 'updated_at'] as $field) {
            if (!empty($map[$field]) && in_array($map[$field], $columns, true)) {
                $select[] = self::sql_ident($map[$field]) . ' AS ' . self::sql_ident($field);
            }
        }
        if (!$select) {
            return [];
        }

        $where = '';
        $args = [];
        $search = trim($search);
        if ($search !== '') {
            $where_parts = [];
            foreach (array_unique(array_filter([$map['name'] ?? '', $map['code'] ?? '', $map['category'] ?? ''])) as $column) {
                if (in_array($column, $columns, true)) {
                    $where_parts[] = self::sql_ident($column) . ' LIKE %s';
                    $args[] = '%' . $wpdb->esc_like($search) . '%';
                }
            }
            if ($where_parts) {
                $where = ' WHERE ' . implode(' OR ', $where_parts);
            }
        }

        $order = '';
        if (!empty($map['updated_at']) && in_array($map['updated_at'], $columns, true)) {
            $order = ' ORDER BY ' . self::sql_ident($map['updated_at']) . ' DESC';
        }

        $sql = 'SELECT ' . implode(', ', $select) . ' FROM ' . self::sql_ident($table) . $where . $order . ' LIMIT %d';
        $args[] = $limit;
        $prepared = $wpdb->prepare($sql, $args);
        $rows = $wpdb->get_results($prepared, ARRAY_A);
        if (!is_array($rows)) {
            return [];
        }

        $items = [];
        foreach ($rows as $row) {
            $item = [];
            foreach (['name', 'code', 'category', 'stock', 'store', 'price', 'member_price', 'image', 'url', 'updated_at'] as $field) {
                $item[$field] = isset($row[$field]) && is_scalar($row[$field]) ? (string) $row[$field] : '';
            }
            if (implode('', $item) !== '') {
                $items[] = $item;
            }
        }
        return $items;
    }

    private static function sql_ident(string $identifier): string {
        return '`' . str_replace('`', '``', $identifier) . '`';
    }

    private static function normalize_column_key(string $text): string {
        $text = strtolower(remove_accents($text));
        return (string) preg_replace('/[^a-z0-9]+/', '', $text);
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
            cache: 'no-store',
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
