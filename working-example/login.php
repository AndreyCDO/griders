<?php

declare(strict_types=1);

require_once __DIR__ . '/auth.php';

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    redirect('/index.php');
}

$email = trim((string)($_POST['email'] ?? ''));
$password = (string)($_POST['password'] ?? '');

try {
    $stmt = db()->prepare('SELECT id, password_hash, verified_at FROM users WHERE email = :email LIMIT 1');
    $stmt->execute(['email' => $email]);
    $user = $stmt->fetch();

    if (!$user || !password_verify($password, $user['password_hash'])) {
        flash('error', 'Неверный email или пароль.');
        redirect('/index.php?auth=login');
    }

    if (!$user['verified_at']) {
        flash('error', 'Подтвердите email перед входом в аккаунт.');
        redirect('/index.php?auth=login');
    }

    $_SESSION['user_id'] = (int)$user['id'];
} catch (Throwable $e) {
    error_log('Login error: ' . $e->getMessage());
    flash('error', 'Ошибка сервера при входе. Попробуйте ещё раз.');
    redirect('/index.php?auth=login');
}

redirect('/dashboard.php');
