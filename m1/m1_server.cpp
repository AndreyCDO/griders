#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <openssl/hmac.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <mutex>
#include <regex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace {

constexpr const char* kMexcBase = "https://api.mexc.com";
constexpr int kDefaultPort = 8010;

struct MexcConnection {
    std::string label;
    std::string api_key;
    std::string api_secret;
};

struct MarketRow {
    std::string symbol;
    bool exists = false;
    bool api_allowed = false;
    bool zero_maker = false;
    bool zero_taker = false;
    std::string maker_fee = "—";
    std::string taker_fee = "—";
    std::string state = "—";
    std::string max_leverage = "—";
    std::string min_vol = "—";
};

struct ContractSpec {
    std::string symbol;
    bool exists = false;
    bool api_allowed = false;
    double contract_size = 0.0;
    double min_vol = 1.0;
    double max_vol = 0.0;
    double vol_unit = 1.0;
    int vol_scale = 0;
    int price_scale = 4;
    int max_leverage = 0;
};

struct BotConfig {
    int id = 0;
    std::string name = "MEXC Bot";
    std::string action = "open";
    std::string strategy = "long";
    std::string pair = "XRPUSDT";
    double order_volume = 6.0;
    int leverage = 10;
    std::string margin_type = "cross";
    std::string order_type = "Market";
    bool continuous = false;
    int cycles = 1;
    bool dca_enabled = true;
    int dca_active = 2;
    int dca_max = 2;
    double dca_volume = 6.0;
    double dca_percent = 1.0;
    double dca_multiplier_volume = 1.2;
    double dca_multiplier_price = 1.2;
    bool close_enabled = true;
    double take_profit_percent = 0.5;
    bool stop_enabled = true;
    double stop_loss_percent = 3.0;
};

struct PlannedOrder {
    std::string label;
    std::string symbol;
    int side = 1;
    int type = 5;
    int open_type = 2;
    int leverage = 10;
    double price = 0.0;
    double vol = 0.0;
    double margin_usdt = 0.0;
    double take_profit_price = 0.0;
    double stop_loss_price = 0.0;
};

struct BotPlan {
    BotConfig config;
    ContractSpec spec;
    double mark_price = 0.0;
    std::vector<PlannedOrder> orders;
    std::vector<std::string> warnings;
    std::vector<std::string> errors;
};

struct ActiveRun {
    int bot_id = 0;
    std::string run_id;
    std::string symbol;
    std::string strategy;
    int open_type = 2;
    int leverage = 10;
    bool close_enabled = true;
    double take_profit_percent = 0.0;
    bool stop_enabled = true;
    double stop_loss_percent = 0.0;
    double last_avg_price = 0.0;
    double last_hold_vol = 0.0;
    double last_take_profit_price = 0.0;
    double last_stop_loss_price = 0.0;
    long long position_id = 0;
    std::string status = "active";
    long long updated_at = 0;
};

struct Deal {
    std::string deal_id;
    int bot_id = 0;
    std::string bot_name;
    std::string symbol;
    std::string strategy;
    bool live = false;
    std::string status = "created";
    int open_type = 2;
    int leverage = 10;
    double planned_margin_usdt = 0.0;
    double planned_volume = 0.0;
    double mark_price = 0.0;
    std::string config_json;
    long long created_at = 0;
    long long updated_at = 0;
};

struct DealOrder {
    std::string deal_id;
    std::string label;
    std::string symbol;
    int side = 0;
    int type = 0;
    int open_type = 2;
    int leverage = 0;
    double price = 0.0;
    double vol = 0.0;
    double margin_usdt = 0.0;
    double take_profit_price = 0.0;
    double stop_loss_price = 0.0;
    std::string status = "planned";
    std::string exchange_order_id;
    std::string request_json;
    std::string response_json;
    long long created_at = 0;
    long long updated_at = 0;
};

const std::vector<std::string> kTrackedPairs = {
    "BTC_USDT", "ETH_USDT", "SOL_USDT", "HYPE_USDT", "NEAR_USDT",
    "ZEC_USDT", "TON_USDT", "XRP_USDT", "SUI_USDT", "FIL_USDT",
    "TAO_USDT", "RENDER_USDT", "ADA_USDT", "INJ_USDT", "LIT_USDT",
    "ENA_USDT", "LINK_USDT", "AVAX_USDT", "JUP_USDT", "ARB_USDT",
};

const std::vector<std::string> kPersonalZeroFeeCandidates = {
    "ABTCSTOCK_USDT", "AI16Z_USDT", "BLSHSTOCK_USDT", "ETHWSTOCK_USDT",
    "HIFI_USDT", "ICGSTOCK_USDT", "KBBB_USDT", "MOONPIG_USDT",
    "NEIROETH_USDT", "TRONSTOCK_USDT",
};

std::string normalize_symbol(std::string value);
std::string compact_symbol(const std::string& symbol);
std::string format_number(double value, int scale);
std::string json_escape(const std::string& value);
std::string bot_to_json(const BotConfig& bot);
std::string json_field(const std::string& object, const std::string& key);
std::string json_object_field(const std::string& object, const std::string& key);
bool mexc_response_success(const std::string& response);
void append_bot_log(const std::string& text);

std::mutex g_active_runs_mutex;

#include "m1_util.inc"
#include "m1_storage.inc"
#include "m1_mexc.inc"
#include "m1_bot_engine.inc"
#include "m1_web.inc"

}  // namespace

int main() {
    int port = std::atoi(getenv_or("M1_PORT", std::to_string(kDefaultPort)).c_str());
    std::string password = getenv_or("M1_ADMIN_PASSWORD", "");
    if (password.empty()) {
        std::cerr << "M1_ADMIN_PASSWORD is required\n";
        return 1;
    }
    ensure_data_dir();
    if (getenv_or("M1_POSITION_MANAGER", "on") != "off") {
        std::thread(position_manager_loop).detach();
    }

    int server_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (server_fd < 0) {
        std::cerr << "socket error\n";
        return 1;
    }
    int opt = 1;
    setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = inet_addr("127.0.0.1");
    addr.sin_port = htons(port);
    if (bind(server_fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        std::cerr << "bind error on 127.0.0.1:" << port << "\n";
        return 1;
    }
    if (listen(server_fd, 64) < 0) {
        std::cerr << "listen error\n";
        return 1;
    }
    std::cerr << "M1 C++ server listening on 127.0.0.1:" << port << "\n";

    while (true) {
        int client = accept(server_fd, nullptr, nullptr);
        if (client < 0) continue;
        std::thread(handle_client, client).detach();
    }
}
