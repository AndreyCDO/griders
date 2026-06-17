<?php

declare(strict_types=1);

const DB_HOST = 'localhost';
const DB_PORT = '3306';
const DB_NAME = 'ctgacademy_r';
const DB_USER = 'ctg_andreycdo';
const DB_PASS = '';
const APP_URL = 'https://ctgacademy.ru';
const APP_SECRET_KEY = '';
const DB_SOCKET = '';
const MAIL_FROM_EMAIL = 'noreply@ctgacademy.ru';
const MAIL_FROM_NAME = 'Академия Криптотрейдинга';
const MAIL_ENVELOPE_FROM = 'noreply@ctgacademy.ru';

function db(): PDO
{
    static $pdo = null;

    if ($pdo instanceof PDO) {
        return $pdo;
    }

    if (DB_SOCKET !== '') {
        $dsn = sprintf('mysql:unix_socket=%s;dbname=%s;charset=utf8mb4', DB_SOCKET, DB_NAME);
    } elseif (DB_HOST === 'localhost') {
        $dsn = sprintf('mysql:host=%s;dbname=%s;charset=utf8mb4', DB_HOST, DB_NAME);
    } else {
        $dsn = sprintf('mysql:host=%s;port=%s;dbname=%s;charset=utf8mb4', DB_HOST, DB_PORT, DB_NAME);
    }

    $pdo = new PDO($dsn, DB_USER, DB_PASS, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);

    return $pdo;
}
