std::string getenv_or(const std::string& key, const std::string& fallback) {
    const char* value = std::getenv(key.c_str());
    return value && *value ? std::string(value) : fallback;
}

std::string html_escape(const std::string& value) {
    std::string out;
    out.reserve(value.size());
    for (char c : value) {
        switch (c) {
            case '&': out += "&amp;"; break;
            case '<': out += "&lt;"; break;
            case '>': out += "&gt;"; break;
            case '"': out += "&quot;"; break;
            case '\'': out += "&#39;"; break;
            default: out += c; break;
        }
    }
    return out;
}

std::string trim(const std::string& value) {
    size_t begin = 0;
    while (begin < value.size() && std::isspace(static_cast<unsigned char>(value[begin]))) begin++;
    size_t end = value.size();
    while (end > begin && std::isspace(static_cast<unsigned char>(value[end - 1]))) end--;
    return value.substr(begin, end - begin);
}

std::string url_decode(const std::string& value) {
    std::string out;
    for (size_t i = 0; i < value.size(); ++i) {
        if (value[i] == '+' ) {
            out += ' ';
        } else if (value[i] == '%' && i + 2 < value.size()) {
            std::string hex = value.substr(i + 1, 2);
            char* end = nullptr;
            long ch = std::strtol(hex.c_str(), &end, 16);
            if (end && *end == '\0') {
                out += static_cast<char>(ch);
                i += 2;
            } else {
                out += value[i];
            }
        } else {
            out += value[i];
        }
    }
    return out;
}

std::string url_encode(const std::string& value) {
    std::ostringstream out;
    out << std::hex << std::uppercase;
    for (unsigned char c : value) {
        if (std::isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') {
            out << c;
        } else {
            out << '%' << std::setw(2) << std::setfill('0') << static_cast<int>(c);
        }
    }
    return out.str();
}

std::map<std::string, std::string> parse_form(const std::string& body) {
    std::map<std::string, std::string> fields;
    size_t start = 0;
    while (start <= body.size()) {
        size_t amp = body.find('&', start);
        std::string part = body.substr(start, amp == std::string::npos ? std::string::npos : amp - start);
        size_t eq = part.find('=');
        std::string key = url_decode(part.substr(0, eq));
        std::string val = eq == std::string::npos ? "" : url_decode(part.substr(eq + 1));
        if (!key.empty()) fields[key] = val;
        if (amp == std::string::npos) break;
        start = amp + 1;
    }
    return fields;
}

std::string query_param(const std::string& path, const std::string& key) {
    size_t q = path.find('?');
    if (q == std::string::npos) return "";
    return parse_form(path.substr(q + 1))[key];
}

std::string base64_decode(const std::string& input) {
    static const std::string chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::vector<int> table(256, -1);
    for (int i = 0; i < 64; i++) table[static_cast<unsigned char>(chars[i])] = i;
    std::string out;
    int val = 0;
    int bits = -8;
    for (unsigned char c : input) {
        if (c == '=') break;
        if (table[c] == -1) continue;
        val = (val << 6) + table[c];
        bits += 6;
        if (bits >= 0) {
            out.push_back(static_cast<char>((val >> bits) & 0xFF));
            bits -= 8;
        }
    }
    return out;
}

std::string hmac_sha256_hex(const std::string& secret, const std::string& payload) {
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int len = 0;
    HMAC(EVP_sha256(), secret.data(), static_cast<int>(secret.size()),
         reinterpret_cast<const unsigned char*>(payload.data()), payload.size(), digest, &len);
    std::ostringstream out;
    out << std::hex << std::setfill('0');
    for (unsigned int i = 0; i < len; ++i) out << std::setw(2) << static_cast<int>(digest[i]);
    return out.str();
}

