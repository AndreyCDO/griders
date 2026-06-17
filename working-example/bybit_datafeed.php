<?php
declare(strict_types=1);

// Bybit datafeed proxy for TradingView Charting Library
// Routes: ?route=config|symbols|history|time|marks

require_once __DIR__ . '/demo_trading_lib.php';

// Allow CORS for same origin (iframe)
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

requireAuth();

$route = (string)($_GET['route'] ?? '');

function bybitRequest(string $path, array $params = []): array
{
    $url = 'https://api.bybit.com' . $path;
    if ($params) {
        $url .= '?' . http_build_query($params);
    }
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 10,
        CURLOPT_CONNECTTIMEOUT => 5,
        CURLOPT_HTTPHEADER => ['Accept: application/json'],
        CURLOPT_USERAGENT => 'CTGAcademy/1.0',
    ]);
    $resp = curl_exec($ch);
    $err = curl_error($ch);
    curl_close($ch);
    if ($resp === false) {
        throw new RuntimeException('Bybit API error: ' . $err);
    }
    $data = json_decode($resp, true);
    if (!is_array($data)) {
        throw new RuntimeException('Invalid Bybit response');
    }
    return $data;
}

// Map short symbol names to Bybit linear/spot pairs
function resolveBybitSymbol(string $tv_symbol, string $type = 'spot'): array
{
    // tv_symbol format: "BTCUSDT" or "BYBIT:BTCUSDT" or "BYBIT:BTCUSDT.P"
    $s = strtoupper(trim($tv_symbol));
    $s = preg_replace('/^BYBIT:/', '', $s);
    $isFutures = str_ends_with($s, '.P') || $type === 'linear';
    $s = rtrim($s, '.P');
    // Ensure USDT suffix
    if (!str_ends_with($s, 'USDT')) {
        $s .= 'USDT';
    }
    return [
        'symbol' => $s,
        'category' => $isFutures ? 'linear' : 'spot',
    ];
}

function intervalToBybitKlineInterval(string $resolution): string
{
    return match($resolution) {
        '1'    => '1',
        '3'    => '3',
        '5'    => '5',
        '15'   => '15',
        '30'   => '30',
        '60'   => '60',
        '120'  => '120',
        '240'  => '240',
        '360'  => '360',
        '720'  => '720',
        'D', '1D' => 'D',
        'W', '1W' => 'W',
        'M', '1M' => 'M',
        default => '60',
    };
}

try {
    switch ($route) {

        case 'config':
            echo json_encode([
                'supported_resolutions' => ['1','3','5','15','30','60','120','240','360','720','D','W','M'],
                'supports_group_request' => false,
                'supports_marks' => true,
                'supports_search' => true,
                'supports_timescale_marks' => false,
                'exchanges' => [
                    ['value' => 'BYBIT', 'name' => 'Bybit', 'desc' => 'Bybit Exchange'],
                ],
                'symbols_types' => [
                    ['name' => 'spot', 'value' => 'spot'],
                    ['name' => 'futures', 'value' => 'futures'],
                ],
            ]);
            break;

        case 'symbols':
            $symbolParam = (string)($_GET['symbol'] ?? 'BTCUSDT');
            $isFutures = str_contains($symbolParam, '.P') || (string)($_GET['type'] ?? '') === 'futures';

            $resolved = resolveBybitSymbol($symbolParam, $isFutures ? 'linear' : 'spot');
            $sym = $resolved['symbol'];
            $cat = $resolved['category'];

            // Fetch instrument info
            try {
                $info = bybitRequest('/v5/market/instruments-info', [
                    'category' => $cat,
                    'symbol' => $sym,
                ]);
                $instrument = $info['result']['list'][0] ?? null;
            } catch (Throwable $e) {
                $instrument = null;
            }

            $pricePrecision = 2;
            $minMove = 0.01;
            if ($instrument) {
                $tickSize = (float)($instrument['priceFilter']['tickSize'] ?? 0.01);
                if ($tickSize > 0) {
                    $decimals = strlen(rtrim(rtrim(number_format($tickSize, 10, '.', ''), '0'), '.'));
                    $pricePrecision = max(0, min(10, $decimals));
                    $minMove = $tickSize;
                }
            }

            $displaySym = $cat === 'linear' ? $sym . '.P' : $sym;
            $base = str_replace('USDT', '', $sym);

            echo json_encode([
                'name' => $displaySym,
                'ticker' => 'BYBIT:' . $displaySym,
                'description' => $base . '/USDT ' . ($cat === 'linear' ? 'Perpetual' : 'Spot'),
                'type' => $cat === 'linear' ? 'futures' : 'spot',
                'session' => '24x7',
                'timezone' => 'Etc/UTC',
                'exchange' => 'BYBIT',
                'listed_exchange' => 'BYBIT',
                'minmov' => 1,
                'pricescale' => (int)round(1 / $minMove),
                'has_intraday' => true,
                'has_daily' => true,
                'has_weekly_and_monthly' => true,
                'supported_resolutions' => ['1','3','5','15','30','60','120','240','360','720','D','W','M'],
                'volume_precision' => 4,
                'data_status' => 'streaming',
            ]);
            break;

        case 'search':
            $query = strtoupper(trim((string)($_GET['query'] ?? '')));
            $limit = min(30, (int)($_GET['limit'] ?? 15));
            $type = (string)($_GET['type'] ?? '');

            $results = [];
            try {
                if ($type !== 'futures') {
                    $spotData = bybitRequest('/v5/market/instruments-info', ['category' => 'spot', 'limit' => 200]);
                    foreach ($spotData['result']['list'] ?? [] as $item) {
                        $s = (string)$item['symbol'];
                        if (!str_ends_with($s, 'USDT')) continue;
                        if ($query && !str_contains($s, $query)) continue;
                        $base = str_replace('USDT', '', $s);
                        $results[] = [
                            'symbol' => $s,
                            'full_name' => 'BYBIT:' . $s,
                            'description' => $base . '/USDT Spot',
                            'exchange' => 'BYBIT',
                            'type' => 'spot',
                        ];
                        if (count($results) >= $limit) break;
                    }
                }
                if ($type !== 'spot' && count($results) < $limit) {
                    $futData = bybitRequest('/v5/market/instruments-info', ['category' => 'linear', 'limit' => 200]);
                    foreach ($futData['result']['list'] ?? [] as $item) {
                        $s = (string)$item['symbol'];
                        if (!str_ends_with($s, 'USDT')) continue;
                        if ($query && !str_contains($s, $query)) continue;
                        $base = str_replace('USDT', '', $s);
                        $results[] = [
                            'symbol' => $s . '.P',
                            'full_name' => 'BYBIT:' . $s . '.P',
                            'description' => $base . '/USDT Perpetual',
                            'exchange' => 'BYBIT',
                            'type' => 'futures',
                        ];
                        if (count($results) >= $limit) break;
                    }
                }
            } catch (Throwable $e) {
                // Return empty on error
            }

            echo json_encode($results);
            break;

        case 'history':
            $symbolParam = (string)($_GET['symbol'] ?? 'BTCUSDT');
            $resolution  = (string)($_GET['resolution'] ?? '60');
            $from        = (int)($_GET['from'] ?? (time() - 86400));
            $to          = (int)($_GET['to'] ?? time());

            $resolved = resolveBybitSymbol($symbolParam);
            $sym = $resolved['symbol'];
            $cat = $resolved['category'];

            $interval = intervalToBybitKlineInterval($resolution);

            // Bybit kline: startTime/endTime in ms, limit max 1000
            $intervalMs = match($interval) {
                '1' => 60000, '3' => 180000, '5' => 300000, '15' => 900000,
                '30' => 1800000, '60' => 3600000, '120' => 7200000,
                '240' => 14400000, '360' => 21600000, '720' => 43200000,
                'D' => 86400000, 'W' => 604800000, 'M' => 2592000000,
                default => 3600000,
            };

            $startMs = $from * 1000;
            $endMs   = $to * 1000;
            $maxBars = 1000;

            $allBars = [];
            $cursor = $endMs;

            // Fetch in chunks going backwards
            for ($i = 0; $i < 5 && $cursor > $startMs; $i++) {
                $chunkStart = max($startMs, $cursor - $intervalMs * $maxBars);
                try {
                    $klineData = bybitRequest('/v5/market/kline', [
                        'category' => $cat,
                        'symbol' => $sym,
                        'interval' => $interval,
                        'start' => (int)$chunkStart,
                        'end' => (int)$cursor,
                        'limit' => $maxBars,
                    ]);
                } catch (Throwable $e) {
                    break;
                }

                $list = $klineData['result']['list'] ?? [];
                if (empty($list)) break;

                foreach ($list as $bar) {
                    // Bybit: [startTime, open, high, low, close, volume, turnover]
                    $t = (int)($bar[0] / 1000);
                    if ($t < $from || $t > $to) continue;
                    $allBars[$t] = [
                        'time'   => $t,
                        'open'   => (float)$bar[1],
                        'high'   => (float)$bar[2],
                        'low'    => (float)$bar[3],
                        'close'  => (float)$bar[4],
                        'volume' => (float)$bar[5],
                    ];
                }

                $minTime = min(array_column($list, 0));
                $cursor = (int)$minTime - 1;
                if (count($list) < $maxBars) break;
            }

            if (empty($allBars)) {
                echo json_encode(['s' => 'no_data']);
                break;
            }

            ksort($allBars);
            $bars = array_values($allBars);

            $t = $o = $h = $l = $c = $v = [];
            foreach ($bars as $bar) {
                $t[] = $bar['time'];
                $o[] = $bar['open'];
                $h[] = $bar['high'];
                $l[] = $bar['low'];
                $c[] = $bar['close'];
                $v[] = $bar['volume'];
            }

            echo json_encode([
                's' => 'ok',
                't' => $t,
                'o' => $o,
                'h' => $h,
                'l' => $l,
                'c' => $c,
                'v' => $v,
            ]);
            break;

        case 'time':
            echo json_encode((int)(microtime(true)));
            break;

        case 'marks':
            // Return order markers for the current user
            $symbolParam = (string)($_GET['symbol'] ?? 'BTCUSDT');
            $from = (int)($_GET['from'] ?? 0);
            $to   = (int)($_GET['to'] ?? time());

            // Get userId from session (already auth'd)
            $user = currentUser();
            $userId = (int)$user['id'];

            // Parse symbol
            $rawSym = strtoupper(str_replace(['BYBIT:', '.P'], '', $symbolParam));
            $baseSym = str_replace('USDT', '', $rawSym);
            $baseSym = normalizeDemoSymbol($baseSym);

            $stmt = db()->prepare(
                'SELECT ft.*, dfo.trigger_type
                 FROM demo_futures_trades ft
                 LEFT JOIN demo_futures_orders dfo ON dfo.id = ft.id
                 WHERE ft.user_id = :user_id AND ft.symbol = :symbol
                   AND UNIX_TIMESTAMP(ft.created_at) BETWEEN :from AND :to
                 ORDER BY ft.created_at ASC
                 LIMIT 200'
            );
            $stmt->execute(['user_id' => $userId, 'symbol' => $baseSym, 'from' => $from, 'to' => $to]);
            $trades = $stmt->fetchAll();

            $marks = [];
            foreach ($trades as $trade) {
                $isLong = $trade['side'] === 'long';
                $isOpen = $trade['action'] === 'open';
                $marks[] = [
                    'id'    => (int)$trade['id'],
                    'time'  => strtotime($trade['created_at']),
                    'color' => $isOpen ? ($isLong ? 'green' : 'red') : ($isLong ? 'blue' : 'orange'),
                    'text'  => ($isOpen ? 'Open ' : 'Close ') . strtoupper($trade['side']) . ' @' . number_format((float)$trade['price'], 2),
                    'label' => $isOpen ? ($isLong ? 'B' : 'S') : 'X',
                    'labelFontColor' => 'white',
                    'minSize' => 14,
                ];
            }

            echo json_encode($marks);
            break;

        case 'price':
            // Quick current price fetch for order processing
            $symbolParam = (string)($_GET['symbol'] ?? 'BTCUSDT');
            $resolved = resolveBybitSymbol($symbolParam);
            $sym = $resolved['symbol'];
            $cat = $resolved['category'];

            $ticker = bybitRequest('/v5/market/tickers', [
                'category' => $cat,
                'symbol' => $sym,
            ]);
            $price = (float)($ticker['result']['list'][0]['lastPrice'] ?? 0);
            echo json_encode(['price' => $price, 'symbol' => $sym]);
            break;

        default:
            http_response_code(400);
            echo json_encode(['error' => 'Unknown route: ' . $route]);
    }
} catch (Throwable $e) {
    http_response_code(500);
    echo json_encode(['error' => $e->getMessage()]);
}
