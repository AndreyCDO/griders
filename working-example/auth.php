<?php

declare(strict_types=1);

require_once __DIR__ . '/config.php';

if (session_status() !== PHP_SESSION_ACTIVE) {
    session_start();
}

function redirect(string $path): never
{
    header('Location: ' . $path);
    exit;
}

function currentUser(): ?array
{
    if (empty($_SESSION['user_id'])) {
        return null;
    }

    $stmt = db()->prepare('SELECT id, email FROM users WHERE id = :id LIMIT 1');
    $stmt->execute(['id' => $_SESSION['user_id']]);
    $user = $stmt->fetch();

    return $user ?: null;
}

function requireAuth(): void
{
    if (!currentUser()) {
        redirect('/index.php');
    }
}

function isAdmin(): bool
{
    $user = currentUser();

    if (!$user) {
        return false;
    }

    static $cache = [];
    $userId = (int)$user['id'];

    if (array_key_exists($userId, $cache)) {
        return $cache[$userId];
    }

    try {
        $stmt = db()->prepare('SELECT is_admin FROM users WHERE id = :id LIMIT 1');
        $stmt->execute(['id' => $userId]);
        $row = $stmt->fetch();

        $cache[$userId] = (bool)($row['is_admin'] ?? false);
        return $cache[$userId];
    } catch (Throwable $e) {
        error_log('Admin role check error: ' . $e->getMessage());
        $cache[$userId] = false;
        return false;
    }
}

function requireAdmin(): void
{
    requireAuth();

    if (!isAdmin()) {
        flash('error', 'У вас нет доступа к разделу администратора.');
        redirect('/dashboard.php');
    }
}

function flash(string $key, ?string $value = null): ?string
{
    if ($value !== null) {
        $_SESSION['flash'][$key] = $value;
        return null;
    }

    if (!isset($_SESSION['flash'][$key])) {
        return null;
    }

    $msg = $_SESSION['flash'][$key];
    unset($_SESSION['flash'][$key]);

    return $msg;
}

function isStrongPassword(string $password): bool
{
    return strlen($password) >= 8
        && preg_match('/\p{L}/u', $password) === 1
        && preg_match('/\d/', $password) === 1;
}

function sendAppMail(string $email, string $subject, string $message): bool
{
    $headers = [];
    $headers[] = 'MIME-Version: 1.0';
    $headers[] = 'Content-Type: text/plain; charset=UTF-8';
    $headers[] = 'From: ' . MAIL_FROM_NAME . ' <' . MAIL_FROM_EMAIL . '>';
    $headers[] = 'Reply-To: ' . MAIL_FROM_EMAIL;
    $headersRaw = implode("\r\n", $headers);

    return mail($email, $subject, $message, $headersRaw, '-f' . MAIL_ENVELOPE_FROM);
}

function appSecretKey(): string
{
    $secret = trim((string)(defined('APP_SECRET_KEY') ? APP_SECRET_KEY : ''));

    if ($secret === '') {
        throw new RuntimeException('APP_SECRET_KEY is not configured.');
    }

    return hash('sha256', $secret, true);
}

function encryptSensitive(string $plainText): array
{
    $key = appSecretKey();
    $iv = random_bytes(16);
    $cipherText = openssl_encrypt($plainText, 'aes-256-cbc', $key, OPENSSL_RAW_DATA, $iv);

    if ($cipherText === false) {
        throw new RuntimeException('Encryption failed.');
    }

    return [
        'ciphertext' => base64_encode($cipherText),
        'iv' => base64_encode($iv),
    ];
}

function decryptSensitive(string $cipherText, string $iv): string
{
    $key = appSecretKey();
    $rawCipherText = base64_decode($cipherText, true);
    $rawIv = base64_decode($iv, true);

    if ($rawCipherText === false || $rawIv === false) {
        throw new RuntimeException('Invalid encrypted payload.');
    }

    $plainText = openssl_decrypt($rawCipherText, 'aes-256-cbc', $key, OPENSSL_RAW_DATA, $rawIv);

    if ($plainText === false) {
        throw new RuntimeException('Decryption failed.');
    }

    return $plainText;
}

function contentHtml(string $slug, string $fallback = ''): string
{
    try {
        $stmt = db()->prepare(
            'SELECT content_html
             FROM legal_texts
             WHERE slug = :slug
             LIMIT 1'
        );
        $stmt->execute(['slug' => $slug]);
        $row = $stmt->fetch();

        return (string)($row['content_html'] ?? $fallback);
    } catch (Throwable $e) {
        error_log('Content load error for ' . $slug . ': ' . $e->getMessage());
        return $fallback;
    }
}

function clientIpAddress(): string
{
    $headers = [
        'HTTP_CF_CONNECTING_IP',
        'HTTP_X_REAL_IP',
    ];

    foreach ($headers as $header) {
        $value = trim((string)($_SERVER[$header] ?? ''));

        if ($value !== '' && filter_var($value, FILTER_VALIDATE_IP)) {
            return $value;
        }
    }

    $forwardedFor = (string)($_SERVER['HTTP_X_FORWARDED_FOR'] ?? '');

    foreach (explode(',', $forwardedFor) as $value) {
        $ip = trim($value);

        if ($ip !== '' && filter_var($ip, FILTER_VALIDATE_IP)) {
            return $ip;
        }
    }

    $remoteAddr = trim((string)($_SERVER['REMOTE_ADDR'] ?? ''));

    return filter_var($remoteAddr, FILTER_VALIDATE_IP) ? $remoteAddr : '';
}

function isPrivateIp(string $ip): bool
{
    if ($ip === '') {
        return true;
    }

    return filter_var(
        $ip,
        FILTER_VALIDATE_IP,
        FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE
    ) === false;
}

function countryCodeFromHeaders(): ?string
{
    $headers = [
        'HTTP_CF_IPCOUNTRY',
        'HTTP_X_COUNTRY_CODE',
        'HTTP_X_GEOIP_COUNTRY_CODE',
        'GEOIP_COUNTRY_CODE',
    ];

    foreach ($headers as $header) {
        $value = strtoupper(trim((string)($_SERVER[$header] ?? '')));

        if (preg_match('/^[A-Z]{2}$/', $value) === 1) {
            return $value;
        }
    }

    return null;
}

function countryCodeForCurrentVisitor(): ?string
{
    $headerCountry = countryCodeFromHeaders();

    if ($headerCountry !== null) {
        return $headerCountry;
    }

    $ip = clientIpAddress();

    if (isPrivateIp($ip)) {
        return 'RU';
    }

    $cacheKey = 'geoip_country_' . $ip;

    if (isset($_SESSION[$cacheKey])) {
        return $_SESSION[$cacheKey] ?: null;
    }

    if (!function_exists('curl_init')) {
        return null;
    }

    $url = 'http://ip-api.com/json/' . rawurlencode($ip) . '?fields=status,countryCode';
    $ch = curl_init($url);

    if ($ch === false) {
        return null;
    }

    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 2,
        CURLOPT_CONNECTTIMEOUT => 1,
    ]);

    $response = curl_exec($ch);
    curl_close($ch);

    if (!is_string($response) || $response === '') {
        $_SESSION[$cacheKey] = '';
        return null;
    }

    $data = json_decode($response, true);
    $countryCode = is_array($data) && ($data['status'] ?? '') === 'success'
        ? strtoupper((string)($data['countryCode'] ?? ''))
        : '';

    $_SESSION[$cacheKey] = preg_match('/^[A-Z]{2}$/', $countryCode) === 1 ? $countryCode : '';

    return $_SESSION[$cacheKey] ?: null;
}

function shouldUseRutube(): bool
{
    return in_array(countryCodeForCurrentVisitor(), ['RU', 'BY'], true);
}

function preferredVideoUrl(?string $youtubeUrl, ?string $rutubeUrl): string
{
    $youtubeUrl = trim((string)$youtubeUrl);
    $rutubeUrl = trim((string)$rutubeUrl);

    if (shouldUseRutube()) {
        return $rutubeUrl !== '' ? $rutubeUrl : $youtubeUrl;
    }

    return $youtubeUrl !== '' ? $youtubeUrl : $rutubeUrl;
}
