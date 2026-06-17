std::string run_curl_config(const std::string& config) {
    char path[] = "/tmp/m1curl_XXXXXX";
    int fd = mkstemp(path);
    if (fd < 0) return "curl temp file error";
    std::string final_config = "user-agent = \"Mozilla/5.0 GridersM1/1.0\"\n";
    final_config += "header = \"Accept-Encoding: identity\"\n";
    final_config += config;
    write(fd, final_config.data(), final_config.size());
    close(fd);
    chmod(path, 0600);

    std::string command = "curl -sS -K ";
    command += path;
    command += " 2>&1";
    FILE* pipe = popen(command.c_str(), "r");
    std::string output;
    if (pipe) {
        char buffer[4096];
        while (fgets(buffer, sizeof(buffer), pipe)) output += buffer;
        pclose(pipe);
    }
    unlink(path);
    return output;
}

std::string run_curl_config_with_body(const std::string& config_prefix, const std::string& body) {
    char body_path[] = "/tmp/m1body_XXXXXX";
    int body_fd = mkstemp(body_path);
    if (body_fd < 0) return "curl body temp file error";
    write(body_fd, body.data(), body.size());
    close(body_fd);
    chmod(body_path, 0600);

    std::string config = config_prefix;
    config += "data-binary = @";
    config += body_path;
    config += "\n";
    std::string output = run_curl_config(config);
    unlink(body_path);
    return output;
}

std::string mexc_get_public(const std::string& path) {
    std::ostringstream cfg;
    cfg << "url = \"" << kMexcBase << path << "\"\n";
    cfg << "request = \"GET\"\n";
    cfg << "connect-timeout = 8\n";
    cfg << "max-time = 20\n";
    return run_curl_config(cfg.str());
}

std::string mexc_post_private(const MexcConnection& conn, const std::string& path, const std::string& body) {
    std::string ts = std::to_string(now_ms());
    std::string signature = hmac_sha256_hex(conn.api_secret, conn.api_key + ts + body);
    std::ostringstream cfg;
    cfg << "url = \"" << kMexcBase << path << "\"\n";
    cfg << "request = \"POST\"\n";
    cfg << "connect-timeout = 8\n";
    cfg << "max-time = 20\n";
    cfg << "header = \"ApiKey: " << conn.api_key << "\"\n";
    cfg << "header = \"Request-Time: " << ts << "\"\n";
    cfg << "header = \"Signature: " << signature << "\"\n";
    cfg << "header = \"Recv-Window: 10000\"\n";
    cfg << "header = \"Content-Type: application/json\"\n";
    return run_curl_config_with_body(cfg.str(), body);
}

std::string mexc_get_private(const MexcConnection& conn, const std::string& path, const std::string& query = "") {
    std::string ts = std::to_string(now_ms());
    std::string signature = hmac_sha256_hex(conn.api_secret, conn.api_key + ts + query);
    std::string url = std::string(kMexcBase) + path + (query.empty() ? "" : "?" + query);
    std::ostringstream cfg;
    cfg << "url = \"" << url << "\"\n";
    cfg << "request = \"GET\"\n";
    cfg << "connect-timeout = 8\n";
    cfg << "max-time = 20\n";
    cfg << "header = \"ApiKey: " << conn.api_key << "\"\n";
    cfg << "header = \"Request-Time: " << ts << "\"\n";
    cfg << "header = \"Signature: " << signature << "\"\n";
    cfg << "header = \"Recv-Window: 10000\"\n";
    cfg << "header = \"Content-Type: application/json\"\n";
    return run_curl_config(cfg.str());
}

std::string extract_json_object_for_symbol(const std::string& json, const std::string& symbol) {
    std::string needle = "\"symbol\":\"" + symbol + "\"";
    size_t pos = json.find(needle);
    if (pos == std::string::npos) {
        needle = "\"symbol\": \"" + symbol + "\"";
        pos = json.find(needle);
    }
    if (pos == std::string::npos) return "";
    size_t begin = json.rfind('{', pos);
    size_t end = json.find('}', pos);
    if (begin == std::string::npos || end == std::string::npos || end <= begin) return "";
    return json.substr(begin, end - begin + 1);
}

std::string json_field(const std::string& object, const std::string& key) {
    std::regex re("\"" + key + "\"\\s*:\\s*(\"([^\"]*)\"|true|false|-?[0-9]+(\\.[0-9]+)?)");
    std::smatch match;
    if (!std::regex_search(object, match, re)) return "";
    std::string raw = match[1].str();
    if (raw.size() >= 2 && raw.front() == '"' && raw.back() == '"') return raw.substr(1, raw.size() - 2);
    return raw;
}

std::string format_fee(const std::string& raw) {
    if (raw.empty()) return "—";
    double value = std::atof(raw.c_str());
    if (value == 0.0) return "0%";
    std::ostringstream out;
    out << std::fixed << std::setprecision(4) << value * 100.0;
    std::string text = out.str();
    while (text.size() > 1 && text.back() == '0') text.pop_back();
    if (!text.empty() && text.back() == '.') text.pop_back();
    return text + "%";
}

std::string normalize_symbol(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) { return std::toupper(c); });
    value.erase(std::remove(value.begin(), value.end(), '-'), value.end());
    value.erase(std::remove(value.begin(), value.end(), '/'), value.end());
    value.erase(std::remove(value.begin(), value.end(), '_'), value.end());
    if (value.size() > 4 && value.substr(value.size() - 4) == "USDT") {
        return value.substr(0, value.size() - 4) + "_USDT";
    }
    return value;
}

std::string compact_symbol(const std::string& symbol) {
    std::string value = symbol;
    value.erase(std::remove(value.begin(), value.end(), '_'), value.end());
    return value;
}

std::string json_object_field(const std::string& object, const std::string& key) {
    std::string needle = "\"" + key + "\"";
    size_t key_pos = object.find(needle);
    if (key_pos == std::string::npos) return "";
    size_t colon = object.find(':', key_pos + needle.size());
    if (colon == std::string::npos) return "";
    size_t begin = object.find_first_not_of(" \r\n\t", colon + 1);
    if (begin == std::string::npos || object[begin] != '{') return "";
    int depth = 0;
    bool in_string = false;
    bool escaped = false;
    for (size_t i = begin; i < object.size(); ++i) {
        char ch = object[i];
        if (in_string) {
            if (escaped) escaped = false;
            else if (ch == '\\') escaped = true;
            else if (ch == '"') in_string = false;
            continue;
        }
        if (ch == '"') in_string = true;
        else if (ch == '{') depth++;
        else if (ch == '}') {
            depth--;
            if (depth == 0) return object.substr(begin, i - begin + 1);
        }
    }
    return "";
}

std::string json_array_field(const std::string& object, const std::string& key) {
    std::string needle = "\"" + key + "\"";
    size_t key_pos = object.find(needle);
    if (key_pos == std::string::npos) return "";
    size_t colon = object.find(':', key_pos + needle.size());
    if (colon == std::string::npos) return "";
    size_t begin = object.find_first_not_of(" \r\n\t", colon + 1);
    if (begin == std::string::npos || object[begin] != '[') return "";
    int depth = 0;
    bool in_string = false;
    bool escaped = false;
    for (size_t i = begin; i < object.size(); ++i) {
        char ch = object[i];
        if (in_string) {
            if (escaped) escaped = false;
            else if (ch == '\\') escaped = true;
            else if (ch == '"') in_string = false;
            continue;
        }
        if (ch == '"') in_string = true;
        else if (ch == '[') depth++;
        else if (ch == ']') {
            depth--;
            if (depth == 0) return object.substr(begin, i - begin + 1);
        }
    }
    return "";
}

std::string first_json_string_in_array(const std::string& array_json) {
    std::regex re("\"([^\"]+)\"");
    std::smatch match;
    if (std::regex_search(array_json, match, re)) return match[1].str();
    return "";
}

double json_double_field(const std::string& object, const std::string& key, double fallback) {
    std::string value = json_field(object, key);
    return value.empty() ? fallback : std::atof(value.c_str());
}

int json_int_field(const std::string& object, const std::string& key, int fallback) {
    std::string value = json_field(object, key);
    return value.empty() ? fallback : std::atoi(value.c_str());
}

bool json_bool_field(const std::string& object, const std::string& key, bool fallback) {
    std::string value = json_field(object, key);
    if (value == "true") return true;
    if (value == "false") return false;
    return fallback;
}

std::string format_number(double value, int scale) {
    if (!std::isfinite(value)) value = 0.0;
    std::ostringstream out;
    out << std::fixed << std::setprecision(std::max(0, scale)) << value;
    std::string text = out.str();
    if (scale > 0) {
        while (text.size() > 1 && text.back() == '0') text.pop_back();
        if (!text.empty() && text.back() == '.') text.pop_back();
    }
    return text;
}

std::string json_escape(const std::string& value) {
    std::string out;
    for (char ch : value) {
        if (ch == '"' || ch == '\\') {
            out += '\\';
            out += ch;
        } else if (ch == '\n') {
            out += "\\n";
        } else {
            out += ch;
        }
    }
    return out;
}

ContractSpec contract_spec(const std::string& symbol) {
    std::string json = mexc_get_public("/api/v1/contract/detail?symbol=" + symbol);
    std::string object = extract_json_object_for_symbol(json, symbol);
    ContractSpec spec;
    spec.symbol = symbol;
    if (object.empty()) return spec;
    spec.exists = true;
    spec.api_allowed = json_field(object, "apiAllowed") == "true";
    spec.contract_size = json_double_field(object, "contractSize", 0.0);
    spec.min_vol = json_double_field(object, "minVol", 1.0);
    spec.max_vol = json_double_field(object, "maxVol", 0.0);
    spec.vol_unit = json_double_field(object, "volUnit", 1.0);
    spec.vol_scale = json_int_field(object, "volScale", 0);
    spec.price_scale = json_int_field(object, "priceScale", 4);
    spec.max_leverage = json_int_field(object, "maxLeverage", 0);
    if (spec.vol_unit <= 0) spec.vol_unit = 1.0;
    return spec;
}

double fair_price(const std::string& symbol) {
    std::string json = mexc_get_public("/api/v1/contract/fair_price/" + symbol);
    double price = json_double_field(json, "fairPrice", 0.0);
    if (price <= 0) price = json_double_field(json, "price", 0.0);
    return price;
}

double round_volume_up(double raw, const ContractSpec& spec) {
    if (raw <= 0 || spec.vol_unit <= 0) return 0.0;
    double steps = std::ceil((raw - 1e-12) / spec.vol_unit);
    double value = steps * spec.vol_unit;
    if (value < spec.min_vol) value = spec.min_vol;
    if (spec.max_vol > 0 && value > spec.max_vol) value = spec.max_vol;
    return value;
}

double contracts_from_margin(double margin_usdt, int leverage, double price, const ContractSpec& spec) {
    if (margin_usdt <= 0 || leverage <= 0 || price <= 0 || spec.contract_size <= 0) return 0.0;
    double notional = margin_usdt * leverage;
    return round_volume_up(notional / (price * spec.contract_size), spec);
}

std::vector<MarketRow> market_rows() {
    static std::string cached_json;
    static long long cached_at = 0;
    long long now = now_ms();
    if (cached_json.empty() || now - cached_at > 120000) {
        cached_json = mexc_get_public("/api/v1/contract/detail");
        cached_at = now;
    }
    std::vector<MarketRow> rows;
    for (const std::string& symbol : kTrackedPairs) {
        MarketRow row;
        row.symbol = symbol;
        std::string object = extract_json_object_for_symbol(cached_json, symbol);
        if (!object.empty()) {
            row.exists = true;
            row.api_allowed = json_field(object, "apiAllowed") == "true";
            row.state = json_field(object, "state") == "0" ? "торгуется" : "state " + json_field(object, "state");
            row.max_leverage = json_field(object, "maxLeverage");
            row.min_vol = json_field(object, "minVol");
            std::string maker = json_field(object, "makerFeeRate");
            std::string taker = json_field(object, "takerFeeRate");
            row.zero_maker = std::atof(maker.c_str()) == 0.0;
            row.zero_taker = std::atof(taker.c_str()) == 0.0;
            row.maker_fee = format_fee(maker);
            row.taker_fee = format_fee(taker);
        }
        rows.push_back(row);
    }
    return rows;
}

std::vector<MarketRow> personal_zero_fee_rows(const MexcConnection& conn) {
    std::vector<MarketRow> rows;
    if (conn.api_key.empty() || conn.api_secret.empty()) return rows;
    for (const std::string& symbol : kPersonalZeroFeeCandidates) {
        std::string response = mexc_get_private(conn, "/api/v1/private/account/tiered_fee_rate/v2", "symbol=" + symbol);
        std::string data = json_object_field(response, "data");
        if (data.empty()) continue;
        std::string maker = json_field(data, "realMakerFee");
        std::string taker = json_field(data, "realTakerFee");
        bool zero_maker = !maker.empty() && std::atof(maker.c_str()) == 0.0;
        bool zero_taker = !taker.empty() && std::atof(taker.c_str()) == 0.0;
        if (!zero_maker || !zero_taker) continue;

        MarketRow row;
        row.symbol = symbol;
        row.exists = true;
        row.zero_maker = true;
        row.zero_taker = true;
        row.maker_fee = format_fee(maker);
        row.taker_fee = format_fee(taker);
        row.state = json_field(data, "feeRateType");
        if (row.state.empty()) row.state = json_field(data, "feeRateMode");
        if (row.state.empty()) row.state = "personal";
        row.max_leverage = json_field(data, "maxLeverage");
        ContractSpec spec = contract_spec(symbol);
        row.api_allowed = !spec.exists || spec.api_allowed;
        row.min_vol = spec.exists ? format_number(spec.min_vol, spec.vol_scale) : "—";
        rows.push_back(row);
    }
    return rows;
}

std::vector<std::string> all_contract_symbols() {
    static std::vector<std::string> cached;
    static long long cached_at = 0;
    long long now = now_ms();
    if (!cached.empty() && now - cached_at < 300000) return cached;
    std::string json = mexc_get_public("/api/v1/contract/detail");
    std::vector<std::string> symbols;
    std::regex re("\"symbol\"\\s*:\\s*\"([A-Z0-9_]+)\"");
    auto begin = std::sregex_iterator(json.begin(), json.end(), re);
    auto end = std::sregex_iterator();
    for (auto it = begin; it != end; ++it) {
        std::string symbol = (*it)[1].str();
        if (symbol.size() > 5 && symbol.find("_USDT") != std::string::npos) symbols.push_back(symbol);
    }
    std::sort(symbols.begin(), symbols.end());
    symbols.erase(std::unique(symbols.begin(), symbols.end()), symbols.end());
    cached = symbols;
    cached_at = now;
    return cached;
}

std::string parse_usdt_equity(const std::string& json) {
    size_t currency = json.find("\"currency\":\"USDT\"");
    if (currency == std::string::npos) currency = json.find("\"currency\": \"USDT\"");
    if (currency == std::string::npos) return "";
    size_t begin = json.rfind('{', currency);
    size_t end = json.find('}', currency);
    if (begin == std::string::npos || end == std::string::npos) return "";
    std::string object = json.substr(begin, end - begin + 1);
    for (const std::string& key : {"equity", "availableBalance", "cashBalance", "balance"}) {
        std::string value = json_field(object, key);
        if (!value.empty()) return value;
    }
    return "";
}

