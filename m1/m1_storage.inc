long long now_ms() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count();
}

std::string data_dir() {
    return getenv_or("M1_DATA_DIR", "./m1_data");
}

std::string connection_file() {
    return data_dir() + "/connection.tsv";
}

std::string bots_file() {
    return data_dir() + "/bots.tsv";
}

std::string active_runs_file() {
    return data_dir() + "/active_runs.tsv";
}

std::string deals_file() {
    return data_dir() + "/deals.tsv";
}

std::string deal_orders_file() {
    return data_dir() + "/deal_orders.tsv";
}

void ensure_data_dir() {
    std::string dir = data_dir();
    mkdir(dir.c_str(), 0700);
}

MexcConnection load_connection() {
    std::ifstream in(connection_file());
    MexcConnection conn;
    std::string line;
    if (!std::getline(in, line)) return conn;
    std::vector<std::string> parts;
    size_t start = 0;
    while (true) {
        size_t tab = line.find('\t', start);
        parts.push_back(line.substr(start, tab == std::string::npos ? std::string::npos : tab - start));
        if (tab == std::string::npos) break;
        start = tab + 1;
    }
    if (parts.size() > 0) conn.label = url_decode(parts[0]);
    if (parts.size() > 1) conn.api_key = url_decode(parts[1]);
    if (parts.size() > 2) conn.api_secret = url_decode(parts[2]);
    return conn;
}

void save_connection(const MexcConnection& conn) {
    ensure_data_dir();
    std::ofstream out(connection_file(), std::ios::trunc);
    out << url_encode(conn.label) << '\t' << url_encode(conn.api_key) << '\t' << url_encode(conn.api_secret) << '\n';
    out.close();
    chmod(connection_file().c_str(), 0600);
}

std::string bot_to_line(const BotConfig& bot) {
    std::ostringstream out;
    out << bot.id << '\t'
        << url_encode(bot.name) << '\t'
        << url_encode(bot.strategy) << '\t'
        << url_encode(bot.pair) << '\t'
        << bot.leverage << '\t'
        << url_encode(bot.margin_type) << '\t'
        << bot.order_volume << '\t'
        << (bot.continuous ? 1 : 0) << '\t'
        << bot.cycles << '\t'
        << bot.dca_volume << '\t'
        << bot.dca_percent << '\t'
        << bot.dca_active << '\t'
        << bot.dca_multiplier_volume << '\t'
        << bot.dca_multiplier_price << '\t'
        << (bot.close_enabled ? 1 : 0) << '\t'
        << bot.take_profit_percent << '\t'
        << (bot.stop_enabled ? 1 : 0) << '\t'
        << bot.stop_loss_percent;
    return out.str();
}

BotConfig bot_from_line(const std::string& line) {
    std::vector<std::string> parts;
    size_t start = 0;
    while (true) {
        size_t tab = line.find('\t', start);
        parts.push_back(line.substr(start, tab == std::string::npos ? std::string::npos : tab - start));
        if (tab == std::string::npos) break;
        start = tab + 1;
    }
    BotConfig bot;
    if (parts.size() > 0) bot.id = std::atoi(parts[0].c_str());
    if (parts.size() > 1) bot.name = url_decode(parts[1]);
    if (parts.size() > 2) bot.strategy = url_decode(parts[2]);
    if (parts.size() > 3) bot.pair = url_decode(parts[3]);
    if (parts.size() > 4) bot.leverage = std::atoi(parts[4].c_str());
    if (parts.size() > 5) bot.margin_type = url_decode(parts[5]);
    if (parts.size() > 6) bot.order_volume = std::atof(parts[6].c_str());
    if (parts.size() > 7) bot.continuous = parts[7] == "1";
    if (parts.size() > 8) bot.cycles = std::atoi(parts[8].c_str());
    if (parts.size() > 9) bot.dca_volume = std::atof(parts[9].c_str());
    if (parts.size() > 10) bot.dca_percent = std::atof(parts[10].c_str());
    if (parts.size() > 11) bot.dca_active = std::atoi(parts[11].c_str());
    if (parts.size() > 12) bot.dca_multiplier_volume = std::atof(parts[12].c_str());
    if (parts.size() > 13) bot.dca_multiplier_price = std::atof(parts[13].c_str());
    if (parts.size() > 14) bot.close_enabled = parts[14] == "1";
    if (parts.size() > 15) bot.take_profit_percent = std::atof(parts[15].c_str());
    if (parts.size() > 16) bot.stop_enabled = parts[16] == "1";
    if (parts.size() > 17) bot.stop_loss_percent = std::atof(parts[17].c_str());
    if (bot.leverage < 1) bot.leverage = 1;
    if (bot.leverage > 100) bot.leverage = 100;
    if (bot.dca_active < 1) bot.dca_active = 1;
    if (bot.dca_active > 20) bot.dca_active = 20;
    if (bot.cycles < 1) bot.cycles = 1;
    return bot;
}

std::vector<BotConfig> load_bots() {
    std::vector<BotConfig> bots;
    std::ifstream in(bots_file());
    std::string line;
    while (std::getline(in, line)) {
        if (!trim(line).empty()) bots.push_back(bot_from_line(line));
    }
    return bots;
}

void save_bots(const std::vector<BotConfig>& bots) {
    ensure_data_dir();
    std::ofstream out(bots_file(), std::ios::trunc);
    for (const auto& bot : bots) out << bot_to_line(bot) << "\n";
    out.close();
    chmod(bots_file().c_str(), 0600);
}

std::string active_run_to_line(const ActiveRun& run) {
    std::ostringstream out;
    out << run.bot_id << '\t'
        << url_encode(run.run_id) << '\t'
        << url_encode(run.symbol) << '\t'
        << url_encode(run.strategy) << '\t'
        << run.open_type << '\t'
        << run.leverage << '\t'
        << (run.close_enabled ? 1 : 0) << '\t'
        << run.take_profit_percent << '\t'
        << (run.stop_enabled ? 1 : 0) << '\t'
        << run.stop_loss_percent << '\t'
        << run.last_avg_price << '\t'
        << run.last_hold_vol << '\t'
        << run.last_take_profit_price << '\t'
        << run.last_stop_loss_price << '\t'
        << run.position_id << '\t'
        << url_encode(run.status) << '\t'
        << run.updated_at;
    return out.str();
}

ActiveRun active_run_from_line(const std::string& line) {
    std::vector<std::string> parts;
    size_t start = 0;
    while (true) {
        size_t tab = line.find('\t', start);
        parts.push_back(line.substr(start, tab == std::string::npos ? std::string::npos : tab - start));
        if (tab == std::string::npos) break;
        start = tab + 1;
    }
    ActiveRun run;
    if (parts.size() > 0) run.bot_id = std::atoi(parts[0].c_str());
    if (parts.size() > 1) run.run_id = url_decode(parts[1]);
    if (parts.size() > 2) run.symbol = url_decode(parts[2]);
    if (parts.size() > 3) run.strategy = url_decode(parts[3]);
    if (parts.size() > 4) run.open_type = std::atoi(parts[4].c_str());
    if (parts.size() > 5) run.leverage = std::atoi(parts[5].c_str());
    if (parts.size() > 6) run.close_enabled = parts[6] == "1";
    if (parts.size() > 7) run.take_profit_percent = std::atof(parts[7].c_str());
    if (parts.size() > 8) run.stop_enabled = parts[8] == "1";
    if (parts.size() > 9) run.stop_loss_percent = std::atof(parts[9].c_str());
    if (parts.size() > 10) run.last_avg_price = std::atof(parts[10].c_str());
    if (parts.size() > 11) run.last_hold_vol = std::atof(parts[11].c_str());
    if (parts.size() > 12) run.last_take_profit_price = std::atof(parts[12].c_str());
    if (parts.size() > 13) run.last_stop_loss_price = std::atof(parts[13].c_str());
    if (parts.size() > 14) run.position_id = std::atoll(parts[14].c_str());
    if (parts.size() > 15) run.status = url_decode(parts[15]);
    if (parts.size() > 16) run.updated_at = std::atoll(parts[16].c_str());
    if (run.run_id.empty()) run.run_id = "m1-" + std::to_string(run.bot_id) + "-" + std::to_string(now_ms());
    if (run.updated_at <= 0) run.updated_at = now_ms();
    return run;
}

std::vector<ActiveRun> load_active_runs_unlocked() {
    std::vector<ActiveRun> runs;
    std::ifstream in(active_runs_file());
    std::string line;
    while (std::getline(in, line)) {
        if (!trim(line).empty()) runs.push_back(active_run_from_line(line));
    }
    return runs;
}

void save_active_runs_unlocked(const std::vector<ActiveRun>& runs) {
    ensure_data_dir();
    std::ofstream out(active_runs_file(), std::ios::trunc);
    for (const auto& run : runs) out << active_run_to_line(run) << "\n";
    out.close();
    chmod(active_runs_file().c_str(), 0600);
}

std::string deal_to_line(const Deal& deal) {
    std::ostringstream out;
    out << url_encode(deal.deal_id) << '\t'
        << deal.bot_id << '\t'
        << url_encode(deal.bot_name) << '\t'
        << url_encode(deal.symbol) << '\t'
        << url_encode(deal.strategy) << '\t'
        << (deal.live ? 1 : 0) << '\t'
        << url_encode(deal.status) << '\t'
        << deal.open_type << '\t'
        << deal.leverage << '\t'
        << deal.planned_margin_usdt << '\t'
        << deal.planned_volume << '\t'
        << deal.mark_price << '\t'
        << url_encode(deal.config_json) << '\t'
        << deal.created_at << '\t'
        << deal.updated_at;
    return out.str();
}

Deal deal_from_line(const std::string& line) {
    std::vector<std::string> parts;
    size_t start = 0;
    while (true) {
        size_t tab = line.find('\t', start);
        parts.push_back(line.substr(start, tab == std::string::npos ? std::string::npos : tab - start));
        if (tab == std::string::npos) break;
        start = tab + 1;
    }
    Deal deal;
    if (parts.size() > 0) deal.deal_id = url_decode(parts[0]);
    if (parts.size() > 1) deal.bot_id = std::atoi(parts[1].c_str());
    if (parts.size() > 2) deal.bot_name = url_decode(parts[2]);
    if (parts.size() > 3) deal.symbol = url_decode(parts[3]);
    if (parts.size() > 4) deal.strategy = url_decode(parts[4]);
    if (parts.size() > 5) deal.live = parts[5] == "1";
    if (parts.size() > 6) deal.status = url_decode(parts[6]);
    if (parts.size() > 7) deal.open_type = std::atoi(parts[7].c_str());
    if (parts.size() > 8) deal.leverage = std::atoi(parts[8].c_str());
    if (parts.size() > 9) deal.planned_margin_usdt = std::atof(parts[9].c_str());
    if (parts.size() > 10) deal.planned_volume = std::atof(parts[10].c_str());
    if (parts.size() > 11) deal.mark_price = std::atof(parts[11].c_str());
    if (parts.size() > 12) deal.config_json = url_decode(parts[12]);
    if (parts.size() > 13) deal.created_at = std::atoll(parts[13].c_str());
    if (parts.size() > 14) deal.updated_at = std::atoll(parts[14].c_str());
    return deal;
}

std::vector<Deal> load_deals() {
    std::vector<Deal> deals;
    std::ifstream in(deals_file());
    std::string line;
    while (std::getline(in, line)) {
        if (!trim(line).empty()) deals.push_back(deal_from_line(line));
    }
    return deals;
}

void save_deals(const std::vector<Deal>& deals) {
    ensure_data_dir();
    std::ofstream out(deals_file(), std::ios::trunc);
    for (const auto& deal : deals) out << deal_to_line(deal) << "\n";
    out.close();
    chmod(deals_file().c_str(), 0600);
}

std::string deal_order_to_line(const DealOrder& order) {
    std::ostringstream out;
    out << url_encode(order.deal_id) << '\t'
        << url_encode(order.label) << '\t'
        << url_encode(order.symbol) << '\t'
        << order.side << '\t'
        << order.type << '\t'
        << order.open_type << '\t'
        << order.leverage << '\t'
        << order.price << '\t'
        << order.vol << '\t'
        << order.margin_usdt << '\t'
        << order.take_profit_price << '\t'
        << order.stop_loss_price << '\t'
        << url_encode(order.status) << '\t'
        << url_encode(order.exchange_order_id) << '\t'
        << url_encode(order.request_json) << '\t'
        << url_encode(order.response_json) << '\t'
        << order.created_at << '\t'
        << order.updated_at << '\t'
        << order.exchange_state << '\t'
        << order.deal_avg_price << '\t'
        << order.deal_vol << '\t'
        << order.fee << '\t'
        << order.profit << '\t'
        << url_encode(order.fee_currency) << '\t'
        << url_encode(order.exchange_json);
    return out.str();
}

DealOrder deal_order_from_line(const std::string& line) {
    std::vector<std::string> parts;
    size_t start = 0;
    while (true) {
        size_t tab = line.find('\t', start);
        parts.push_back(line.substr(start, tab == std::string::npos ? std::string::npos : tab - start));
        if (tab == std::string::npos) break;
        start = tab + 1;
    }
    DealOrder order;
    if (parts.size() > 0) order.deal_id = url_decode(parts[0]);
    if (parts.size() > 1) order.label = url_decode(parts[1]);
    if (parts.size() > 2) order.symbol = url_decode(parts[2]);
    if (parts.size() > 3) order.side = std::atoi(parts[3].c_str());
    if (parts.size() > 4) order.type = std::atoi(parts[4].c_str());
    if (parts.size() > 5) order.open_type = std::atoi(parts[5].c_str());
    if (parts.size() > 6) order.leverage = std::atoi(parts[6].c_str());
    if (parts.size() > 7) order.price = std::atof(parts[7].c_str());
    if (parts.size() > 8) order.vol = std::atof(parts[8].c_str());
    if (parts.size() > 9) order.margin_usdt = std::atof(parts[9].c_str());
    if (parts.size() > 10) order.take_profit_price = std::atof(parts[10].c_str());
    if (parts.size() > 11) order.stop_loss_price = std::atof(parts[11].c_str());
    if (parts.size() > 12) order.status = url_decode(parts[12]);
    if (parts.size() > 13) order.exchange_order_id = url_decode(parts[13]);
    if (parts.size() > 14) order.request_json = url_decode(parts[14]);
    if (parts.size() > 15) order.response_json = url_decode(parts[15]);
    if (parts.size() > 16) order.created_at = std::atoll(parts[16].c_str());
    if (parts.size() > 17) order.updated_at = std::atoll(parts[17].c_str());
    if (parts.size() > 18) order.exchange_state = std::atoi(parts[18].c_str());
    if (parts.size() > 19) order.deal_avg_price = std::atof(parts[19].c_str());
    if (parts.size() > 20) order.deal_vol = std::atof(parts[20].c_str());
    if (parts.size() > 21) order.fee = std::atof(parts[21].c_str());
    if (parts.size() > 22) order.profit = std::atof(parts[22].c_str());
    if (parts.size() > 23) order.fee_currency = url_decode(parts[23]);
    if (parts.size() > 24) order.exchange_json = url_decode(parts[24]);
    return order;
}

std::vector<DealOrder> load_deal_orders() {
    std::vector<DealOrder> orders;
    std::ifstream in(deal_orders_file());
    std::string line;
    while (std::getline(in, line)) {
        if (!trim(line).empty()) orders.push_back(deal_order_from_line(line));
    }
    return orders;
}

void save_deal_orders(const std::vector<DealOrder>& orders) {
    ensure_data_dir();
    std::ofstream out(deal_orders_file(), std::ios::trunc);
    for (const auto& order : orders) out << deal_order_to_line(order) << "\n";
    out.close();
    chmod(deal_orders_file().c_str(), 0600);
}

std::string exchange_order_id_from_response(const std::string& response) {
    std::string data = json_object_field(response, "data");
    std::string id = data.empty() ? "" : json_field(data, "orderId");
    if (!id.empty()) return id;
    id = json_field(response, "orderId");
    if (!id.empty()) return id;
    std::string value = json_field(response, "data");
    return value;
}

Deal create_deal_from_plan(const BotPlan& plan, bool live) {
    long long created = now_ms();
    Deal deal;
    deal.deal_id = "deal-" + std::to_string(plan.config.id) + "-" + std::to_string(created);
    deal.bot_id = plan.config.id;
    deal.bot_name = plan.config.name;
    deal.symbol = normalize_symbol(plan.config.pair);
    deal.strategy = plan.config.strategy;
    std::transform(deal.strategy.begin(), deal.strategy.end(), deal.strategy.begin(), [](unsigned char c) { return std::tolower(c); });
    deal.live = live;
    deal.status = live ? "opening" : "dry_run";
    if (!plan.orders.empty()) {
        deal.open_type = plan.orders.front().open_type;
        deal.leverage = plan.orders.front().leverage;
    } else {
        deal.leverage = plan.config.leverage;
    }
    for (const auto& order : plan.orders) {
        deal.planned_margin_usdt += order.margin_usdt;
        deal.planned_volume += order.vol;
    }
    deal.mark_price = plan.mark_price;
    deal.config_json = bot_to_json(plan.config);
    deal.created_at = created;
    deal.updated_at = created;

    std::vector<Deal> deals = load_deals();
    deals.push_back(deal);
    save_deals(deals);
    return deal;
}

void upsert_deal(const Deal& deal) {
    std::vector<Deal> deals = load_deals();
    bool replaced = false;
    for (auto& item : deals) {
        if (item.deal_id == deal.deal_id) {
            item = deal;
            replaced = true;
            break;
        }
    }
    if (!replaced) deals.push_back(deal);
    save_deals(deals);
}

bool update_deal_status(const std::string& deal_id, const std::string& status) {
    std::vector<Deal> deals = load_deals();
    bool changed = false;
    for (auto& deal : deals) {
        if (deal.deal_id == deal_id) {
            deal.status = status;
            deal.updated_at = now_ms();
            changed = true;
            break;
        }
    }
    if (changed) save_deals(deals);
    return changed;
}

void append_deal_order(const Deal& deal, const PlannedOrder& planned, const std::string& request, const std::string& response, bool ok) {
    long long created = now_ms();
    DealOrder order;
    order.deal_id = deal.deal_id;
    order.label = planned.label;
    order.symbol = planned.symbol;
    order.side = planned.side;
    order.type = planned.type;
    order.open_type = planned.open_type;
    order.leverage = planned.leverage;
    order.price = planned.price;
    order.vol = planned.vol;
    order.margin_usdt = planned.margin_usdt;
    order.take_profit_price = planned.take_profit_price;
    order.stop_loss_price = planned.stop_loss_price;
    order.status = ok ? (deal.live ? "placed" : "dry_run") : "failed";
    order.exchange_order_id = exchange_order_id_from_response(response);
    order.request_json = request;
    order.response_json = response.substr(0, 1200);
    order.created_at = created;
    order.updated_at = created;

    std::vector<DealOrder> orders = load_deal_orders();
    orders.push_back(order);
    save_deal_orders(orders);
}

void register_active_run(const BotPlan& plan) {
    if (!plan.errors.empty() || plan.orders.empty()) return;
    ActiveRun run;
    run.bot_id = plan.config.id;
    run.run_id = "m1-" + std::to_string(plan.config.id) + "-" + std::to_string(now_ms());
    run.symbol = normalize_symbol(plan.config.pair);
    run.strategy = plan.config.strategy;
    std::transform(run.strategy.begin(), run.strategy.end(), run.strategy.begin(), [](unsigned char c) { return std::tolower(c); });
    run.open_type = plan.orders.front().open_type;
    run.leverage = plan.config.leverage;
    run.close_enabled = plan.config.close_enabled;
    run.take_profit_percent = plan.config.take_profit_percent;
    run.stop_enabled = plan.config.stop_enabled;
    run.stop_loss_percent = plan.config.stop_loss_percent;
    run.status = "active";
    run.updated_at = now_ms();

    std::lock_guard<std::mutex> lock(g_active_runs_mutex);
    std::vector<ActiveRun> runs = load_active_runs_unlocked();
    bool replaced = false;
    for (auto& item : runs) {
        if (item.symbol == run.symbol && item.strategy == run.strategy && item.status == "active") {
            item = run;
            replaced = true;
            break;
        }
    }
    if (!replaced) runs.push_back(run);
    save_active_runs_unlocked(runs);
}

int next_bot_id(const std::vector<BotConfig>& bots) {
    int value = 1;
    for (const auto& bot : bots) value = std::max(value, bot.id + 1);
    return value;
}

BotConfig* find_bot(std::vector<BotConfig>& bots, int id) {
    for (auto& bot : bots) {
        if (bot.id == id) return &bot;
    }
    return nullptr;
}

std::string bot_to_json(const BotConfig& bot) {
    std::ostringstream json;
    json << "{\n";
    json << "  \"action\": \"open\",\n";
    json << "  \"params\": {\n";
    json << "    \"strategy\": \"" << json_escape(bot.strategy) << "\",\n";
    json << "    \"pairs\": [\"" << json_escape(compact_symbol(normalize_symbol(bot.pair))) << "\"],\n";
    json << "    \"open\": {\"orderVolume\": \"" << format_number(bot.order_volume, 2) << "\", \"leverage\": " << bot.leverage << ", \"marginType\": \"" << json_escape(bot.margin_type) << "\", \"orderType\": \"Market\", \"cycles\": " << bot.cycles << ", \"continuous\": " << (bot.continuous ? "true" : "false") << "},\n";
    json << "    \"dca\": {\"enabled\": true, \"max\": " << bot.dca_active << ", \"active\": " << bot.dca_active << ", \"volume\": \"" << format_number(bot.dca_volume, 2) << "\", \"percent\": \"" << format_number(bot.dca_percent, 3) << "\", \"multiplierVolume\": \"" << format_number(bot.dca_multiplier_volume, 3) << "\", \"multiplierPrice\": \"" << format_number(bot.dca_multiplier_price, 3) << "\"},\n";
    json << "    \"close\": {\"enabled\": " << (bot.close_enabled ? "true" : "false") << ", \"event\": \"percentage\", \"value\": \"" << format_number(bot.take_profit_percent, 3) << "\"},\n";
    json << "    \"stop\": {\"enabled\": " << (bot.stop_enabled ? "true" : "false") << ", \"event\": \"percentage\", \"value\": \"" << format_number(bot.stop_loss_percent, 3) << "\"}\n";
    json << "  }\n";
    json << "}";
    return json.str();
}

std::string mask_key(const std::string& key) {
    if (key.empty()) return "не указан";
    if (key.size() <= 8) return key.substr(0, 2) + "..." + key.substr(key.size() - 2);
    return key.substr(0, 4) + "..." + key.substr(key.size() - 4);
}

