<?php
/**
 * Plugin Name: YOME WooCommerce Assistant
 * Description: Shows a cartoon YOME assistant for logged-in WooCommerce members and proxies questions to the YOME AI service.
 * Version: 1.0.11
 * Author: YOME
 * Text Domain: yome-woocommerce-assistant
 */

if (!defined('ABSPATH')) {
    exit;
}

final class YOME_WooCommerce_Assistant {
    private const OPTION_KEY = 'yome_woocommerce_assistant_options';
    private const NONCE_ACTION = 'yome_assistant_chat';
    private const DEFAULT_INVENTORY_API_URL = 'https://yome-inventory-deploy-production.up.railway.app/api/products';
    private const DEFAULT_INVENTORY_LOGIN_URL = 'https://yome-inventory-deploy-production.up.railway.app/api/login';
    private static $assets_loaded = false;
    private static $dashboard_rendered = false;

    public static function init(): void {
        add_action('init', [__CLASS__, 'register_account_endpoint']);
        add_action('admin_menu', [__CLASS__, 'admin_menu']);
        add_action('rest_api_init', [__CLASS__, 'register_inventory_api']);
        add_action('wp_enqueue_scripts', [__CLASS__, 'enqueue_widget']);
        add_action('wp_footer', [__CLASS__, 'render_widget']);
        add_action('wp_ajax_yome_assistant_chat', [__CLASS__, 'ajax_chat']);
        add_filter('woocommerce_account_menu_items', [__CLASS__, 'account_menu_items']);
        add_action('woocommerce_account_dashboard', [__CLASS__, 'account_dashboard_assistant'], 3);
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

    public static function account_dashboard_assistant(): void {
        $options = self::options();
        if ($options['enabled'] !== 'yes' || !self::user_can_chat()) {
            return;
        }
        if (!in_array($options['display_scope'], ['account', 'site'], true)) {
            return;
        }

        self::$dashboard_rendered = true;
        self::enqueue_widget(true);
        self::widget_markup('dashboard');
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
            'inventory_enabled' => 'yes',
            'inventory_api_url' => self::DEFAULT_INVENTORY_API_URL,
            'inventory_login_url' => self::DEFAULT_INVENTORY_LOGIN_URL,
            'inventory_api_key' => '',
            'inventory_username' => '',
            'inventory_password' => '',
            'inventory_table' => '',
            'display_scope' => 'account',
            'enabled' => 'yes',
        ];
    }

    private static function options(): array {
        $saved = get_option(self::OPTION_KEY, []);
        $saved = is_array($saved) ? $saved : [];
        $options = wp_parse_args($saved, self::defaults());

        if (empty($options['inventory_api_url']) || self::is_builtin_inventory_api_url((string) $options['inventory_api_url'])) {
            $options['inventory_api_url'] = self::DEFAULT_INVENTORY_API_URL;
        }
        if (empty($options['inventory_login_url'])) {
            $options['inventory_login_url'] = self::DEFAULT_INVENTORY_LOGIN_URL;
        }
        if (($saved['inventory_api_url'] ?? '') === '' && ($saved['inventory_enabled'] ?? '') === 'no') {
            $options['inventory_enabled'] = 'yes';
        }

        return $options;
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
            $inventory_login_url = esc_url_raw(wp_unslash($_POST['inventory_login_url'] ?? ''));
            $inventory_api_key = sanitize_text_field(wp_unslash($_POST['inventory_api_key'] ?? ''));
            $inventory_username = sanitize_text_field(wp_unslash($_POST['inventory_username'] ?? ''));
            $inventory_password = sanitize_text_field(wp_unslash($_POST['inventory_password'] ?? ''));
            $inventory_table = sanitize_text_field(wp_unslash($_POST['inventory_table'] ?? ''));
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
                'inventory_login_url' => $inventory_login_url,
                'inventory_api_key' => $inventory_api_key,
                'inventory_username' => $inventory_username,
                'inventory_password' => $inventory_password,
                'inventory_table' => $inventory_table,
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
                            <p class="description">Use the real YOME warehouse app: <code><?php echo esc_html(self::DEFAULT_INVENTORY_API_URL); ?></code>. The member assistant does not use the old AI products CSV for inventory replies.</p>
                        </td>
                    </tr>
                    <tr>
                        <th scope="row"><label for="inventory_login_url">Inventory login URL</label></th>
                        <td>
                            <input name="inventory_login_url" id="inventory_login_url" type="url" class="regular-text"
                                   value="<?php echo esc_attr($options['inventory_login_url']); ?>" />
                            <p class="description">Use <code><?php echo esc_html(self::DEFAULT_INVENTORY_LOGIN_URL); ?></code>. If blank, it is auto-derived from <code>/api/products</code>.</p>
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
                        <th scope="row"><label for="inventory_table">YOME inventory table</label></th>
                        <td>
                            <input name="inventory_table" id="inventory_table" type="text" class="regular-text"
                                   value="<?php echo esc_attr($options['inventory_table']); ?>" autocomplete="off" />
                            <p class="description">Optional. Use only the real YOME warehouse table. Leave blank for auto-detect. WooCommerce/member products are not used as fallback.</p>
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

        if (self::can_view_inventory_sales()) {
            return true;
        }

        if (function_exists('wc_memberships_is_user_active_member')) {
            return (bool) wc_memberships_is_user_active_member(get_current_user_id());
        }

        return true;
    }

    private static function can_view_inventory_sales(): bool {
        return current_user_can('manage_woocommerce') || current_user_can('manage_options');
    }

    private static function settings_inventory_preview(array $options): void {
        if (($options['inventory_enabled'] ?? 'no') !== 'yes') {
            echo '<h2>Inventory preview</h2>';
            echo '<p>Turn on YOME · INVENTARIO and save to preview the real YOME warehouse inventory here.</p>';
            return;
        }

        $context = self::yome_inventory_context('que mercancia hay', $options);
        $items = self::yome_inventory_context_items($context);

        echo '<h2>Inventory preview</h2>';
        echo '<p><strong>Source:</strong> ' . esc_html((string) ($context['source'] ?? 'configured inventory')) . '</p>';
        echo '<p><strong>Items:</strong> ' . esc_html((string) count($items)) . '</p>';
        echo '<p><strong>Mode:</strong> YOME warehouse only. WooCommerce/member product fallback is disabled.</p>';
        if (!empty($context['error'])) {
            echo '<p><strong>Error:</strong> ' . esc_html((string) $context['error']) . '</p>';
        }

        if (!$items) {
            echo '<p>No real YOME warehouse rows found yet. The chat will not use WooCommerce/member products or old CSV stock.</p>';
            self::settings_inventory_candidates_preview($options);
            return;
        }

        echo '<table class="widefat striped" style="max-width:1000px">';
        echo '<thead><tr><th>Name</th><th>Code</th><th>Stock</th><th>Sales</th><th>Price</th><th>Member price</th><th>Store</th></tr></thead><tbody>';
        foreach (array_slice($items, 0, 8) as $item) {
            echo '<tr>';
            echo '<td>' . esc_html((string) ($item['name'] ?? '')) . '</td>';
            echo '<td>' . esc_html((string) ($item['code'] ?? '')) . '</td>';
            echo '<td>' . esc_html((string) ($item['stock'] ?? '')) . '</td>';
            echo '<td>' . esc_html((string) ($item['sales'] ?? '')) . '</td>';
            echo '<td>' . esc_html((string) ($item['price'] ?? '')) . '</td>';
            echo '<td>' . esc_html((string) ($item['member_price'] ?? '')) . '</td>';
            echo '<td>' . esc_html((string) ($item['store'] ?? '')) . '</td>';
            echo '</tr>';
        }
        echo '</tbody></table>';
        self::settings_inventory_candidates_preview($options);
    }

    private static function settings_inventory_candidates_preview(array $options): void {
        $rows = self::inventory_candidate_table_debug((string) ($options['inventory_table'] ?? ''));
        echo '<h3>YOME warehouse table candidates</h3>';
        if (!$rows) {
            echo '<p>No likely YOME warehouse tables detected yet.</p>';
            return;
        }

        echo '<table class="widefat striped" style="max-width:1000px">';
        echo '<thead><tr><th>Table</th><th>Score</th><th>Name</th><th>Code</th><th>Stock</th><th>Price</th><th>Sales</th><th>Columns</th></tr></thead><tbody>';
        foreach (array_slice($rows, 0, 12) as $row) {
            $map = is_array($row['map'] ?? null) ? $row['map'] : [];
            echo '<tr>';
            echo '<td><code>' . esc_html((string) ($row['table'] ?? '')) . '</code></td>';
            echo '<td>' . esc_html((string) ($row['score'] ?? '')) . '</td>';
            echo '<td>' . esc_html((string) ($map['name'] ?? '')) . '</td>';
            echo '<td>' . esc_html((string) ($map['code'] ?? '')) . '</td>';
            echo '<td>' . esc_html((string) ($map['stock'] ?? '')) . '</td>';
            echo '<td>' . esc_html((string) ($map['price'] ?? '')) . '</td>';
            echo '<td>' . esc_html((string) ($map['sales'] ?? '')) . '</td>';
            echo '<td>' . esc_html(implode(', ', array_slice((array) ($row['columns'] ?? []), 0, 10))) . '</td>';
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

        wp_register_style('yome-assistant-widget', false, [], '1.0.11');
        wp_enqueue_style('yome-assistant-widget');
        wp_add_inline_style('yome-assistant-widget', self::css());

        wp_register_script('yome-assistant-widget', false, [], '1.0.11', true);
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

        if (self::$dashboard_rendered) {
            return;
        }

        if (function_exists('is_wc_endpoint_url') && is_wc_endpoint_url('yome-assistant')) {
            return;
        }

        self::widget_markup('floating');
    }

    private static function widget_markup(string $mode): void {
        if ($mode === 'inline') {
            $class = 'yome-assistant yome-assistant-inline';
        } elseif ($mode === 'dashboard') {
            $class = 'yome-assistant yome-assistant-dashboard';
        } else {
            $class = 'yome-assistant yome-assistant-floating';
        }
        $launcher_label = $mode === 'dashboard' ? 'AI YOME' : 'YOME助手';
        ?>
        <div class="<?php echo esc_attr($class); ?>" data-yome-assistant>
            <button class="yome-assistant-launcher" type="button" aria-label="Open YOME Assistant">
                <?php self::robot_markup(); ?>
                <span class="yome-launcher-copy">
                    <span class="yome-launcher-text"><?php echo esc_html($launcher_label); ?></span>
                </span>
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
        $classes = trim('yome-bunny-mascot ' . $class);
        ?>
        <span class="<?php echo esc_attr($classes); ?>" aria-hidden="true">
            <img src="<?php echo esc_url(plugins_url('assets/yome-bunny-assistant.png', __FILE__)); ?>" alt="" loading="lazy" decoding="async" />
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
        $can_view_inventory_sales = self::can_view_inventory_sales();

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
                'can_view_inventory_sales' => $can_view_inventory_sales,
                'user_role' => $can_view_inventory_sales ? 'admin' : 'member',
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

        $options = self::options();
        $can_view_inventory_sales = self::can_view_inventory_sales();

        return rest_ensure_response([
            'items' => self::local_inventory_items($q, $limit, $can_view_inventory_sales, (string) ($options['inventory_table'] ?? '')),
            'source' => 'yome_warehouse_database',
            'can_view_inventory_sales' => $can_view_inventory_sales,
            'live' => true,
            'fetched_at' => gmdate('c'),
        ]);
    }

    public static function rest_inventory_debug() {
        $options = self::options();
        $tables = self::inventory_candidate_table_debug((string) ($options['inventory_table'] ?? ''));

        return rest_ensure_response([
            'api' => rest_url('yome-assistant/v1/inventory'),
            'debug' => rest_url('yome-assistant/v1/inventory-debug'),
            'mode' => 'yome_warehouse_only',
            'woocommerce_fallback' => false,
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
        $can_view_inventory_sales = self::can_view_inventory_sales();

        if (self::service_question_intent($message_norm)) {
            return ['enabled' => true, 'queried' => false, 'items' => []];
        }

        if (!$inventory_intent && $search === '') {
            return ['enabled' => true, 'queried' => false, 'items' => []];
        }

        $inventory_api_url = trim((string) ($options['inventory_api_url'] ?? ''));
        if ($inventory_api_url === '' || self::is_builtin_inventory_api_url($inventory_api_url)) {
            return self::local_inventory_context($message, $search, $can_view_inventory_sales, $options);
        }

        $request_limit = self::inventory_request_limit($message_norm);
        $url = add_query_arg([
            'q' => $search !== '' ? $search : $message,
            'limit' => $request_limit,
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
        $login_token = self::inventory_login_token($inventory_api_url, $options);
        if ($login_token !== '') {
            $headers['Authorization'] = 'Bearer ' . $login_token;
        } elseif (!empty($options['inventory_username']) || !empty($options['inventory_password'])) {
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
            'source' => 'external_yome_inventory_api',
            'can_view_inventory_sales' => $can_view_inventory_sales,
            'live' => true,
            'fetched_at' => gmdate('c'),
            'items' => self::extract_inventory_items($body, $can_view_inventory_sales, 12),
        ];
    }

    private static function local_inventory_context(string $message, string $search, bool $can_view_inventory_sales, array $options): array {
        return [
            'enabled' => true,
            'queried' => true,
            'query' => $message,
            'search' => $search,
            'source' => 'yome_warehouse_database',
            'can_view_inventory_sales' => $can_view_inventory_sales,
            'live' => true,
            'fetched_at' => gmdate('c'),
            'items' => self::local_inventory_items($search, 12, $can_view_inventory_sales, (string) ($options['inventory_table'] ?? '')),
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

    private static function inventory_default_login_url(string $api_url): string {
        $parts = wp_parse_url($api_url);
        $path = isset($parts['path']) ? (string) $parts['path'] : '';
        if (substr($path, -13) !== '/api/products') {
            return '';
        }

        $scheme = isset($parts['scheme']) ? (string) $parts['scheme'] : 'https';
        $host = isset($parts['host']) ? (string) $parts['host'] : '';
        if ($host === '') {
            return '';
        }
        $port = isset($parts['port']) ? ':' . (string) $parts['port'] : '';
        return $scheme . '://' . $host . $port . '/api/login';
    }

    private static function inventory_token_from_body($body): string {
        if (!is_array($body)) {
            return '';
        }

        foreach (['token', 'access_token', 'auth_token', 'jwt'] as $key) {
            if (!empty($body[$key]) && is_scalar($body[$key])) {
                return (string) $body[$key];
            }
        }

        foreach (['data', 'user'] as $key) {
            if (!empty($body[$key]) && is_array($body[$key])) {
                $token = self::inventory_token_from_body($body[$key]);
                if ($token !== '') {
                    return $token;
                }
            }
        }

        return '';
    }

    private static function inventory_login_token(string $api_url, array $options): string {
        $username = trim((string) ($options['inventory_username'] ?? ''));
        $password = trim((string) ($options['inventory_password'] ?? ''));
        if ($username === '' || $password === '') {
            return '';
        }

        $login_url = trim((string) ($options['inventory_login_url'] ?? ''));
        if ($login_url === '') {
            $login_url = self::inventory_default_login_url($api_url);
        }
        if ($login_url === '') {
            return '';
        }

        $response = wp_remote_post($login_url, [
            'timeout' => 12,
            'headers' => [
                'Accept' => 'application/json',
                'Content-Type' => 'application/json',
            ],
            'body' => wp_json_encode([
                'username' => $username,
                'password' => $password,
            ]),
        ]);

        if (is_wp_error($response)) {
            return '';
        }

        $code = (int) wp_remote_retrieve_response_code($response);
        $body = json_decode(wp_remote_retrieve_body($response), true);
        if ($code < 200 || $code >= 300 || !is_array($body)) {
            return '';
        }

        return trim(self::inventory_token_from_body($body));
    }

    private static function local_inventory_items(string $search = '', int $limit = 12, bool $include_sales = false, string $preferred_table = ''): array {
        global $wpdb;

        if (empty($wpdb)) {
            return [];
        }

        $limit = max(1, min(50, absint($limit)));
        $items = [];
        foreach (self::inventory_candidate_tables($preferred_table) as $table) {
            $columns = self::table_columns($table);
            if (!$columns) {
                continue;
            }

            $map = self::inventory_column_map($columns);
            if (empty($map['name']) && empty($map['code'])) {
                continue;
            }
            if (empty($map['stock']) && empty($map['price']) && empty($map['member_price']) && empty($map['sales'])) {
                continue;
            }

            $rows = self::query_inventory_table($table, $columns, $map, $search, $limit - count($items), $include_sales);
            foreach ($rows as $row) {
                $items[] = $row;
                if (count($items) >= $limit) {
                    break 2;
                }
            }
        }

        return $items;
    }

    private static function inventory_candidate_tables(string $preferred_table = ''): array {
        return array_map(static function ($row) {
            return (string) $row['table'];
        }, self::inventory_candidate_table_debug($preferred_table));
    }

    private static function inventory_candidate_table_debug(string $preferred_table = ''): array {
        global $wpdb;

        $tables = $wpdb->get_col('SHOW TABLES');
        if (!is_array($tables)) {
            return [];
        }

        $preferred_table = trim($preferred_table);
        $preferred_names = [];
        if ($preferred_table !== '') {
            $preferred_names[] = $preferred_table;
            if (isset($wpdb->prefix) && strpos($preferred_table, (string) $wpdb->prefix) !== 0) {
                $preferred_names[] = (string) $wpdb->prefix . $preferred_table;
            }
        }

        $scored = [];
        foreach ($tables as $table) {
            $table = (string) $table;
            $is_preferred = in_array($table, $preferred_names, true);
            if (!$is_preferred && self::inventory_table_is_blocked($table)) {
                continue;
            }

            $columns = self::table_columns($table);
            if (!$columns) {
                continue;
            }

            $map = self::inventory_column_map($columns);
            $score = self::inventory_table_score($table) + self::inventory_column_score($map, $columns);
            if ($is_preferred) {
                $score += 1000;
            }

            $has_identity = !empty($map['name']) || !empty($map['code']);
            $has_inventory_data = !empty($map['stock']) || !empty($map['price']) || !empty($map['member_price']) || !empty($map['sales']);
            if ($is_preferred || ($score >= 40 && $has_identity && $has_inventory_data)) {
                $scored[] = [
                    'table' => $table,
                    'score' => $score,
                    'columns' => $columns,
                    'map' => $map,
                ];
            }
        }

        usort($scored, static function ($a, $b) {
            return $b['score'] <=> $a['score'];
        });

        return $scored;
    }

    private static function inventory_table_is_blocked(string $table): bool {
        $name = strtolower($table);
        $blocked = [
            'actionscheduler', 'comment', 'links', 'options', 'postmeta', 'posts',
            'term', 'usermeta', 'users', 'woocommerce', 'wc_', 'wc-', '_wc_',
            'yoast', 'rank_math', 'redirection', 'snippets', 'session', 'queue',
        ];
        foreach ($blocked as $word) {
            if (strpos($name, $word) !== false) {
                return true;
            }
        }
        return false;
    }

    private static function inventory_table_score(string $table): int {
        $name = strtolower($table);
        if (self::inventory_table_is_blocked($table)) {
            return 0;
        }

        $score = 0;
        foreach (['yome' => 50, 'invent' => 45, 'stock' => 35, 'warehouse' => 25, 'almacen' => 25, 'bodega' => 25, 'tienda' => 25, 'store' => 15, 'producto' => 15, 'product' => 15] as $word => $points) {
            if (strpos($name, $word) !== false) {
                $score += $points;
            }
        }

        return $score;
    }

    private static function inventory_column_score(array $map, array $columns): int {
        $score = 0;
        foreach ([
            'name' => 25,
            'code' => 20,
            'stock' => 45,
            'store' => 10,
            'price' => 15,
            'member_price' => 10,
            'sales' => 15,
            'updated_at' => 5,
        ] as $field => $points) {
            if (!empty($map[$field])) {
                $score += $points;
            }
        }

        foreach ($columns as $column) {
            $key = self::normalize_column_key((string) $column);
            if (strpos($key, 'inventario') !== false || strpos($key, 'existencia') !== false || strpos($key, 'cantidad') !== false) {
                $score += 8;
            }
            if (strpos($key, 'producto') !== false || strpos($key, 'product') !== false || strpos($key, 'codigo') !== false) {
                $score += 5;
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
            'name' => ['name', 'productname', 'nombre', 'nombreproducto', 'nombredelproducto', 'producto', 'product', 'item', 'itemname', 'articulo', 'artículo', 'descripcion', 'description', 'title', 'concepto', '名称', '商品', '产品', '产品名称'],
            'code' => ['code', 'sku', 'codigo', 'codigobarra', 'codigodebarra', 'barcode', 'barcodeid', 'productcode', 'idproducto', 'productid', 'referencia', 'ref', 'modelo', 'model', '编号', '编码', '条码', '货号'],
            'category' => ['category', 'categoria', 'tipo', 'class', '分类', '类别'],
            'stock' => ['stock', 'qty', 'quantity', 'cantidad', 'existencia', 'existencias', 'available', 'disponible', 'disponibles', 'inventario', 'onhand', 'saldo', 'balance', 'restante', 'quedan', 'actual', 'cantidadactual', 'stockactual', 'inventarioactual', 'piezas', 'pcs', '库存', '数量', '剩余'],
            'store' => ['store', 'branch', 'location', 'warehouse', 'tienda', 'almacen', 'almacén', 'sucursal', 'ubicacion', 'ubicación', 'bodega', 'deposito', 'depósito', '门店', '仓库', '位置'],
            'price' => ['price', 'regularprice', 'precio', 'precioregular', 'venta', 'priceretail', 'retail', 'costo', 'cost', '价格', '售价', '零售价'],
            'member_price' => ['memberprice', 'pricewholesale', 'preciomiembro', 'preciomayor', 'mayor', 'wholesale', 'membershipprice', 'miembro', '会员价', '批发价'],
            'sales' => ['sales', 'totalsales', 'sold', 'soldqty', 'quantitysold', 'vendido', 'vendidos', 'ventas', 'cantidadvendida', 'unidadesvendidas', 'salida', 'salidas', 'egreso', 'egresos', 'orders', 'ordercount', '销量', '销售', '销售量', '卖出', '已售'],
            'image' => ['image', 'imageurl', 'photo', 'foto', 'thumbnail', 'imagen', 'imageurls', '图片', '照片'],
            'url' => ['url', 'link', 'permalink'],
            'updated_at' => ['updatedat', 'createdat', 'createddate', 'date', 'fecha', 'modified', 'updated'],
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

    private static function query_inventory_table(string $table, array $columns, array $map, string $search, int $limit, bool $include_sales = false): array {
        global $wpdb;

        if ($limit < 1) {
            return [];
        }

        $select = [];
        $fields = ['name', 'code', 'category', 'stock', 'store', 'price', 'member_price', 'image', 'url', 'updated_at'];
        if ($include_sales) {
            $fields[] = 'sales';
        }

        foreach ($fields as $field) {
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
            foreach ($fields as $field) {
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
        return (string) preg_replace('/[^a-z0-9\x{4e00}-\x{9fff}]+/u', '', $text);
    }

    private static function inventory_latest_intent(string $message_norm): bool {
        $words = [
            'nuevo', 'nueva', 'nuevos', 'nuevas', 'novedad', 'novedades',
            'llegaron', 'reciente', 'recientes', 'ultimo', 'ultimos',
            'latest', 'new', '新货', '新品', '最新', '新到', '到货'
        ];
        foreach ($words as $word) {
            if (strpos($message_norm, $word) !== false) {
                return true;
            }
        }
        return false;
    }

    private static function inventory_request_limit(string $message_norm): int {
        return self::inventory_latest_intent($message_norm) ? 50 : 12;
    }

    private static function inventory_item_timestamp(array $item): int {
        $date = self::first_value($item, ['updated_at', 'created_at', 'created_date', 'date', 'fecha', 'modified']);
        if ($date === '') {
            return 0;
        }

        $timestamp = strtotime($date);
        return $timestamp ? (int) $timestamp : 0;
    }

    private static function sort_inventory_items_latest(array $items): array {
        $has_dates = false;
        foreach ($items as $item) {
            if (is_array($item) && self::inventory_item_timestamp($item) > 0) {
                $has_dates = true;
                break;
            }
        }
        if (!$has_dates) {
            return $items;
        }

        usort($items, static function ($a, $b) {
            $left = is_array($a) ? self::inventory_item_timestamp($a) : 0;
            $right = is_array($b) ? self::inventory_item_timestamp($b) : 0;
            return $right <=> $left;
        });
        return $items;
    }

    private static function extract_inventory_items(array $body, bool $include_sales = false, int $limit = 12): array {
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

        $items = self::sort_inventory_items_latest(array_filter($items, 'is_array'));
        $limit = max(1, min(50, absint($limit)));
        $clean = [];
        foreach (array_slice($items, 0, $limit) as $item) {
            $clean_item = [
                'name' => self::first_value($item, ['name', 'product_name', 'nombre', 'title', 'producto']),
                'code' => self::first_value($item, ['code', 'sku', 'codigo', 'código', 'barcode']),
                'category' => self::first_value($item, ['category', 'categoria', 'categoría']),
                'stock' => self::first_value($item, ['stock', 'stock_qty', 'qty', 'quantity', 'cantidad', 'existencia', 'available']),
                'store' => self::first_value($item, ['store', 'store_location', 'low_location_text', 'branch', 'location', 'warehouse', 'tienda', 'almacen', 'almacén', '门店']),
                'price' => self::first_value($item, ['price', 'retail_price', 'regular_price', 'precio', 'precio_regular']),
                'member_price' => self::first_value($item, ['member_price', 'wholesale_price', 'min_wholesale_price', 'price_member', 'precio_miembro', 'miembro', '会员价']),
                'image' => self::first_value($item, ['image', 'image_url', 'main_photo_url', 'photo', 'foto', 'thumbnail']),
                'url' => self::first_value($item, ['url', 'link', 'permalink']),
                'updated_at' => self::first_value($item, ['updated_at', 'created_at', 'created_date', 'date', 'fecha', 'modified']),
            ];
            if ($include_sales) {
                $clean_item['sales'] = self::first_value($item, [
                    'sales', 'total_sales', 'sold', 'sold_qty', 'quantity_sold',
                    'vendido', 'vendidos', 'ventas', 'cantidad_vendida', 'unidades_vendidas',
                    'salida', 'salidas', 'orders', 'order_count', '销量', '销售量'
                ]);
            }
            $clean[] = $clean_item;
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
            'novedades', 'hay', 'tienen', 'venden', 'ventas', 'vendido', 'vendidos',
            'sales', 'sold', '货', '库存', '产品', '商品', '新品', '新货', '销量', '销售'
        ];
        foreach ($words as $word) {
            if (strpos($message_norm, $word) !== false) {
                return true;
            }
        }
        return false;
    }

    private static function service_question_intent(string $message_norm): bool {
        $words = [
            'direccion', 'direcion', 'dirrecion', 'ubicacion', 'donde estan',
            'donde queda', 'tienda fisica', 'local', 'sucursal', 'address',
            'location', 'pago', 'cuenta', 'banco', 'transferencia', 'deposito',
            'metodo de pago', 'horario', 'abierto', 'hora', 'delivery', 'envio',
            'entrega', '地址', '位置', '付款', '银行', '营业', '配送',
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
            'disponible', 'disponibles', 'ventas', 'vendido', 'vendidos', 'sales',
            'sold', 'por', 'favor', 'yome', 'miembro', 'direccion', 'direcion',
            'dirrecion', 'ubicacion', 'tienda', 'local', 'sucursal', 'pago',
            'cuenta', 'banco', 'transferencia', 'deposito', 'horario', 'delivery',
            'envio', 'entrega', '会员价', '库存', '销量', '销售'
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
.yome-assistant-dashboard{display:inline-block;max-width:110px;margin:0 0 18px}
.yome-assistant-dashboard.open{display:block;max-width:420px}
.yome-member-assistant-page{max-width:760px}
.yome-assistant-launcher{display:flex;align-items:center;gap:10px;border:0;background:#fff;color:var(--yome-ink);padding:9px 13px;border-radius:999px;box-shadow:0 10px 30px rgba(23,32,51,.18);cursor:pointer}
.yome-assistant-dashboard .yome-assistant-launcher{width:92px;min-height:96px;flex-direction:column;justify-content:center;gap:3px;border:1px solid var(--yome-line);border-radius:8px;padding:7px 8px;box-shadow:0 8px 22px rgba(23,32,51,.12)}
.yome-launcher-copy{display:flex;min-width:0;flex-direction:column;align-items:center;text-align:center}
.yome-assistant-dashboard .yome-launcher-text{font-size:12px;line-height:1.1}
.yome-launcher-text{font-weight:700;font-size:14px;white-space:nowrap}
.yome-bunny-mascot{width:58px;height:66px;display:inline-flex;align-items:center;justify-content:center;flex:0 0 58px;animation:yome-bunny-float 2.8s ease-in-out infinite;filter:drop-shadow(0 9px 13px rgba(23,32,51,.18));transform-origin:50% 85%}
.yome-bunny-mascot img{display:block;width:100%;height:100%;object-fit:contain}
.yome-assistant-dashboard .yome-bunny-mascot{width:58px;height:66px;flex-basis:auto}
.yome-bunny-mascot.small{width:42px;height:50px;flex-basis:42px}
.yome-chat-panel{display:none;width:min(380px,calc(100vw - 28px));height:520px;max-height:calc(100vh - 110px);background:#fff;border:1px solid var(--yome-line);border-radius:8px;box-shadow:0 18px 50px rgba(23,32,51,.22);overflow:hidden}
.yome-assistant-inline .yome-chat-panel{width:100%;height:560px}
.yome-assistant-dashboard .yome-chat-panel{width:min(420px,100%);height:520px;max-height:70vh;margin-top:10px}
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
@keyframes yome-bunny-float{0%,100%{transform:translateY(0) rotate(-2deg)}50%{transform:translateY(-6px) rotate(2deg)}}
@media (prefers-reduced-motion:reduce){.yome-bunny-mascot{animation:none}}
@media (max-width:480px){.yome-assistant-floating{right:10px;bottom:10px}.yome-chat-panel{width:calc(100vw - 20px);height:540px}.yome-assistant-floating .yome-launcher-copy{display:none}}
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
