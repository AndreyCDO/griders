<?php

declare(strict_types=1);

require_once __DIR__ . '/auth.php';

$token = trim((string)($_GET['token'] ?? ''));

if ($token === '') {
    flash('error', 'Некорректная ссылка подтверждения.');
    redirect('/index.php');
}

try {
    $update = db()->prepare(
        'UPDATE users SET verified_at = NOW(), verification_token = NULL, updated_at = NOW() WHERE verification_token = :token LIMIT 1'
    );
    $update->execute(['token' => $token]);

    if ($update->rowCount() === 0) {
        flash('error', 'Ссылка подтверждения недействительна или уже использована.');
        redirect('/index.php?auth=login');
    }
} catch (Throwable $e) {
    error_log('Verify error: ' . $e->getMessage());
    flash('error', 'Ошибка сервера при подтверждении email.');
    redirect('/index.php');
}

flash('success', 'Email подтверждён. Теперь можно войти в аккаунт.');
redirect('/index.php?auth=login');
