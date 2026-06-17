<?php

declare(strict_types=1);

require_once __DIR__ . '/auth.php';

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    redirect('/index.php');
}

$email = trim((string)($_POST['email'] ?? ''));
$password = (string)($_POST['password'] ?? '');
$consentPersonalData = ($_POST['consent_personal_data'] ?? '') === '1';

if (!filter_var($email, FILTER_VALIDATE_EMAIL)) {
    flash('error', 'Укажите корректный email.');
    redirect('/index.php?auth=register');
}

if (!isStrongPassword($password)) {
    flash('error', 'Пароль должен быть не короче 8 символов и содержать буквы и цифры.');
    redirect('/index.php?auth=register');
}

if (!$consentPersonalData) {
    flash('error', 'Для регистрации необходимо дать согласие на обработку персональных данных.');
    redirect('/index.php?auth=register');
}

try {
    $stmt = db()->prepare('SELECT id, verified_at FROM users WHERE email = :email LIMIT 1');
    $stmt->execute(['email' => $email]);
    $existing = $stmt->fetch();

    if ($existing && $existing['verified_at']) {
        flash('error', 'Пользователь с таким email уже зарегистрирован.');
        redirect('/index.php?auth=register');
    }

    $token = bin2hex(random_bytes(32));
    $passwordHash = password_hash($password, PASSWORD_DEFAULT);

    if ($existing) {
        $update = db()->prepare(
            'UPDATE users SET password_hash = :password_hash, verification_token = :token, verified_at = NULL, updated_at = NOW() WHERE id = :id'
        );
        $update->execute([
            'password_hash' => $passwordHash,
            'token' => $token,
            'id' => $existing['id'],
        ]);
    } else {
        $insert = db()->prepare(
            'INSERT INTO users (email, password_hash, verification_token, created_at, updated_at) VALUES (:email, :password_hash, :token, NOW(), NOW())'
        );
        $insert->execute([
            'email' => $email,
            'password_hash' => $passwordHash,
            'token' => $token,
        ]);
    }

    $verificationLink = APP_URL . '/verify.php?token=' . urlencode($token);
    $subject = 'Подтверждение регистрации — Академия Криптотрейдинга';
    $message = "Подтвердите регистрацию по ссылке: {$verificationLink}";
    $sent = sendAppMail($email, $subject, $message);

    if (!$sent) {
        flash('error', 'Не удалось отправить письмо подтверждения. Проверьте настройки почты на сервере.');
        redirect('/index.php?auth=register');
    }
} catch (Throwable $e) {
    error_log('Register error: ' . $e->getMessage());
    flash('error', 'Ошибка сервера при регистрации. Проверьте настройки БД и попробуйте снова.');
    redirect('/index.php?auth=register');
}

flash('success', 'Регистрация почти завершена. Проверьте email и подтвердите аккаунт по ссылке.');
redirect('/index.php?auth=register');
