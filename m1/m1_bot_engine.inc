BotConfig parse_bot_config(const std::string& json) {
    BotConfig cfg;
    cfg.action = json_field(json, "action").empty() ? cfg.action : json_field(json, "action");
    std::string params = json_object_field(json, "params");
    if (params.empty()) return cfg;

    cfg.strategy = json_field(params, "strategy").empty() ? cfg.strategy : json_field(params, "strategy");
    std::string pair = first_json_string_in_array(json_array_field(params, "pairs"));
    if (!pair.empty()) cfg.pair = pair;

    std::string open = json_object_field(params, "open");
    if (!open.empty()) {
        cfg.order_volume = json_double_field(open, "orderVolume", cfg.order_volume);
        cfg.leverage = json_int_field(open, "leverage", cfg.leverage);
        cfg.margin_type = json_field(open, "marginType").empty() ? cfg.margin_type : json_field(open, "marginType");
        cfg.order_type = json_field(open, "orderType").empty() ? cfg.order_type : json_field(open, "orderType");
    }

    std::string dca = json_object_field(params, "dca");
    if (!dca.empty()) {
        cfg.dca_enabled = json_bool_field(dca, "enabled", cfg.dca_enabled);
        cfg.dca_max = json_int_field(dca, "max", cfg.dca_max);
        cfg.dca_active = json_int_field(dca, "active", cfg.dca_active);
        cfg.dca_volume = json_double_field(dca, "volume", cfg.dca_volume);
        cfg.dca_percent = json_double_field(dca, "percent", cfg.dca_percent);
        cfg.dca_multiplier_volume = json_double_field(dca, "multiplierVolume", cfg.dca_multiplier_volume);
        cfg.dca_multiplier_price = json_double_field(dca, "multiplierPrice", cfg.dca_multiplier_price);
    }

    std::string close = json_object_field(params, "close");
    if (!close.empty()) {
        cfg.close_enabled = json_bool_field(close, "enabled", cfg.close_enabled);
        cfg.take_profit_percent = json_double_field(close, "value", cfg.take_profit_percent);
    }

    std::string stop = json_object_field(params, "stop");
    if (!stop.empty()) {
        cfg.stop_enabled = json_bool_field(stop, "enabled", cfg.stop_enabled);
        cfg.stop_loss_percent = json_double_field(stop, "value", cfg.stop_loss_percent);
    }
    return cfg;
}

std::string default_bot_json() {
    return "{\n"
           "  \"action\": \"open\",\n"
           "  \"params\": {\n"
           "    \"strategy\": \"long\",\n"
           "    \"pairs\": [\"XRPUSDT\"],\n"
           "    \"open\": {\"orderVolume\": \"6\", \"leverage\": 10, \"marginType\": \"cross\", \"orderType\": \"Market\"},\n"
           "    \"dca\": {\"enabled\": true, \"max\": 2, \"active\": 2, \"volume\": \"6\", \"percent\": \"1\", \"multiplierVolume\": \"1.2\", \"multiplierPrice\": \"1.2\"},\n"
           "    \"close\": {\"enabled\": true, \"event\": \"percentage\", \"value\": \"0.5\"},\n"
           "    \"stop\": {\"enabled\": true, \"event\": \"percentage\", \"value\": \"3\"}\n"
           "  }\n"
           "}";
}

BotPlan build_bot_plan(const BotConfig& cfg) {
    BotPlan plan;
    plan.config = cfg;
    plan.config.pair = compact_symbol(normalize_symbol(cfg.pair));
    std::string symbol = normalize_symbol(cfg.pair);
    plan.spec = contract_spec(symbol);
    plan.mark_price = fair_price(symbol);

    std::string action = cfg.action;
    std::transform(action.begin(), action.end(), action.begin(), [](unsigned char c) { return std::tolower(c); });
    std::string strategy = cfg.strategy;
    std::transform(strategy.begin(), strategy.end(), strategy.begin(), [](unsigned char c) { return std::tolower(c); });

    if (action != "open") plan.errors.push_back("M1 пока поддерживает только action=open.");
    if (strategy != "long" && strategy != "short") plan.errors.push_back("strategy должен быть long или short.");
    if (!plan.spec.exists) plan.errors.push_back("Пара не найдена в MEXC futures contract/detail.");
    if (plan.spec.exists && !plan.spec.api_allowed) plan.errors.push_back("Для пары MEXC API trading не разрешён.");
    if (plan.mark_price <= 0) plan.errors.push_back("Не удалось получить fair price MEXC.");
    if (cfg.order_volume <= 0) plan.errors.push_back("orderVolume должен быть больше 0.");
    if (cfg.leverage <= 0) plan.errors.push_back("leverage должен быть больше 0.");
    if (plan.spec.max_leverage > 0 && cfg.leverage > plan.spec.max_leverage) plan.errors.push_back("Плечо выше максимума MEXC для пары.");
    if (!plan.errors.empty()) return plan;

    bool is_long = strategy == "long";
    int open_side = is_long ? 1 : 3;
    int open_type = cfg.margin_type == "isolated" ? 1 : 2;
    double cumulative_qty = 0.0;
    double cumulative_value = 0.0;
    auto apply_estimated_protection = [&](PlannedOrder& order, double fill_price) {
        if (order.vol <= 0 || fill_price <= 0 || plan.spec.contract_size <= 0) return;
        double qty = order.vol * plan.spec.contract_size;
        cumulative_qty += qty;
        cumulative_value += fill_price * qty;
        double avg_price = cumulative_qty > 0 ? cumulative_value / cumulative_qty : fill_price;
        order.take_profit_price = cfg.close_enabled ? (is_long ? avg_price * (1 + cfg.take_profit_percent / 100.0) : avg_price * (1 - cfg.take_profit_percent / 100.0)) : 0.0;
        order.stop_loss_price = cfg.stop_enabled ? (is_long ? avg_price * (1 - cfg.stop_loss_percent / 100.0) : avg_price * (1 + cfg.stop_loss_percent / 100.0)) : 0.0;
    };

    PlannedOrder first;
    first.label = "first market";
    first.symbol = symbol;
    first.side = open_side;
    first.type = 5;
    first.open_type = open_type;
    first.leverage = cfg.leverage;
    first.price = 0.0;
    first.margin_usdt = cfg.order_volume;
    first.vol = contracts_from_margin(cfg.order_volume, cfg.leverage, plan.mark_price, plan.spec);
    apply_estimated_protection(first, plan.mark_price);
    if (first.vol <= 0) plan.errors.push_back("Не удалось рассчитать объём первого ордера.");
    plan.orders.push_back(first);

    if (cfg.dca_enabled) {
        int active = std::max(0, std::min(cfg.dca_active, cfg.dca_max));
        double cumulative_distance = 0.0;
        for (int i = 0; i < active; ++i) {
            double step = cfg.dca_percent * std::pow(cfg.dca_multiplier_price, i);
            cumulative_distance += step;
            double price = is_long ? plan.mark_price * (1 - cumulative_distance / 100.0) : plan.mark_price * (1 + cumulative_distance / 100.0);
            double margin = cfg.dca_volume * std::pow(cfg.dca_multiplier_volume, i);
            PlannedOrder order;
            order.label = "dca " + std::to_string(i + 1);
            order.symbol = symbol;
            order.side = open_side;
            order.type = 1;
            order.open_type = open_type;
            order.leverage = cfg.leverage;
            order.price = price;
            order.margin_usdt = margin;
            order.vol = contracts_from_margin(margin, cfg.leverage, price, plan.spec);
            if (order.vol <= 0) plan.warnings.push_back("DCA " + std::to_string(i + 1) + ": объём не рассчитан.");
            else {
                apply_estimated_protection(order, price);
                plan.orders.push_back(order);
            }
        }
    }
    return plan;
}

std::vector<std::string> flat_json_objects(const std::string& json) {
    std::vector<std::string> objects;
    int depth = 0;
    bool in_string = false;
    bool escaped = false;
    size_t begin = std::string::npos;
    for (size_t i = 0; i < json.size(); ++i) {
        char ch = json[i];
        if (in_string) {
            if (escaped) escaped = false;
            else if (ch == '\\') escaped = true;
            else if (ch == '"') in_string = false;
            continue;
        }
        if (ch == '"') {
            in_string = true;
        } else if (ch == '{') {
            if (depth == 0) begin = i;
            depth++;
        } else if (ch == '}') {
            depth--;
            if (depth == 0 && begin != std::string::npos) {
                objects.push_back(json.substr(begin, i - begin + 1));
                begin = std::string::npos;
            }
        }
    }
    return objects;
}

std::string position_object_for_run(const std::string& positions_json, const ActiveRun& run) {
    int expected_type = run.strategy == "short" ? 2 : 1;
    for (const std::string& object : flat_json_objects(positions_json)) {
        if (json_field(object, "symbol") != run.symbol) continue;
        if (json_int_field(object, "positionType", 0) != expected_type) continue;
        double hold_vol = json_double_field(object, "holdVol", 0.0);
        int state = json_int_field(object, "state", 1);
        if (hold_vol > 0 && state != 3) return object;
    }
    return "";
}

std::string close_trigger_body(const ActiveRun& run, double trigger_price, int trigger_type, double hold_vol, const ContractSpec& spec) {
    bool is_long = run.strategy != "short";
    int close_side = is_long ? 4 : 2;
    std::ostringstream body;
    body << "{";
    body << "\"symbol\":\"" << run.symbol << "\",";
    body << "\"price\":0,";
    body << "\"vol\":" << format_number(hold_vol, spec.vol_scale) << ",";
    body << "\"side\":" << close_side << ",";
    body << "\"openType\":" << run.open_type << ",";
    body << "\"triggerPrice\":" << format_number(trigger_price, spec.price_scale) << ",";
    body << "\"triggerType\":" << trigger_type << ",";
    body << "\"executeCycle\":1,";
    body << "\"orderType\":5,";
    body << "\"trend\":2";
    body << "}";
    return body.str();
}

void sync_position_protection(const MexcConnection& conn, ActiveRun& run, double avg_price, double hold_vol, long long position_id) {
    if (avg_price <= 0 || hold_vol <= 0) return;
    ContractSpec spec = contract_spec(run.symbol);
    if (!spec.exists) {
        append_bot_log("manager " + run.run_id + " cannot sync protection: contract not found " + run.symbol);
        return;
    }

    bool is_long = run.strategy != "short";
    double tp = run.close_enabled ? (is_long ? avg_price * (1 + run.take_profit_percent / 100.0) : avg_price * (1 - run.take_profit_percent / 100.0)) : 0.0;
    double sl = run.stop_enabled ? (is_long ? avg_price * (1 - run.stop_loss_percent / 100.0) : avg_price * (1 + run.stop_loss_percent / 100.0)) : 0.0;
    double tick = std::pow(10.0, -std::max(0, spec.price_scale));
    bool changed = std::fabs(avg_price - run.last_avg_price) >= tick ||
                   std::fabs(hold_vol - run.last_hold_vol) > 1e-9 ||
                   std::fabs(tp - run.last_take_profit_price) >= tick ||
                   std::fabs(sl - run.last_stop_loss_price) >= tick;
    if (!changed) return;

    std::string cancel_body = "{\"symbol\":\"" + run.symbol + "\"}";
    std::string cancel_stop = mexc_post_private(conn, "/api/v1/private/stoporder/cancel_all", cancel_body);
    std::string cancel_plan = mexc_post_private(conn, "/api/v1/private/planorder/cancel_all", cancel_body);
    append_bot_log("manager " + run.run_id + " cancel protection " + run.symbol + " stop=" + cancel_stop.substr(0, 300) + " plan=" + cancel_plan.substr(0, 300));

    if (tp > 0) {
        int trigger_type = is_long ? 1 : 2;
        std::string body = close_trigger_body(run, tp, trigger_type, hold_vol, spec);
        std::string resp = mexc_post_private(conn, "/api/v1/private/planorder/place/v2", body);
        append_bot_log("manager " + run.run_id + " tp " + body + " => " + resp.substr(0, 500));
    }
    if (sl > 0) {
        int trigger_type = is_long ? 2 : 1;
        std::string body = close_trigger_body(run, sl, trigger_type, hold_vol, spec);
        std::string resp = mexc_post_private(conn, "/api/v1/private/planorder/place/v2", body);
        append_bot_log("manager " + run.run_id + " sl " + body + " => " + resp.substr(0, 500));
    }

    run.last_avg_price = avg_price;
    run.last_hold_vol = hold_vol;
    run.last_take_profit_price = tp;
    run.last_stop_loss_price = sl;
    run.position_id = position_id;
    run.updated_at = now_ms();
}

void position_manager_once() {
    MexcConnection conn = load_connection();
    if (conn.api_key.empty() || conn.api_secret.empty()) return;

    std::vector<ActiveRun> runs;
    {
        std::lock_guard<std::mutex> lock(g_active_runs_mutex);
        runs = load_active_runs_unlocked();
    }
    bool has_active = false;
    for (const auto& run : runs) {
        if (run.status == "active") {
            has_active = true;
            break;
        }
    }
    if (!has_active) return;

    std::string positions = mexc_get_private(conn, "/api/v1/private/position/open_positions");
    long long now = now_ms();
    bool changed = false;
    for (auto& run : runs) {
        if (run.status != "active") continue;
        std::string object = position_object_for_run(positions, run);
        if (object.empty()) {
            if (run.last_hold_vol > 0 || now - run.updated_at > 60000) {
                run.status = "closed";
                run.updated_at = now;
                changed = true;
                append_bot_log("manager " + run.run_id + " marked closed: no open position " + run.symbol + " " + run.strategy);
            }
            continue;
        }
        double avg = json_double_field(object, "holdAvgPrice", 0.0);
        if (avg <= 0) avg = json_double_field(object, "openAvgPrice", 0.0);
        double hold_vol = json_double_field(object, "holdVol", 0.0);
        long long position_id = std::atoll(json_field(object, "positionId").c_str());
        double before_avg = run.last_avg_price;
        double before_vol = run.last_hold_vol;
        sync_position_protection(conn, run, avg, hold_vol, position_id);
        if (std::fabs(before_avg - run.last_avg_price) > 1e-12 || std::fabs(before_vol - run.last_hold_vol) > 1e-12) changed = true;
    }

    if (changed) {
        std::lock_guard<std::mutex> lock(g_active_runs_mutex);
        save_active_runs_unlocked(runs);
    }
}

void position_manager_loop() {
    int interval = std::max(2, std::atoi(getenv_or("M1_POSITION_MANAGER_INTERVAL_SEC", "5").c_str()));
    while (true) {
        try {
            position_manager_once();
        } catch (const std::exception& ex) {
            append_bot_log(std::string("manager exception: ") + ex.what());
        } catch (...) {
            append_bot_log("manager unknown exception");
        }
        std::this_thread::sleep_for(std::chrono::seconds(interval));
    }
}

void sync_position_protection_soon() {
    for (int attempt = 0; attempt < 6; ++attempt) {
        try {
            position_manager_once();
        } catch (const std::exception& ex) {
            append_bot_log(std::string("immediate protection exception: ") + ex.what());
        } catch (...) {
            append_bot_log("immediate protection unknown exception");
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(600));
    }
}

std::string mexc_order_body(const PlannedOrder& order, const ContractSpec& spec) {
    std::ostringstream body;
    body << "{";
    body << "\"symbol\":\"" << order.symbol << "\",";
    body << "\"price\":" << (order.type == 5 ? "0" : format_number(order.price, spec.price_scale)) << ",";
    body << "\"vol\":" << format_number(order.vol, spec.vol_scale) << ",";
    body << "\"side\":" << order.side << ",";
    body << "\"type\":" << order.type << ",";
    body << "\"openType\":" << order.open_type << ",";
    body << "\"leverage\":" << order.leverage;
    body << "}";
    return body.str();
}

void append_bot_log(const std::string& text) {
    ensure_data_dir();
    std::ofstream out(data_dir() + "/bot_events.log", std::ios::app);
    out << "[" << now_ms() << "] " << text << "\n";
}

bool mexc_response_success(const std::string& response) {
    if (response.find("<HTML") != std::string::npos || response.find("<html") != std::string::npos) return false;
    if (json_field(response, "success") == "true") return true;
    if (response.find("\"code\"") != std::string::npos && json_int_field(response, "code", -999999) == 0) return true;
    return false;
}

bool mexc_order_endpoint_blocked(const std::string& response) {
    return response.find("Access Denied") != std::string::npos
        || response.find("order/submit") != std::string::npos
        || response.find("order/create") != std::string::npos
        || response.find("Under maintenance") != std::string::npos;
}

std::string render_plan_table(const BotPlan& plan) {
    std::ostringstream body;
    if (!plan.errors.empty()) {
        body << "<div class=\"flash warn\"><strong>Ошибки плана:</strong><br>";
        for (const auto& err : plan.errors) body << html_escape(err) << "<br>";
        body << "</div>";
    }
    if (!plan.warnings.empty()) {
        body << "<div class=\"flash warn\"><strong>Предупреждения:</strong><br>";
        for (const auto& warn : plan.warnings) body << html_escape(warn) << "<br>";
        body << "</div>";
    }
    body << "<div class=\"scroll\"><table><thead><tr><th>Ордер</th><th>Пара</th><th>Side</th><th>Type</th><th>Цена</th><th>Vol</th><th>Маржа</th><th>TP</th><th>SL</th></tr></thead><tbody>";
    for (const auto& order : plan.orders) {
        body << "<tr><td>" << html_escape(order.label) << "</td><td>" << html_escape(order.symbol) << "</td><td>" << order.side << "</td><td>" << order.type << "</td>";
        body << "<td>" << (order.type == 5 ? "Market" : format_number(order.price, plan.spec.price_scale)) << "</td>";
        body << "<td>" << format_number(order.vol, plan.spec.vol_scale) << "</td><td>" << format_number(order.margin_usdt, 2) << " USDT</td>";
        body << "<td>" << (order.take_profit_price > 0 ? format_number(order.take_profit_price, plan.spec.price_scale) : "—") << "</td>";
        body << "<td>" << (order.stop_loss_price > 0 ? format_number(order.stop_loss_price, plan.spec.price_scale) : "—") << "</td></tr>";
    }
    body << "</tbody></table></div>";
    return body.str();
}

std::string execute_plan(const BotPlan& plan, bool live) {
    if (!plan.errors.empty()) return "{\"ok\":false,\"error\":\"plan validation failed\"}";
    MexcConnection conn = load_connection();
    if (live && (conn.api_key.empty() || conn.api_secret.empty())) {
        return "{\"ok\":false,\"error\":\"MEXC API key/secret not configured\"}";
    }
    std::ostringstream orders_json;
    bool all_ok = true;
    bool first_order_ok = !live;
    bool order_endpoint_blocked = false;
    bool first = true;
    for (const auto& order : plan.orders) {
        std::string body = mexc_order_body(order, plan.spec);
        std::string response = live ? mexc_post_private(conn, "/api/v1/private/order/create", body) : "{\"dryRun\":true}";
        append_bot_log(order.label + " " + body + " => " + response.substr(0, 800));
        bool order_ok = !live || mexc_response_success(response);
        if (live && !order_ok && mexc_order_endpoint_blocked(response)) order_endpoint_blocked = true;
        if (order.label == "first market") first_order_ok = order_ok;
        if (!order_ok) all_ok = false;
        if (!first) orders_json << ",";
        first = false;
        orders_json << "{\"label\":\"" << json_escape(order.label) << "\",\"ok\":" << (order_ok ? "true" : "false") << ",\"request\":" << body << ",\"response\":\"" << json_escape(response.substr(0, 800)) << "\"}";
        if (live && order.label == "first market" && !order_ok) break;
    }
    if (live && first_order_ok) {
        register_active_run(plan);
        sync_position_protection_soon();
    }
    std::ostringstream result;
    result << "{\"ok\":" << (all_ok ? "true" : "false") << ",\"live\":" << (live ? "true" : "false");
    if (!all_ok && order_endpoint_blocked) {
        result << ",\"error\":\"MEXC private order endpoint returned Access Denied; order create appears unavailable from this server/API path\"";
    } else if (!all_ok) {
        result << ",\"error\":\"one or more MEXC requests failed\"";
    }
    result << ",\"orders\":[" << orders_json.str() << "]}";
    return result.str();
}

std::string execute_saved_bot(int bot_id, bool live) {
    std::vector<BotConfig> bots = load_bots();
    BotConfig* bot = find_bot(bots, bot_id);
    if (!bot) return "{\"ok\":false,\"error\":\"bot not found\"}";
    std::string payload = bot_to_json(*bot);
    BotPlan plan = build_bot_plan(parse_bot_config(payload));
    plan.config.id = bot->id;
    plan.config.name = bot->name;
    return execute_plan(plan, live);
}

