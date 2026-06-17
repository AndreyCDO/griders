<?php

declare(strict_types=1);

require_once __DIR__ . '/auth.php';

const BYBIT_API_BASE = 'https://api.bybit.com';
const BYBIT_RECV_WINDOW = '5000';

function bybitBuildQuery(array $params): string
{
    ksort($params);
    return http_build_query($params, '', '&', PHP_QUERY_RFC3986);
}

function bybitRequest(string $path, string $apiKey, string $apiSecret, array $params = []): array
{
    $queryString = bybitBuildQuery($params);
    $timestamp = (string)round(microtime(true) * 1000);
    $payload = $timestamp . $apiKey . BYBIT_RECV_WINDOW . $queryString;
    $signature = hash_hmac('sha256', $payload, $apiSecret);
    $url = BYBIT_API_BASE . $path . ($queryString !== '' ? '?' . $queryString : '');

    $ch = curl_init($url);

    if ($ch === false) {
        throw new RuntimeException('Не удалось инициализировать HTTP-запрос.');
    }

    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 20,
        CURLOPT_HTTPHEADER => [
            'Accept: application/json',
            'X-BAPI-API-KEY: ' . $apiKey,
            'X-BAPI-TIMESTAMP: ' . $timestamp,
            'X-BAPI-RECV-WINDOW: ' . BYBIT_RECV_WINDOW,
            'X-BAPI-SIGN: ' . $signature,
        ],
    ]);

    $response = curl_exec($ch);

    if ($response === false) {
        $error = curl_error($ch);
        curl_close($ch);
        throw new RuntimeException('Ошибка подключения к API: ' . $error);
    }

    $statusCode = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    curl_close($ch);

    if ($statusCode >= 400) {
        throw new RuntimeException('API вернул HTTP ' . $statusCode . '.');
    }

    $data = json_decode($response, true);

    if (!is_array($data)) {
        throw new RuntimeException('Некорректный ответ API.');
    }

    if ((int)($data['retCode'] ?? -1) !== 0) {
        $message = (string)($data['retMsg'] ?? 'Неизвестная ошибка API.');
        throw new RuntimeException($message);
    }

    return $data['result'] ?? [];
}

function fetchBotMonitorSnapshot(string $apiKey, string $apiSecret): array
{
    $wallet = bybitRequest('/v5/account/wallet-balance', $apiKey, $apiSecret, [
        'accountType' => 'UNIFIED',
    ]);

    $positions = bybitRequest('/v5/position/list', $apiKey, $apiSecret, [
        'category' => 'linear',
        'settleCoin' => 'USDT',
        'limit' => 50,
    ]);

    return [
        'wallet' => $wallet,
        'positions' => $positions['list'] ?? [],
    ];
}

function fetchClosedPnlHistory(string $apiKey, string $apiSecret, int $startMs, int $endMs): array
{
    $items = [];
    $chunkStart = $startMs;
    $weekMs = 7 * 24 * 60 * 60 * 1000;

    while ($chunkStart <= $endMs) {
        $chunkEnd = min($endMs, $chunkStart + $weekMs - 1);
        $cursor = null;

        do {
            $params = [
                'category' => 'linear',
                'startTime' => $chunkStart,
                'endTime' => $chunkEnd,
                'limit' => 100,
            ];

            if ($cursor) {
                $params['cursor'] = $cursor;
            }

            $result = bybitRequest('/v5/position/closed-pnl', $apiKey, $apiSecret, $params);
            $list = $result['list'] ?? [];

            if (is_array($list)) {
                foreach ($list as $row) {
                    if (is_array($row)) {
                        $items[] = $row;
                    }
                }
            }

            $cursor = isset($result['nextPageCursor']) && $result['nextPageCursor'] !== ''
                ? (string)$result['nextPageCursor']
                : null;
        } while ($cursor !== null);

        $chunkStart = $chunkEnd + 1;
    }

    usort($items, static function (array $left, array $right): int {
        return (int)($left['createdTime'] ?? 0) <=> (int)($right['createdTime'] ?? 0);
    });

    return $items;
}
