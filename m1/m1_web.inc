bool is_authenticated(const std::map<std::string, std::string>& headers) {
    auto it = headers.find("authorization");
    if (it == headers.end()) return false;
    const std::string prefix = "Basic ";
    if (it->second.rfind(prefix, 0) != 0) return false;
    std::string decoded = base64_decode(it->second.substr(prefix.size()));
    size_t colon = decoded.find(':');
    if (colon == std::string::npos) return false;
    std::string user = decoded.substr(0, colon);
    std::string pass = decoded.substr(colon + 1);
    return user == getenv_or("M1_ADMIN_USER", "admin") && pass == getenv_or("M1_ADMIN_PASSWORD", "");
}

std::string layout(const std::string& body, const std::string& flash = "") {
    std::ostringstream html;
    html << "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">";
    html << "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">";
    html << "<title>MEXC M1 · Griders</title>";
    html << "<style>";
    html << ":root{--bg:#f5f7f9;--panel:#fff;--text:#182026;--muted:#64717d;--line:#dce3e8;--accent:#0b8f70;--warn:#b6471d;--shadow:0 8px 28px rgba(28,43,54,.08)}";
    html << "*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 Inter,system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}";
    html << ".top{height:64px;border-bottom:1px solid var(--line);background:rgba(255,255,255,.92);display:flex;align-items:center;padding:12px max(20px,calc((100vw - 1180px)/2));gap:12px;position:sticky;top:0;z-index:2;backdrop-filter:blur(12px)}";
    html << ".mark{width:32px;height:32px;border-radius:7px;background:var(--accent);color:white;display:grid;place-items:center;font-weight:800}.brand{font-weight:800}.nav{display:flex;gap:14px;margin-left:18px}.nav a{color:var(--muted);font-weight:760;text-decoration:none}.nav a:hover{color:var(--accent)}.shell{max-width:1180px;margin:0 auto;padding:28px 20px 56px}";
    html << ".beta{padding:12px 16px;border:1px solid #ead59b;border-radius:8px;background:#fff2c8;color:#75520b;font-weight:760;text-align:center;margin-bottom:20px}";
    html << ".head{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;margin-bottom:20px}.eyebrow{color:var(--accent);font-size:12px;text-transform:uppercase;font-weight:800}.lead{color:var(--muted);font-size:18px;max-width:720px}h1{font-size:38px;line-height:1.05;margin:6px 0 12px}h2{margin:0 0 12px;font-size:21px}";
    html << ".grid{display:grid;grid-template-columns:340px 1fr;gap:18px;align-items:start}.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:18px;margin-bottom:18px;box-shadow:var(--shadow)}";
    html << "label{display:grid;gap:6px;font-weight:760;margin-bottom:14px}input,textarea{min-height:42px;border:1px solid var(--line);border-radius:7px;padding:9px 11px;font:inherit}textarea{min-height:260px;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px}.form{display:grid;grid-template-columns:1fr 1fr;gap:4px 16px}.wide{grid-column:1/-1}.button{border:1px solid var(--accent);background:var(--accent);color:white;border-radius:7px;padding:10px 14px;font-weight:760;cursor:pointer}.secondary{background:white;color:var(--accent)}";
    html << ".flash{padding:10px 12px;border-radius:7px;background:#eaf7f1;border:1px solid #a9d9c4;color:#06785e;margin-bottom:18px}.warn{background:#fff4ef;border-color:#f0c4b1;color:var(--warn)}";
    html << "table{width:100%;border-collapse:collapse;min-width:760px}th,td{text-align:left;padding:11px 9px;border-bottom:1px solid var(--line);white-space:nowrap}th{font-size:12px;color:var(--muted);text-transform:uppercase}.scroll{overflow-x:auto}.pill{display:inline-flex;min-height:24px;align-items:center;padding:3px 8px;border-radius:7px;background:#eef2f5;color:var(--muted);font-size:12px;font-weight:780}.ok{background:#eaf7f1;color:#06785e}.bad{background:#f8ece8;color:#a43e1c}.fee{color:#06785e;font-weight:800}.muted{color:var(--muted)}.small{font-size:13px;color:var(--muted)}pre{white-space:pre-wrap;word-break:break-word;background:#f7faf9;border:1px solid var(--line);border-radius:8px;padding:12px;font-size:13px}";
    html << "details{border:1px solid var(--line);border-radius:8px;background:#fbfcfd;margin-top:10px}summary{cursor:pointer;padding:10px 12px;font-weight:800}.detail-body{padding:0 12px 12px}.raw-small{max-height:170px;overflow:auto;font-size:12px}.nowrap{white-space:nowrap}";
    html << ".metric-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:18px}.metric{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px;box-shadow:var(--shadow)}.metric b{display:block;font-size:22px;line-height:1.15}.metric span{display:block;color:var(--muted);font-size:12px;text-transform:uppercase;font-weight:800;margin-bottom:4px}.raw{max-height:360px;overflow:auto}";
    html << "@media(max-width:860px){.grid,.form,.metric-grid{grid-template-columns:1fr}.head{display:block}h1{font-size:32px}.nav{gap:9px;margin-left:8px;flex-wrap:wrap}.top{height:auto}}";
    html << "</style></head><body><header class=\"top\"><span class=\"mark\">M1</span><span class=\"brand\">Griders · MEXC</span><nav class=\"nav\"><a href=\"/bots\">Список ботов</a><a href=\"/deals\">Сделки</a><a href=\"/account\">Аккаунт</a><a href=\"/connections\">Подключения</a></nav></header><main class=\"shell\">";
    html << "<div class=\"beta\">M1 работает отдельно от основного Griders. Торговая логика MEXC пока находится в разработке.</div>";
    if (!flash.empty()) html << flash;
    html << body << "</main></body></html>";
    return html.str();
}

std::string home_page(const std::string& flash = "") {
    MexcConnection conn = load_connection();
    std::vector<MarketRow> rows = personal_zero_fee_rows(conn);
    int zero_both = static_cast<int>(rows.size());

    std::ostringstream body;
    body << "<section class=\"head\"><div><p class=\"eyebrow\">MEXC M1</p><h1>Подключение к MEXC Futures</h1>";
    body << "<p class=\"lead\">Здесь готовим отдельную реализацию Griders под MEXC: API ключи, комиссии, проверка фьючерсного аккаунта и дальнейшее управление ордерами.</p></div>";
    body << "<a class=\"button secondary\" href=\"/health\">Health</a></section>";
    body << "<section class=\"grid\"><div class=\"panel\"><h2>Текущее подключение</h2>";
    body << "<p><strong>" << html_escape(conn.label.empty() ? "MEXC Futures" : conn.label) << "</strong></p>";
    body << "<p class=\"small\">API key: " << html_escape(mask_key(conn.api_key)) << "</p>";
    body << "<p class=\"small\">Права ключа: чтение + управление фьючерсами. Без этих прав торговый модуль позже не сможет выставлять ордера.</p>";
    body << "<form method=\"post\" action=\"/test\"><button class=\"button secondary\" type=\"submit\">Проверить API</button></form></div>";
    body << "<div class=\"panel\"><h2>Сохранить ключ MEXC</h2><form class=\"form\" method=\"post\" action=\"/connection\" autocomplete=\"off\">";
    body << "<label>Название<input name=\"label\" value=\"" << html_escape(conn.label) << "\" placeholder=\"MEXC Futures\"></label>";
    body << "<label>API Key<input name=\"api_key\" value=\"" << html_escape(conn.api_key) << "\" autocomplete=\"off\" spellcheck=\"false\"></label>";
    body << "<label class=\"wide\">API Secret<input name=\"api_secret\" type=\"text\" autocomplete=\"off\" spellcheck=\"false\" placeholder=\"" << (conn.api_secret.empty() ? "" : "секрет сохранён, оставьте пустым чтобы не менять") << "\"></label>";
    body << "<button class=\"button\" type=\"submit\">Сохранить</button></form></div></section>";
    body << "<section class=\"panel\"><div class=\"head\"><div><h2>Комиссии MEXC Futures</h2><p class=\"muted\">Пары, где для подключённого API-аккаунта MEXC сейчас realMakerFee и realTakerFee равны 0.</p></div><span class=\"pill ok\">" << zero_both << " пар maker+taker 0%</span></div>";
    body << "<div class=\"scroll\"><table><thead><tr><th>Пара</th><th>Статус</th><th>API</th><th>Maker</th><th>Taker</th><th>Плечо</th><th>Мин. объём</th></tr></thead><tbody>";
    if (rows.empty()) {
        body << "<tr><td colspan=\"7\" class=\"muted\">Для подключённого аккаунта не найдено пар с maker+taker 0% или MEXC API сейчас не вернул комиссии.</td></tr>";
    }
    for (const auto& row : rows) {
        body << "<tr><td><strong>" << row.symbol << "</strong></td><td>";
        if (row.exists) body << "<span class=\"pill ok\">" << row.state << "</span>"; else body << "<span class=\"pill bad\">не найдено</span>";
        body << "</td><td><span class=\"pill " << (row.api_allowed ? "ok" : "bad") << "\">" << (row.api_allowed ? "разрешён" : "нет") << "</span></td>";
        body << "<td><span class=\"" << (row.zero_maker ? "fee" : "") << "\">" << row.maker_fee << "</span></td>";
        body << "<td><span class=\"" << (row.zero_taker ? "fee" : "") << "\">" << row.taker_fee << "</span></td>";
        body << "<td>" << html_escape(row.max_leverage) << "</td><td>" << html_escape(row.min_vol) << "</td></tr>";
    }
    body << "</tbody></table></div>";
    body << "<p class=\"small\">Список основан на персональном endpoint tiered_fee_rate/v2. Публичный zero-fee список MEXC может отличаться от фактической комиссии твоего API-аккаунта.</p></section>";
    body << "<section class=\"panel\"><div class=\"head\"><div><h2>Ghost Bot JSON для MEXC</h2><p class=\"muted\">Формат совместим с Cryptorg hook: action=open, strategy long/short, pairs, open, dca, close, stop. Предпросмотр не отправляет ордера.</p></div><span class=\"pill bad\">live осторожно</span></div>";
    body << "<form method=\"post\" action=\"/bot/preview\"><label class=\"wide\">JSON команда<textarea name=\"payload\" spellcheck=\"false\">" << html_escape(default_bot_json()) << "</textarea></label><button class=\"button secondary\" type=\"submit\">Показать план</button></form>";
    body << "<form method=\"post\" action=\"/bot/run\" style=\"margin-top:12px\"><label class=\"wide\">JSON команда для запуска<textarea name=\"payload\" spellcheck=\"false\">" << html_escape(default_bot_json()) << "</textarea></label><label style=\"display:flex;gap:8px;align-items:center;font-weight:700\"><input type=\"checkbox\" name=\"live\" value=\"1\" style=\"min-height:0;width:auto\"> Отправить ордера в MEXC</label><button class=\"button\" type=\"submit\">Запустить вручную</button></form>";
    body << "<p class=\"small\">Webhook endpoint: <code>/hook/&lt;secret&gt;</code>. Команда передаётся POST JSON в том же формате. Для безопасности secret задаётся в переменной M1_WEBHOOK_SECRET.</p></section>";
    return layout(body.str(), flash);
}

std::vector<std::string> json_array_objects(const std::string& array_json) {
    std::vector<std::string> objects;
    bool in_string = false;
    bool escaped = false;
    int depth = 0;
    size_t begin = std::string::npos;
    for (size_t i = 0; i < array_json.size(); ++i) {
        char ch = array_json[i];
        if (in_string) {
            if (escaped) escaped = false;
            else if (ch == '\\') escaped = true;
            else if (ch == '"') in_string = false;
            continue;
        }
        if (ch == '"') in_string = true;
        else if (ch == '{') {
            if (depth == 0) begin = i;
            depth++;
        } else if (ch == '}') {
            depth--;
            if (depth == 0 && begin != std::string::npos) {
                objects.push_back(array_json.substr(begin, i - begin + 1));
                begin = std::string::npos;
            }
        }
    }
    return objects;
}

std::string json_value_or_dash(const std::string& object, const std::string& key) {
    std::string value = json_field(object, key);
    return value.empty() ? "—" : value;
}

std::string format_time_ms(long long timestamp_ms) {
    if (timestamp_ms <= 0) return "-";
    std::time_t seconds = static_cast<std::time_t>(timestamp_ms / 1000);
    std::tm tm{};
    localtime_r(&seconds, &tm);
    char buffer[32];
    std::strftime(buffer, sizeof(buffer), "%Y-%m-%d %H:%M:%S", &tm);
    return buffer;
}

std::string status_class(const std::string& status) {
    if (status == "active" || status == "placed" || status == "dry_run") return "ok";
    if (status == "failed" || status.find("error") != std::string::npos) return "bad";
    return "";
}

std::string side_label(int side) {
    if (side == 1 || side == 2) return "long";
    if (side == 3 || side == 4) return "short";
    return std::to_string(side);
}

std::string order_type_label(int type) {
    if (type == 1) return "limit";
    if (type == 5) return "market";
    return std::to_string(type);
}

std::string render_asset_table(const std::string& assets) {
    std::vector<std::string> rows = json_array_objects(json_array_field(assets, "data"));
    std::ostringstream body;
    body << "<div class=\"scroll\"><table><thead><tr><th>Валюта</th><th>Equity</th><th>Доступно</th><th>Cash</th><th>Frozen</th><th>Position margin</th><th>Unrealized</th><th>Bonus</th><th>Available open</th><th>Debt</th></tr></thead><tbody>";
    if (rows.empty()) body << "<tr><td colspan=\"10\" class=\"muted\">Нет данных по активам.</td></tr>";
    for (const auto& row : rows) {
        body << "<tr><td><strong>" << html_escape(json_value_or_dash(row, "currency")) << "</strong></td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "equity")) << "</td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "availableBalance")) << "</td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "cashBalance")) << "</td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "frozenBalance")) << "</td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "positionMargin")) << "</td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "unrealized")) << "</td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "bonus")) << "</td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "availableOpen")) << "</td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "debtAmount")) << "</td></tr>";
    }
    body << "</tbody></table></div>";
    return body.str();
}

std::string render_positions_table(const std::string& positions) {
    std::vector<std::string> rows = json_array_objects(json_array_field(positions, "data"));
    std::ostringstream body;
    body << "<div class=\"scroll\"><table><thead><tr><th>Пара</th><th>Сторона</th><th>Vol</th><th>Avg</th><th>Плечо</th><th>Маржа</th><th>Ликвидация</th><th>Realised</th><th>Fee</th><th>ID</th></tr></thead><tbody>";
    if (rows.empty()) body << "<tr><td colspan=\"10\" class=\"muted\">Открытых позиций нет.</td></tr>";
    for (const auto& row : rows) {
        std::string side = json_field(row, "positionType") == "2" ? "short" : "long";
        body << "<tr><td><strong>" << html_escape(json_value_or_dash(row, "symbol")) << "</strong></td>";
        body << "<td><span class=\"pill " << (side == "long" ? "ok" : "bad") << "\">" << side << "</span></td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "holdVol")) << "</td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "holdAvgPrice")) << "</td>";
        body << "<td>x" << html_escape(json_value_or_dash(row, "leverage")) << "</td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "im")) << "</td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "liquidatePrice")) << "</td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "realised")) << "</td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "totalFee")) << "</td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "positionId")) << "</td></tr>";
    }
    body << "</tbody></table></div>";
    return body.str();
}

std::string render_orders_table(const std::string& orders, const std::string& empty_text) {
    std::vector<std::string> rows = json_array_objects(json_array_field(orders, "data"));
    std::ostringstream body;
    body << "<div class=\"scroll\"><table><thead><tr><th>ID</th><th>Пара</th><th>Side</th><th>Type</th><th>Цена</th><th>Vol</th><th>Плечо</th><th>State</th><th>Margin</th><th>Fee</th></tr></thead><tbody>";
    if (rows.empty()) body << "<tr><td colspan=\"10\" class=\"muted\">" << html_escape(empty_text) << "</td></tr>";
    for (const auto& row : rows) {
        std::string id = json_field(row, "orderId");
        if (id.empty()) id = json_field(row, "id");
        std::string type = json_field(row, "orderType");
        if (type.empty()) type = json_field(row, "triggerType");
        std::string price = json_field(row, "price");
        if (price.empty()) price = json_field(row, "triggerPrice");
        body << "<tr><td>" << html_escape(id.empty() ? "—" : id) << "</td>";
        body << "<td><strong>" << html_escape(json_value_or_dash(row, "symbol")) << "</strong></td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "side")) << "</td>";
        body << "<td>" << html_escape(type.empty() ? "—" : type) << "</td>";
        body << "<td>" << html_escape(price.empty() ? "—" : price) << "</td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "vol")) << "</td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "leverage")) << "</td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "state")) << "</td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "orderMargin")) << "</td>";
        body << "<td>" << html_escape(json_value_or_dash(row, "totalFee")) << "</td></tr>";
    }
    body << "</tbody></table></div>";
    return body.str();
}

std::string account_page(const std::string& flash = "") {
    MexcConnection conn = load_connection();
    if (conn.api_key.empty() || conn.api_secret.empty()) {
        std::ostringstream body;
        body << "<section class=\"head\"><div><p class=\"eyebrow\">MEXC account</p><h1>Аккаунт</h1><p class=\"lead\">Сначала сохраните API key и API secret для MEXC Futures.</p></div><a class=\"button\" href=\"/connections\">Подключения</a></section>";
        return layout(body.str(), "<div class=\"flash warn\">MEXC подключение не настроено.</div>");
    }

    long long now = now_ms();
    long long start = now - 30LL * 24 * 60 * 60 * 1000;
    std::string assets = mexc_get_private(conn, "/api/v1/private/account/assets");
    std::string positions = mexc_get_private(conn, "/api/v1/private/position/open_positions");
    std::string open_orders = mexc_get_private(conn, "/api/v1/private/order/list/open_orders", "page_num=1&page_size=100");
    std::string plan_orders = mexc_get_private(conn, "/api/v1/private/planorder/list/orders", "end_time=" + std::to_string(now) + "&page_num=1&page_size=100&start_time=" + std::to_string(start) + "&states=1");
    std::string stop_orders = mexc_get_private(conn, "/api/v1/private/stoporder/list/orders", "is_finished=0&page_num=1&page_size=100");

    std::string equity = parse_usdt_equity(assets);
    int asset_count = static_cast<int>(json_array_objects(json_array_field(assets, "data")).size());
    int position_count = static_cast<int>(json_array_objects(json_array_field(positions, "data")).size());
    int order_count = static_cast<int>(json_array_objects(json_array_field(open_orders, "data")).size());
    int plan_count = static_cast<int>(json_array_objects(json_array_field(plan_orders, "data")).size());
    int stop_count = static_cast<int>(json_array_objects(json_array_field(stop_orders, "data")).size());
    bool ok = assets.find("\"success\":true") != std::string::npos && positions.find("\"success\":true") != std::string::npos;

    std::ostringstream body;
    body << "<section class=\"head\"><div><p class=\"eyebrow\">MEXC account</p><h1>Аккаунт</h1><p class=\"lead\">Баланс, позиции, активные ордера и сырой read-only ответ MEXC Futures для подключённого ключа.</p></div><a class=\"button secondary\" href=\"/account\">Обновить</a></section>";
    body << "<section class=\"metric-grid\">";
    body << "<div class=\"metric\"><span>USDT equity</span><b>" << html_escape(equity.empty() ? "—" : equity) << "</b></div>";
    body << "<div class=\"metric\"><span>Активы</span><b>" << asset_count << "</b></div>";
    body << "<div class=\"metric\"><span>Позиции</span><b>" << position_count << "</b></div>";
    body << "<div class=\"metric\"><span>Ордера</span><b>" << (order_count + plan_count + stop_count) << "</b></div>";
    body << "</section>";
    body << "<section class=\"panel\"><h2>Подключение</h2><p><strong>" << html_escape(conn.label.empty() ? "MEXC Futures" : conn.label) << "</strong></p><p class=\"small\">API key: " << html_escape(mask_key(conn.api_key)) << "</p><p><span class=\"pill " << (ok ? "ok" : "bad") << "\">" << (ok ? "API отвечает" : "есть ошибка API") << "</span></p></section>";
    body << "<section class=\"panel\"><h2>Баланс</h2>" << render_asset_table(assets) << "</section>";
    body << "<section class=\"panel\"><h2>Открытые позиции</h2>" << render_positions_table(positions) << "</section>";
    body << "<section class=\"panel\"><h2>Текущие ордера</h2>" << render_orders_table(open_orders, "Активных лимитных ордеров нет.") << "</section>";
    body << "<section class=\"panel\"><h2>Плановые ордера</h2>" << render_orders_table(plan_orders, "Активных плановых ордеров нет.") << "</section>";
    body << "<section class=\"panel\"><h2>TP/SL ордера</h2>" << render_orders_table(stop_orders, "Активных TP/SL ордеров нет.") << "</section>";
    body << "<section class=\"panel\"><h2>Raw MEXC JSON</h2><p class=\"small\">Полные read-only ответы MEXC для анализа и диагностики.</p>";
    body << "<h3>Assets</h3><pre class=\"raw\">" << html_escape(assets) << "</pre>";
    body << "<h3>Open positions</h3><pre class=\"raw\">" << html_escape(positions) << "</pre>";
    body << "<h3>Current orders</h3><pre class=\"raw\">" << html_escape(open_orders) << "</pre>";
    body << "<h3>Plan orders</h3><pre class=\"raw\">" << html_escape(plan_orders) << "</pre>";
    body << "<h3>TP/SL orders</h3><pre class=\"raw\">" << html_escape(stop_orders) << "</pre></section>";
    return layout(body.str(), flash);
}

std::string render_deal_orders_table(const std::vector<DealOrder>& orders, const std::string& deal_id) {
    std::ostringstream body;
    body << "<div class=\"scroll\"><table><thead><tr><th>Label</th><th>Side</th><th>Type</th><th>Price</th><th>Vol</th><th>Margin</th><th>TP</th><th>SL</th><th>Status</th><th>Exchange ID</th><th>Time</th></tr></thead><tbody>";
    int count = 0;
    for (const auto& order : orders) {
        if (order.deal_id != deal_id) continue;
        ++count;
        body << "<tr><td><strong>" << html_escape(order.label) << "</strong></td>";
        body << "<td>" << html_escape(side_label(order.side)) << "</td>";
        body << "<td>" << html_escape(order_type_label(order.type)) << "</td>";
        body << "<td>" << (order.price > 0 ? format_number(order.price, 6) : "-") << "</td>";
        body << "<td>" << format_number(order.vol, 4) << "</td>";
        body << "<td>" << format_number(order.margin_usdt, 2) << " USDT</td>";
        body << "<td>" << (order.take_profit_price > 0 ? format_number(order.take_profit_price, 6) : "-") << "</td>";
        body << "<td>" << (order.stop_loss_price > 0 ? format_number(order.stop_loss_price, 6) : "-") << "</td>";
        body << "<td><span class=\"pill " << status_class(order.status) << "\">" << html_escape(order.status) << "</span></td>";
        body << "<td>" << html_escape(order.exchange_order_id.empty() ? "-" : order.exchange_order_id) << "</td>";
        body << "<td>" << html_escape(format_time_ms(order.created_at)) << "</td></tr>";
        body << "<tr><td colspan=\"11\"><details><summary>Request / response</summary><div class=\"detail-body\"><div class=\"grid\">";
        body << "<pre class=\"raw-small\">" << html_escape(order.request_json) << "</pre>";
        body << "<pre class=\"raw-small\">" << html_escape(order.response_json) << "</pre>";
        body << "</div></div></details></td></tr>";
    }
    if (count == 0) body << "<tr><td colspan=\"11\" class=\"muted\">No orders recorded for this deal.</td></tr>";
    body << "</tbody></table></div>";
    return body.str();
}

std::string deals_page(const std::string& flash = "") {
    std::vector<Deal> deals = load_deals();
    std::vector<DealOrder> orders = load_deal_orders();
    std::reverse(deals.begin(), deals.end());

    int active = 0;
    int live = 0;
    int failed = 0;
    for (const auto& deal : deals) {
        if (deal.status == "active" || deal.status == "active_with_errors") ++active;
        if (deal.live) ++live;
        if (deal.status == "failed" || deal.status.find("error") != std::string::npos) ++failed;
    }

    std::ostringstream body;
    body << "<section class=\"head\"><div><p class=\"eyebrow\">MEXC deals</p><h1>Сделки</h1><p class=\"lead\">Журнал запусков ботов: снимок настроек, статус сделки и все ордера, которые бот планировал или отправлял в MEXC.</p></div><a class=\"button secondary\" href=\"/deals\">Обновить</a></section>";
    body << "<section class=\"metric-grid\">";
    body << "<div class=\"metric\"><span>Total deals</span><b>" << deals.size() << "</b></div>";
    body << "<div class=\"metric\"><span>Active</span><b>" << active << "</b></div>";
    body << "<div class=\"metric\"><span>Live</span><b>" << live << "</b></div>";
    body << "<div class=\"metric\"><span>Failed</span><b>" << failed << "</b></div>";
    body << "</section>";

    if (deals.empty()) {
        body << "<section class=\"panel\"><p class=\"muted\">Сделок пока нет. Запусти dry-run или live-запуск бота, и здесь появится первая запись.</p></section>";
        return layout(body.str(), flash);
    }

    for (const auto& deal : deals) {
        int order_count = 0;
        int placed_count = 0;
        int failed_count = 0;
        for (const auto& order : orders) {
            if (order.deal_id != deal.deal_id) continue;
            ++order_count;
            if (order.status == "placed" || order.status == "dry_run") ++placed_count;
            if (order.status == "failed") ++failed_count;
        }

        body << "<section class=\"panel\">";
        body << "<div class=\"head\"><div><p class=\"eyebrow\">" << html_escape(deal.live ? "live" : "dry-run") << "</p>";
        body << "<h2>" << html_escape(deal.symbol) << " · " << html_escape(deal.strategy) << "</h2>";
        body << "<p class=\"small nowrap\">" << html_escape(deal.deal_id) << " · " << html_escape(format_time_ms(deal.created_at)) << "</p></div>";
        body << "<span class=\"pill " << status_class(deal.status) << "\">" << html_escape(deal.status) << "</span></div>";
        body << "<div class=\"metric-grid\">";
        body << "<div class=\"metric\"><span>Bot</span><b>" << html_escape(deal.bot_name.empty() ? "-" : deal.bot_name) << "</b></div>";
        body << "<div class=\"metric\"><span>Margin</span><b>" << format_number(deal.planned_margin_usdt, 2) << "</b></div>";
        body << "<div class=\"metric\"><span>Volume</span><b>" << format_number(deal.planned_volume, 4) << "</b></div>";
        body << "<div class=\"metric\"><span>Orders</span><b>" << placed_count << "/" << order_count << "</b></div>";
        body << "</div>";
        body << "<p class=\"small\">Mark price: " << format_number(deal.mark_price, 6) << " · Leverage: x" << deal.leverage << " · Open type: " << deal.open_type;
        if (failed_count > 0) body << " · Failed orders: " << failed_count;
        body << "</p>";
        bool can_manage = deal.live && deal.status != "released" && deal.status != "force_closed" && deal.status != "closed_no_position" && deal.status != "failed";
        if (can_manage) {
            body << "<div style=\"display:flex;gap:10px;flex-wrap:wrap;margin:12px 0\">";
            body << "<form method=\"post\" action=\"/deals/release\"><input type=\"hidden\" name=\"deal_id\" value=\"" << html_escape(deal.deal_id) << "\"><button class=\"button secondary\" type=\"submit\">Release</button></form>";
            body << "<form method=\"post\" action=\"/deals/force-close\"><input type=\"hidden\" name=\"deal_id\" value=\"" << html_escape(deal.deal_id) << "\"><button class=\"button\" type=\"submit\">Force close</button></form>";
            body << "</div>";
        }
        body << "<details open><summary>Orders</summary><div class=\"detail-body\">" << render_deal_orders_table(orders, deal.deal_id) << "</div></details>";
        body << "<details><summary>Config snapshot</summary><div class=\"detail-body\"><pre class=\"raw\">" << html_escape(deal.config_json) << "</pre></div></details>";
        body << "</section>";
    }
    return layout(body.str(), flash);
}

BotConfig bot_from_form(const std::map<std::string, std::string>& fields) {
    BotConfig bot;
    bot.id = std::atoi(fields.count("id") ? fields.at("id").c_str() : "0");
    bot.name = trim(fields.count("name") ? fields.at("name") : "");
    if (bot.name.empty()) bot.name = "MEXC Bot";
    bot.strategy = fields.count("strategy") ? fields.at("strategy") : "long";
    bot.pair = fields.count("pair") ? fields.at("pair") : "XRPUSDT";
    bot.leverage = std::max(1, std::min(100, std::atoi((fields.count("leverage") ? fields.at("leverage") : "10").c_str())));
    bot.margin_type = fields.count("margin_type") ? fields.at("margin_type") : "cross";
    if (bot.margin_type != "isolated") bot.margin_type = "cross";
    bot.order_volume = std::atof((fields.count("order_volume") ? fields.at("order_volume") : "6").c_str());
    bot.continuous = fields.count("continuous") && fields.at("continuous") == "1";
    bot.cycles = std::max(1, std::atoi((fields.count("cycles") ? fields.at("cycles") : "1").c_str()));
    bot.dca_volume = std::atof((fields.count("dca_volume") ? fields.at("dca_volume") : "6").c_str());
    bot.dca_percent = std::atof((fields.count("dca_percent") ? fields.at("dca_percent") : "1").c_str());
    bot.dca_active = std::max(1, std::min(20, std::atoi((fields.count("dca_active") ? fields.at("dca_active") : "2").c_str())));
    bot.dca_max = bot.dca_active;
    bot.dca_multiplier_volume = std::atof((fields.count("dca_multiplier_volume") ? fields.at("dca_multiplier_volume") : "1.2").c_str());
    bot.dca_multiplier_price = std::atof((fields.count("dca_multiplier_price") ? fields.at("dca_multiplier_price") : "1.2").c_str());
    bot.close_enabled = fields.count("close_enabled") && fields.at("close_enabled") == "1";
    bot.take_profit_percent = std::atof((fields.count("take_profit_percent") ? fields.at("take_profit_percent") : "0.5").c_str());
    bot.stop_enabled = fields.count("stop_enabled") && fields.at("stop_enabled") == "1";
    bot.stop_loss_percent = std::atof((fields.count("stop_loss_percent") ? fields.at("stop_loss_percent") : "3").c_str());
    return bot;
}

std::string bots_page(const std::string& flash = "") {
    std::vector<BotConfig> bots = load_bots();
    std::ostringstream body;
    body << "<section class=\"head\"><div><p class=\"eyebrow\">MEXC bots</p><h1>Список ботов</h1><p class=\"lead\">Здесь отображаются все настроенные MEXC-боты. Их можно запускать вручную или использовать как шаблон для webhook JSON.</p></div><a class=\"button\" href=\"/bots/new\">Создать бота</a></section>";
    body << "<section class=\"panel\"><div class=\"scroll\"><table><thead><tr><th>ID</th><th>Название</th><th>Пара</th><th>Стратегия</th><th>Первый ордер</th><th>DCA</th><th>TP / SL</th><th>Действия</th></tr></thead><tbody>";
    if (bots.empty()) {
        body << "<tr><td colspan=\"8\" class=\"muted\">Ботов пока нет.</td></tr>";
    }
    for (const auto& bot : bots) {
        body << "<tr><td>" << bot.id << "</td><td><strong>" << html_escape(bot.name) << "</strong></td><td>" << html_escape(normalize_symbol(bot.pair)) << "</td><td>" << html_escape(bot.strategy) << "</td>";
        body << "<td>" << format_number(bot.order_volume, 2) << " USDT · x" << bot.leverage << "</td><td>" << bot.dca_active << " SO · " << format_number(bot.dca_percent, 2) << "%</td>";
        body << "<td>" << (bot.close_enabled ? format_number(bot.take_profit_percent, 2) + "%" : "off") << " / " << (bot.stop_enabled ? format_number(bot.stop_loss_percent, 2) + "%" : "off") << "</td>";
        body << "<td><a class=\"button secondary\" href=\"/bots/view?id=" << bot.id << "\">Просмотр</a> <a class=\"button secondary\" href=\"/bots/edit?id=" << bot.id << "\">Изменить</a> <a class=\"button secondary\" href=\"/bots/launch?id=" << bot.id << "\">Запуск</a> <form method=\"post\" action=\"/bots/delete\" style=\"display:inline\"><input type=\"hidden\" name=\"id\" value=\"" << bot.id << "\"><button class=\"button secondary\" type=\"submit\">Удалить</button></form></td></tr>";
    }
    body << "</tbody></table></div></section>";
    return layout(body.str(), flash);
}

std::string pair_datalist() {
    std::ostringstream out;
    out << "<datalist id=\"mexc-pairs\">";
    for (const auto& symbol : all_contract_symbols()) out << "<option value=\"" << html_escape(symbol) << "\"></option>";
    out << "</datalist>";
    return out.str();
}

std::string bot_form_page(const BotConfig& bot, bool readonly = false, const std::string& flash = "") {
    std::string ro = readonly ? " readonly disabled" : "";
    std::ostringstream body;
    body << "<section class=\"head\"><div><p class=\"eyebrow\">MEXC bot</p><h1>" << (readonly ? "Настройки бота" : (bot.id ? "Изменить бота" : "Создать бота")) << "</h1></div><a class=\"button secondary\" href=\"/bots\">К списку</a></section>";
    body << "<form method=\"post\" action=\"/bots/save\">";
    body << "<input type=\"hidden\" name=\"id\" value=\"" << bot.id << "\">";
    body << "<section class=\"panel\"><h2>Основное</h2><div class=\"form\">";
    body << "<label>Название бота<input name=\"name\" value=\"" << html_escape(bot.name) << "\"" << ro << "></label>";
    body << "<label>Стратегия<select name=\"strategy\"" << (readonly ? " disabled" : "") << "><option value=\"long\"" << (bot.strategy == "long" ? " selected" : "") << ">Лонг</option><option value=\"short\"" << (bot.strategy == "short" ? " selected" : "") << ">Шорт</option></select></label>";
    body << "<label>Торговая пара<input name=\"pair\" list=\"mexc-pairs\" value=\"" << html_escape(normalize_symbol(bot.pair)) << "\"" << ro << "></label>";
    body << pair_datalist();
    body << "</div></section>";
    body << "<section class=\"panel\"><h2>Открытие</h2><div class=\"form\">";
    body << "<label>Плечо: <span id=\"levVal\">" << bot.leverage << "</span><input type=\"range\" min=\"1\" max=\"100\" name=\"leverage\" value=\"" << bot.leverage << "\"" << (readonly ? " disabled" : "") << " oninput=\"document.getElementById('levVal').textContent=this.value\"></label>";
    body << "<label>Режим маржи<select name=\"margin_type\"" << (readonly ? " disabled" : "") << "><option value=\"cross\"" << (bot.margin_type == "cross" ? " selected" : "") << ">Перекрёстная</option><option value=\"isolated\"" << (bot.margin_type == "isolated" ? " selected" : "") << ">Изолированная</option></select></label>";
    body << "<label>Объём первого ордера, USDT<input name=\"order_volume\" type=\"number\" step=\"0.01\" value=\"" << format_number(bot.order_volume, 2) << "\"" << ro << "></label>";
    body << "<label><span>Открывать сделки в непрерывном режиме</span><input type=\"checkbox\" name=\"continuous\" value=\"1\"" << (bot.continuous ? " checked" : "") << (readonly ? " disabled" : "") << "></label>";
    body << "<label>Ограничение успешно завершённых сделок<input name=\"cycles\" type=\"number\" min=\"1\" step=\"1\" value=\"" << bot.cycles << "\"" << ro << "></label>";
    body << "</div><p class=\"small\">Если бот закрывается вручную по рынку или по стоп-лоссу, следующая автоматическая сделка не должна запускаться.</p></section>";
    body << "<section class=\"panel\"><h2>Усреднение</h2><div class=\"form\">";
    body << "<label>Объём первого страховочного ордера, USDT<input name=\"dca_volume\" type=\"number\" step=\"0.01\" value=\"" << format_number(bot.dca_volume, 2) << "\"" << ro << "></label>";
    body << "<label>Шаг страховочных ордеров, %<input name=\"dca_percent\" type=\"number\" step=\"0.01\" value=\"" << format_number(bot.dca_percent, 2) << "\"" << ro << "></label>";
    body << "<label>Кол-во страховочных ордеров: <span id=\"dcaVal\">" << bot.dca_active << "</span><input type=\"range\" min=\"1\" max=\"20\" name=\"dca_active\" value=\"" << bot.dca_active << "\"" << (readonly ? " disabled" : "") << " oninput=\"document.getElementById('dcaVal').textContent=this.value\"></label>";
    body << "<label>Множитель объёма страховочных ордеров<input name=\"dca_multiplier_volume\" type=\"number\" step=\"0.01\" value=\"" << format_number(bot.dca_multiplier_volume, 2) << "\"" << ro << "></label>";
    body << "<label>Множитель шага цены страховочных ордеров<input name=\"dca_multiplier_price\" type=\"number\" step=\"0.01\" value=\"" << format_number(bot.dca_multiplier_price, 2) << "\"" << ro << "></label>";
    body << "</div></section>";
    body << "<section class=\"panel\"><h2>Закрытие</h2><div class=\"form\"><label><span>Возможность закрытия сделки ботом</span><input type=\"checkbox\" name=\"close_enabled\" value=\"1\"" << (bot.close_enabled ? " checked" : "") << (readonly ? " disabled" : "") << "></label>";
    body << "<label>Тейк-профит от средней точки входа, %<input name=\"take_profit_percent\" type=\"number\" step=\"0.01\" value=\"" << format_number(bot.take_profit_percent, 2) << "\"" << ro << "></label></div></section>";
    body << "<section class=\"panel\"><h2>Стоп-лосс</h2><div class=\"form\"><label><span>Выставлять стоп-лосс ордера</span><input type=\"checkbox\" name=\"stop_enabled\" value=\"1\"" << (bot.stop_enabled ? " checked" : "") << (readonly ? " disabled" : "") << "></label>";
    body << "<label>Стоп-лосс от средней точки входа, %<input name=\"stop_loss_percent\" type=\"number\" step=\"0.01\" value=\"" << format_number(bot.stop_loss_percent, 2) << "\"" << ro << "></label></div></section>";
    body << "<section class=\"panel\"><h2>JSON</h2><pre>" << html_escape(bot_to_json(bot)) << "</pre></section>";
    if (!readonly) body << "<button class=\"button\" type=\"submit\">Сохранить</button>";
    body << "</form>";
    return layout(body.str(), flash);
}

std::string bot_launch_page(const BotConfig& bot, const std::string& flash = "", const std::string& result = "") {
    std::string payload = bot_to_json(bot);
    BotPlan plan = build_bot_plan(parse_bot_config(payload));
    std::string secret = getenv_or("M1_WEBHOOK_SECRET", "");
    std::ostringstream body;
    body << "<section class=\"head\"><div><p class=\"eyebrow\">Launch</p><h1>Запуск бота: " << html_escape(bot.name) << "</h1><p class=\"lead\">Перед live-запуском проверьте план ордеров и аккаунт MEXC.</p></div><a class=\"button secondary\" href=\"/bots\">К списку</a></section>";
    body << "<section class=\"panel\"><h2>Webhook</h2>";
    body << "<p class=\"small\">Live webhook:</p><pre>https://m1.griders.ru/hook/bot/" << bot.id << "/" << html_escape(secret.empty() ? "SECRET_NOT_SET" : secret) << "</pre>";
    body << "<p class=\"small\">Dry-run webhook:</p><pre>https://m1.griders.ru/hook/bot/" << bot.id << "/" << html_escape(secret.empty() ? "SECRET_NOT_SET" : secret) << "?dryRun=1</pre></section>";
    body << "<section class=\"panel\"><h2>План ордеров</h2>" << render_plan_table(plan) << "</section>";
    body << "<section class=\"panel\"><h2>JSON</h2><pre>" << html_escape(payload) << "</pre></section>";
    body << "<section class=\"panel\"><h2>Ручной запуск</h2><form method=\"post\" action=\"/bots/run\"><input type=\"hidden\" name=\"id\" value=\"" << bot.id << "\"><button class=\"button secondary\" name=\"dry\" value=\"1\" type=\"submit\">Dry-run</button> <label style=\"display:flex;gap:8px;align-items:center;margin-top:12px\"><input type=\"checkbox\" name=\"live\" value=\"1\" style=\"min-height:0;width:auto\"> Подтверждаю live-запуск на MEXC</label><button class=\"button\" type=\"submit\">Запустить live</button></form></section>";
    if (!result.empty()) body << "<section class=\"panel\"><h2>Результат</h2><pre>" << html_escape(result) << "</pre></section>";
    return layout(body.str(), flash);
}

std::string response(int status, const std::string& body, const std::string& content_type = "text/html; charset=utf-8", const std::vector<std::string>& extra_headers = {}) {
    std::ostringstream out;
    std::string reason = status == 200 ? "OK" : status == 302 ? "Found" : status == 401 ? "Unauthorized" : status == 404 ? "Not Found" : "Error";
    out << "HTTP/1.1 " << status << ' ' << reason << "\r\n";
    out << "Content-Type: " << content_type << "\r\n";
    out << "Content-Length: " << body.size() << "\r\n";
    out << "Connection: close\r\n";
    for (const std::string& header : extra_headers) out << header << "\r\n";
    out << "\r\n" << body;
    return out.str();
}

std::string redirect(const std::string& target) {
    return response(302, "", "text/plain", {"Location: " + target});
}

std::map<std::string, std::string> parse_headers(const std::string& raw) {
    std::map<std::string, std::string> headers;
    std::istringstream in(raw);
    std::string line;
    std::getline(in, line);
    while (std::getline(in, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) break;
        size_t colon = line.find(':');
        if (colon == std::string::npos) continue;
        std::string key = line.substr(0, colon);
        std::transform(key.begin(), key.end(), key.begin(), [](unsigned char c) { return std::tolower(c); });
        headers[key] = trim(line.substr(colon + 1));
    }
    return headers;
}

void handle_client(int fd) {
    std::string raw;
    char buffer[8192];
    ssize_t n;
    while ((n = recv(fd, buffer, sizeof(buffer), 0)) > 0) {
        raw.append(buffer, n);
        if (raw.find("\r\n\r\n") != std::string::npos) break;
        if (raw.size() > 65536) break;
    }
    if (raw.empty()) {
        close(fd);
        return;
    }
    size_t header_end = raw.find("\r\n\r\n");
    std::string header_raw = raw.substr(0, header_end);
    std::map<std::string, std::string> headers = parse_headers(header_raw);
    std::istringstream first(header_raw);
    std::string method, path, version;
    first >> method >> path >> version;
    std::string route_path = path;
    size_t query_pos = route_path.find('?');
    if (query_pos != std::string::npos) route_path = route_path.substr(0, query_pos);
    size_t content_length = 0;
    if (headers.count("content-length")) content_length = static_cast<size_t>(std::stoul(headers["content-length"]));
    std::string body = header_end == std::string::npos ? "" : raw.substr(header_end + 4);
    while (body.size() < content_length && (n = recv(fd, buffer, sizeof(buffer), 0)) > 0) body.append(buffer, n);

    if (method == "POST" && route_path.rfind("/hook/bot/", 0) == 0) {
        std::string rest = route_path.substr(std::string("/hook/bot/").size());
        size_t slash = rest.find('/');
        std::string id_text = slash == std::string::npos ? "" : rest.substr(0, slash);
        std::string secret = slash == std::string::npos ? "" : rest.substr(slash + 1);
        std::string expected = getenv_or("M1_WEBHOOK_SECRET", "");
        std::string out;
        if (expected.empty() || secret != expected || id_text.empty()) {
            out = response(404, "not found\n", "text/plain; charset=utf-8");
        } else {
            int id = std::atoi(id_text.c_str());
            bool live_webhook = path.find("dryRun=1") == std::string::npos;
            out = response(200, execute_saved_bot(id, live_webhook), "application/json; charset=utf-8");
        }
        send(fd, out.data(), out.size(), 0);
        close(fd);
        return;
    }

    if (method == "POST" && route_path.rfind("/hook/", 0) == 0) {
        std::string secret = route_path.substr(std::string("/hook/").size());
        std::string expected = getenv_or("M1_WEBHOOK_SECRET", "");
        std::string out;
        if (expected.empty() || secret != expected) {
            out = response(404, "not found\n", "text/plain; charset=utf-8");
        } else {
            BotPlan plan = build_bot_plan(parse_bot_config(body));
            bool live_webhook = path.find("dryRun=1") == std::string::npos;
            out = response(200, execute_plan(plan, live_webhook), "application/json; charset=utf-8");
        }
        send(fd, out.data(), out.size(), 0);
        close(fd);
        return;
    }

    if (method == "POST" && (route_path == "/bot/preview" || route_path == "/bot/run")) {
        std::string out;
        if (!is_authenticated(headers)) {
            out = response(401, "MEXC M1 admin access required\n", "text/plain; charset=utf-8", {"WWW-Authenticate: Basic realm=\"MEXC M1\""});
        } else {
            auto fields = parse_form(body);
            std::string payload = fields["payload"].empty() ? default_bot_json() : fields["payload"];
            bool live = route_path == "/bot/run" && fields["live"] == "1";
            BotPlan plan = build_bot_plan(parse_bot_config(payload));
            std::ostringstream page;
            page << "<section class=\"panel\"><h2>" << (route_path == "/bot/run" ? "MEXC bot launch result" : "MEXC bot launch plan") << "</h2>";
            page << "<p class=\"muted\">" << (live ? "Live request was sent if validation passed." : "Preview/dry-run. No orders were sent.") << "</p>";
            page << render_plan_table(plan);
            if (route_path == "/bot/run") {
                page << "<h3>Response</h3><pre>" << html_escape(execute_plan(plan, live)) << "</pre>";
            }
            page << "<h3>JSON</h3><pre>" << html_escape(payload) << "</pre>";
            page << "<p><a class=\"button secondary\" href=\"/\">Back</a></p></section>";
            out = response(200, layout(page.str(), live ? "<div class=\"flash warn\">Live request sent. Check MEXC positions and orders.</div>" : ""));
        }
        send(fd, out.data(), out.size(), 0);
        close(fd);
        return;
    }

    if (route_path == "/" || route_path == "/bots" || route_path == "/deals" || route_path == "/deals/release" || route_path == "/deals/force-close" || route_path == "/bots/new" || route_path == "/bots/view" || route_path == "/bots/edit" || route_path == "/bots/launch" || route_path == "/account" || route_path == "/connections" || route_path == "/bots/save" || route_path == "/bots/run" || route_path == "/bots/delete") {
        std::string out;
        if (!is_authenticated(headers)) {
            out = response(401, "MEXC M1 admin access required\n", "text/plain; charset=utf-8", {"WWW-Authenticate: Basic realm=\"MEXC M1\""});
        } else if (method == "GET" && (route_path == "/" || route_path == "/bots")) {
            out = response(200, bots_page());
        } else if (method == "GET" && route_path == "/connections") {
            out = response(200, home_page());
        } else if (method == "GET" && route_path == "/account") {
            out = response(200, account_page());
        } else if (method == "GET" && route_path == "/deals") {
            out = response(200, deals_page());
        } else if (method == "POST" && route_path == "/deals/release") {
            auto fields = parse_form(body);
            std::string result = release_deal(fields["deal_id"]);
            out = response(200, deals_page("<div class=\"flash\">Release result: " + html_escape(result) + "</div>"));
        } else if (method == "POST" && route_path == "/deals/force-close") {
            auto fields = parse_form(body);
            std::string result = force_close_deal(fields["deal_id"]);
            bool ok = result.find("\"ok\":true") != std::string::npos;
            out = response(200, deals_page(std::string("<div class=\"flash ") + (ok ? "" : "warn") + "\">Force close result: " + html_escape(result) + "</div>"));
        } else if (method == "GET" && route_path == "/bots/new") {
            out = response(200, bot_form_page(BotConfig{}));
        } else if (method == "GET" && (route_path == "/bots/view" || route_path == "/bots/edit")) {
            int id = std::atoi(query_param(path, "id").c_str());
            std::vector<BotConfig> bots = load_bots();
            BotConfig* bot = find_bot(bots, id);
            if (!bot) {
                out = response(200, bots_page("<div class=\"flash warn\">Bot not found.</div>"));
            } else {
                out = response(200, bot_form_page(*bot, route_path == "/bots/view"));
            }
        } else if (method == "GET" && route_path == "/bots/launch") {
            int id = std::atoi(query_param(path, "id").c_str());
            std::vector<BotConfig> bots = load_bots();
            BotConfig* bot = find_bot(bots, id);
            if (!bot) {
                out = response(200, bots_page("<div class=\"flash warn\">Bot not found.</div>"));
            } else {
                out = response(200, bot_launch_page(*bot));
            }
        } else if (method == "POST" && route_path == "/bots/save") {
            auto fields = parse_form(body);
            BotConfig incoming = bot_from_form(fields);
            std::vector<BotConfig> bots = load_bots();
            if (incoming.id > 0) {
                BotConfig* existing = find_bot(bots, incoming.id);
                if (existing) *existing = incoming;
                else bots.push_back(incoming);
            } else {
                incoming.id = next_bot_id(bots);
                bots.push_back(incoming);
            }
            save_bots(bots);
            out = response(200, bots_page("<div class=\"flash\">Bot saved.</div>"));
        } else if (method == "POST" && route_path == "/bots/run") {
            auto fields = parse_form(body);
            int id = std::atoi(fields["id"].c_str());
            std::vector<BotConfig> bots = load_bots();
            BotConfig* bot = find_bot(bots, id);
            if (!bot) {
                out = response(200, bots_page("<div class=\"flash warn\">Bot not found.</div>"));
            } else {
                bool live = fields["live"] == "1" && fields["dry"] != "1";
                std::string result = execute_saved_bot(id, live);
                out = response(200, bot_launch_page(*bot, live ? "<div class=\"flash warn\">Live request sent. Check MEXC positions and orders.</div>" : "<div class=\"flash\">Dry-run completed. No orders were sent.</div>", result));
            }
        } else if (method == "POST" && route_path == "/bots/delete") {
            auto fields = parse_form(body);
            int id = std::atoi(fields["id"].c_str());
            std::vector<BotConfig> bots = load_bots();
            bots.erase(std::remove_if(bots.begin(), bots.end(), [id](const BotConfig& bot) { return bot.id == id; }), bots.end());
            save_bots(bots);
            out = response(200, bots_page("<div class=\"flash warn\">Bot deleted.</div>"));
        } else {
            out = response(404, "not found\n", "text/plain; charset=utf-8");
        }
        send(fd, out.data(), out.size(), 0);
        close(fd);
        return;
    }

    std::string out;
    if (path == "/health") {
        out = response(200, "ok\n", "text/plain; charset=utf-8");
    } else if (!is_authenticated(headers)) {
        out = response(401, "MEXC M1 admin access required\n", "text/plain; charset=utf-8", {"WWW-Authenticate: Basic realm=\"MEXC M1\""});
    } else if (method == "GET" && (path == "/" || path.rfind("/?", 0) == 0)) {
        out = response(200, home_page());
    } else if (method == "POST" && path == "/connection") {
        auto fields = parse_form(body);
        MexcConnection current = load_connection();
        current.label = trim(fields["label"]).empty() ? "MEXC Futures" : trim(fields["label"]);
        current.api_key = trim(fields["api_key"]);
        if (!trim(fields["api_secret"]).empty()) current.api_secret = trim(fields["api_secret"]);
        save_connection(current);
        out = response(200, home_page("<div class=\"flash\">MEXC подключение сохранено.</div>"));
    } else if (method == "POST" && path == "/test") {
        MexcConnection conn = load_connection();
        if (conn.api_key.empty() || conn.api_secret.empty()) {
            out = response(200, home_page("<div class=\"flash warn\">Укажите API key и API secret MEXC.</div>"));
        } else {
            std::string assets = mexc_get_private(conn, "/api/v1/private/account/assets");
            std::string positions = mexc_get_private(conn, "/api/v1/private/position/open_positions");
            std::string equity = parse_usdt_equity(assets);
            bool ok = assets.find("\"success\":true") != std::string::npos && positions.find("\"success\":true") != std::string::npos;
            std::ostringstream flash;
            if (ok) {
                flash << "<div class=\"flash\">MEXC API проверен. USDT equity: " << html_escape(equity.empty() ? "не удалось распознать" : equity) << ".</div>";
            } else {
                flash << "<div class=\"flash warn\">MEXC API вернул ошибку. Ответ assets: " << html_escape(assets.substr(0, 400)) << "</div>";
            }
            out = response(200, home_page(flash.str()));
        }
    } else {
        out = response(404, "not found\n", "text/plain; charset=utf-8");
    }
    send(fd, out.data(), out.size(), 0);
    close(fd);
}

