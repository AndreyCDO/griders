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

